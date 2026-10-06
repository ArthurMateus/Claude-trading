"""⚡ Execution Agent — turns an approved plan into broker orders, and closes positions.

Entries use a marketable IOC limit (ask + capped slippage) so a thin book can't fill us far away; every
filled entry immediately gets a resting protective stop at the broker, so a crash of this process still
leaves the stop live. Take-profit and time-stop exits are managed by the pipeline each cycle.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel

from ..brokers.base import Broker
from ..contracts import AssetState, OrderRequest, RiskPlan, TradeRecord, utcnow
from .base import Agent


class ExecOut(BaseModel):
    proceed: bool
    max_slippage_bps: float
    rationale: str


class ExecutionAgent(Agent):
    name = "execution"
    job = ("decide whether to send an approved entry order right now and how much slippage above the ask to "
           "tolerate, given spread, depth imbalance and volatility. Skip if the book looks thin or dislocated.")

    def __init__(self, ctx, broker: Broker):
        super().__init__(ctx)
        self.broker = broker

    def enter(self, plan: RiskPlan, state: AssetState) -> TradeRecord | None:
        cap = self.settings.risk.max_entry_slippage_bps
        cand = plan.candidate
        payload = {"asset": cand.asset, "side": cand.side, "qty": plan.quantity, "bid": state.bid, "ask": state.ask,
                   "spread_bps": state.spread_bps, "book_imbalance": state.book_imbalance,
                   "realized_vol_pct": state.realized_vol_pct, "slippage_cap_bps": cap}
        out, _ = self.ask(payload, ExecOut,
                          lambda: ExecOut(proceed=True, max_slippage_bps=min(cap, 10), rationale="default"),
                          lambda: None)
        if out is None or not out.proceed:
            self.journal.log_decision(cand.asset, "execution", "SKIP", {"why": out.rationale if out else "LLM unavailable"})
            return None
        slip = min(cap, max(0.0, out.max_slippage_bps))
        trade_id = f"T{utcnow():%Y%m%d%H%M%S}-{cand.asset.replace('/', '')}-{uuid.uuid4().hex[:6]}"
        side = "buy" if cand.side == "long" else "sell"
        limit = state.ask * (1 + slip / 1e4) if side == "buy" else state.bid * (1 - slip / 1e4)
        req = OrderRequest(asset=cand.asset, side=side, qty=plan.quantity, type="limit", limit_price=limit,
                           tif="ioc", client_order_id=f"{trade_id}-in")
        self.journal.log_event("order", f"entry {cand.asset} {side} {plan.quantity:.6f} @<= {limit:.6f}")
        try:
            res = self.broker.submit(req)
        except Exception as e:
            self.journal.log_event("api_error", f"entry submit {cand.asset}: {e}", "ERROR")
            return None
        if res.status not in ("filled", "partially_filled") or res.filled_qty <= 0:
            self.journal.log_decision(cand.asset, "execution", "NO_FILL", {"status": res.status})
            return None
        held = self.broker.positions().get(cand.asset, res.filled_qty)
        qty = min(res.filled_qty, held) if held > 0 else res.filled_qty
        fill = res.avg_price
        # Keep the planned stop/target distances, re-anchored on the actual fill.
        dist = abs(plan.entry_price - plan.stop_price)
        reward = abs(plan.take_profit_price - plan.entry_price)
        stop = fill - dist if cand.side == "long" else fill + dist
        tp = fill + reward if cand.side == "long" else fill - reward
        stop_id = ""
        try:
            stop_res = self.broker.submit(OrderRequest(
                asset=cand.asset, side="sell" if side == "buy" else "buy", qty=qty, type="stop_limit",
                stop_price=stop, limit_price=stop * (0.995 if side == "buy" else 1.005), tif="gtc",
                client_order_id=f"{trade_id}-stop"))
            stop_id = stop_res.order_id
        except Exception as e:   # no protective stop => close immediately, never hold naked
            self.journal.log_event("api_error", f"stop submit failed {cand.asset}: {e}; flattening", "ERROR")
            self.broker.submit(OrderRequest(asset=cand.asset, side="sell" if side == "buy" else "buy", qty=qty,
                                            type="market", tif="gtc"))
            return None
        sign = 1 if cand.side == "long" else -1
        rec = TradeRecord(
            trade_id=trade_id, timestamp=utcnow(), asset=cand.asset, side=cand.side, setup=cand.setup,
            mode=self.settings.mode, entry=fill, position_size=qty, notional_usd=qty * fill,
            risk_pct=qty * dist / max(1e-9, self.broker.account().equity) * 100, risk_usd=qty * dist,
            stop_price=stop, take_profit_price=tp, decision_price=cand.decision_price,
            agents_agreed=cand.agents_agreed, agents_disagreed=cand.agents_disagreed,
            agent_signals=[s.model_dump(exclude={"data"}) for s in cand.signals],
            confidence=cand.confidence, reason=cand.reason, expected_return_pct=cand.expected_return_pct,
            fees_usd=res.fee_usd, market_conditions=cand.market_conditions | {"invalidation": cand.invalidation},
            entry_slippage_bps=sign * (fill - cand.decision_price) / cand.decision_price * 1e4,
            entry_order_id=res.order_id, stop_order_id=stop_id,
        )
        self.journal.open_trade(rec)
        return rec

    def exit(self, trade: TradeRecord, reason: str, reference_price: float) -> TradeRecord:
        if trade.stop_order_id:
            self.broker.cancel(trade.stop_order_id)
        held = self.broker.positions().get(trade.asset, 0.0)
        qty = min(trade.position_size, held)
        side = "sell" if trade.side == "long" else "buy"
        if qty <= 0:
            self.journal.log_event("reconcile", f"{trade.asset} position missing at exit", "WARN")
            return self.journal.close_trade(trade.trade_id, reference_price, f"{reason}|position_missing", 0.0, reference_price)
        self.journal.log_event("order", f"exit {trade.asset} {side} {qty:.6f} ({reason})")
        res = self.broker.submit(OrderRequest(asset=trade.asset, side=side, qty=qty, type="market", tif="gtc",
                                              client_order_id=f"{trade.trade_id}-out"))
        px = res.avg_price if res.filled_qty > 0 else reference_price
        return self.journal.close_trade(trade.trade_id, px, reason, res.fee_usd, reference_price)
