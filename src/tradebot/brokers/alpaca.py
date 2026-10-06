"""Alpaca crypto broker adapter (paper by default).

Alpaca crypto specifics this adapter accounts for:
- spot only, long only, no margin: `allow_short` must stay false
- bracket/OCO orders are not available for crypto, so the protective stop is a resting
  stop_limit order and take-profit / time-stop exits are managed by the bot each cycle
- the trading fee is taken from the asset received, so the held quantity after a buy can be
  slightly below the filled quantity: always size exits from `positions()`
"""
from __future__ import annotations

import os
import time

from ..contracts import Account, OrderRequest, OrderResult
from .base import Broker


def _sym(asset: str) -> str:
    return asset.replace("/", "")


class AlpacaBroker(Broker):
    name = "alpaca"

    def __init__(self, paper: bool = True, fee_bps: float = 25):
        from alpaca.trading.client import TradingClient

        key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY are not set")
        self.paper = paper
        self.fee = fee_bps / 1e4
        self.client = TradingClient(key, secret, paper=paper)

    def account(self) -> Account:
        a = self.client.get_account()
        return Account(equity=float(a.equity), cash=float(a.cash), buying_power=float(a.non_marginable_buying_power or a.cash))

    def positions(self) -> dict[str, float]:
        out = {}
        for p in self.client.get_all_positions():
            sym = p.symbol
            asset = f"{sym[:-3]}/{sym[-3:]}" if "/" not in sym and sym.endswith("USD") else sym
            out[asset] = float(p.qty)
        return out

    def submit(self, req: OrderRequest) -> OrderResult:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, StopLimitOrderRequest

        common = dict(symbol=req.asset, qty=round(req.qty, 9),
                      side=OrderSide.BUY if req.side == "buy" else OrderSide.SELL,
                      time_in_force=TimeInForce.IOC if req.tif == "ioc" else TimeInForce.GTC,
                      client_order_id=req.client_order_id)
        if req.type == "market":
            order = self.client.submit_order(MarketOrderRequest(**common))
        elif req.type == "limit":
            order = self.client.submit_order(LimitOrderRequest(limit_price=round(req.limit_price, 6), **common))
        else:
            order = self.client.submit_order(StopLimitOrderRequest(stop_price=round(req.stop_price, 6),
                                                                   limit_price=round(req.limit_price, 6), **common))
        res = self._to_result(order)
        if req.type != "stop_limit":
            res = self._await_terminal(res.order_id)
        return res

    def _await_terminal(self, order_id: str, timeout_s: float = 10.0) -> OrderResult:
        deadline = time.time() + timeout_s
        res = self.order_status(order_id)
        while res.status in ("new", "accepted", "pending_new", "partially_filled") and time.time() < deadline:
            time.sleep(0.5)
            res = self.order_status(order_id)
        return res

    def _to_result(self, o) -> OrderResult:
        filled = float(o.filled_qty or 0)
        px = float(o.filled_avg_price or 0)
        status = o.status.value if hasattr(o.status, "value") else str(o.status)
        return OrderResult(order_id=str(o.id), status=status, filled_qty=filled, avg_price=px,
                           fee_usd=filled * px * self.fee)   # estimate; exact fees are in account activities

    def cancel(self, order_id: str) -> None:
        try:
            self.client.cancel_order_by_id(order_id)
        except Exception:
            pass   # already filled/canceled

    def order_status(self, order_id: str) -> OrderResult:
        return self._to_result(self.client.get_order_by_id(order_id))
