"""In-process broker with a spread + slippage + fee model. Used for `simulated` mode and tests."""
from __future__ import annotations

import itertools

from ..contracts import Account, MarketSnapshot, OrderRequest, OrderResult
from .base import Broker


class SimulatedBroker(Broker):
    name = "simulated"

    def __init__(self, starting_cash: float = 10_000.0, taker_fee_bps: float = 25, slippage_bps: float = 5):
        self.cash = starting_cash
        self.qty: dict[str, float] = {}
        self.last: dict[str, tuple[float, float]] = {}   # asset -> (bid, ask)
        self.fee = taker_fee_bps / 1e4
        self.slip = slippage_bps / 1e4
        self.orders: dict[str, OrderResult] = {}
        self.resting: dict[str, OrderRequest] = {}
        self._ids = itertools.count(1)

    # -- market updates
    def set_quote(self, asset: str, bid: float, ask: float) -> None:
        self.last[asset] = (bid, ask)

    def on_market(self, snapshot: MarketSnapshot) -> None:
        for asset, st in snapshot.assets.items():
            self.set_quote(asset, st.bid, st.ask)
        for oid, req in list(self.resting.items()):
            df = snapshot.bars.get(req.asset)
            if df is None or df.empty or req.type != "stop_limit":
                continue
            bar = df.iloc[-1]
            if req.side == "sell" and bar["low"] <= req.stop_price:
                px = min(float(bar["open"]), req.stop_price) * (1 - self.slip)
                self._fill(oid, req, px)
                del self.resting[oid]

    # -- broker API
    def account(self) -> Account:
        equity = self.cash + sum(q * self.last.get(a, (0, 0))[0] for a, q in self.qty.items())
        return Account(equity=equity, cash=self.cash, buying_power=self.cash)

    def positions(self) -> dict[str, float]:
        return {a: q for a, q in self.qty.items() if q > 1e-12}

    def submit(self, req: OrderRequest) -> OrderResult:
        oid = f"sim-{next(self._ids)}"
        bid, ask = self.last.get(req.asset, (0.0, 0.0))
        if ask <= 0:
            res = OrderResult(order_id=oid, status="rejected")
        elif req.type == "stop_limit":
            self.resting[oid] = req
            res = OrderResult(order_id=oid, status="new")
        elif req.side == "buy":
            px = ask * (1 + self.slip)
            if req.type == "limit" and req.limit_price is not None and px > req.limit_price:
                res = OrderResult(order_id=oid, status="canceled")      # IOC limit not marketable
            elif px * req.qty * (1 + self.fee) > self.cash + 1e-9:
                res = OrderResult(order_id=oid, status="rejected")
            else:
                self.orders[oid] = OrderResult(order_id=oid, status="new")
                return self._fill(oid, req, px)
        else:
            px = bid * (1 - self.slip)
            if req.type == "limit" and req.limit_price is not None and px < req.limit_price:
                res = OrderResult(order_id=oid, status="canceled")
            else:
                return self._fill(oid, req, px)
        self.orders[oid] = res
        return res

    def _fill(self, oid: str, req: OrderRequest, px: float) -> OrderResult:
        qty = req.qty if req.side == "buy" else min(req.qty, self.qty.get(req.asset, 0.0))
        notional = qty * px
        fee = notional * self.fee
        if req.side == "buy":
            self.cash -= notional + fee
            self.qty[req.asset] = self.qty.get(req.asset, 0.0) + qty
        else:
            self.cash += notional - fee
            self.qty[req.asset] = self.qty.get(req.asset, 0.0) - qty
        res = OrderResult(order_id=oid, status="filled", filled_qty=qty, avg_price=px, fee_usd=fee)
        self.orders[oid] = res
        return res

    def cancel(self, order_id: str) -> None:
        self.resting.pop(order_id, None)
        if order_id in self.orders and self.orders[order_id].status == "new":
            self.orders[order_id] = self.orders[order_id].model_copy(update={"status": "canceled"})

    def order_status(self, order_id: str) -> OrderResult:
        if order_id in self.resting:
            return OrderResult(order_id=order_id, status="new")
        return self.orders.get(order_id, OrderResult(order_id=order_id, status="unknown"))
