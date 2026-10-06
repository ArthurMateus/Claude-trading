"""⚠️ Risk Agent — "Can we trade? How much? Where's the stop?" -> APPROVE / REJECT.

The LLM proposes stop distance, target and a risk request; `guardrails.check_trade` then enforces
every hard limit (calibrated sizing, heat cap, cluster cap, daily loss, notional, spread).
The LLM can make the trade smaller or reject it — it can never make it bigger than the guardrails allow.
"""
from __future__ import annotations

from typing import Literal

import pandas as pd
from pydantic import BaseModel

from ..contracts import AssetState, RiskPlan, TradeCandidate
from ..guardrails import PortfolioState, allowed_risk_pct, check_trade
from ..strategies import SETUPS
from .base import Agent


class RiskOut(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    stop_atr_multiple: float
    reward_risk: float
    requested_risk_pct: float
    rationale: str


class RiskAgent(Agent):
    name = "risk"
    job = ("decide whether a trade candidate is acceptable from a risk standpoint and propose the stop distance "
           "(in ATRs), reward:risk target and the % of equity to risk. Be stricter around high event risk, wide "
           "spreads, high volatility regimes, crowded correlated exposure and recent losses. The requested risk "
           "can never exceed `max_allowed_risk_pct`.")

    def evaluate(self, cand: TradeCandidate, state: AssetState, pstate: PortfolioState,
                 corr: pd.DataFrame | None) -> RiskPlan:
        r = self.settings.risk
        closed = self.journal.closed_trades(limit=500)
        allowed, why = allowed_risk_pct(cand.confidence, closed, r, self.settings.calibration)
        setup = SETUPS.get(cand.setup)
        default_mult = setup.stop_atr_mult if setup else 1.5
        default_rr = setup.reward_risk if setup else 2.0
        event_risk = cand.market_conditions.get("news_event_risk")
        payload = {
            "candidate": cand.model_dump(exclude={"signals"}),
            "atr": state.atr, "atr_pct": state.atr_pct, "spread_bps": state.spread_bps, "regime": state.regime,
            "setup_defaults": {"stop_atr_multiple": default_mult, "reward_risk": default_rr},
            "max_allowed_risk_pct": allowed, "calibration_note": why,
            "portfolio": {"equity": pstate.equity, "heat_pct": round(pstate.heat_pct, 3),
                          "daily_pnl_pct": round(pstate.daily_pnl_pct, 3),
                          "open_positions": [t.asset for t in pstate.open_trades]},
            "recent_results": [t.result for t in closed[:10]],
            "limits": r.model_dump(),
        }

        def heuristic() -> RiskOut:
            req = r.base_risk_pct if event_risk == "high" else allowed
            return RiskOut(decision="APPROVE", stop_atr_multiple=default_mult, reward_risk=default_rr,
                           requested_risk_pct=req, rationale=f"setup defaults; {why}")

        out, src = self.ask(payload, RiskOut, heuristic, lambda: None)
        if out is None:
            return RiskPlan(candidate=cand, approved=False, reasons=["risk LLM unavailable"], source="abstain")
        if out.decision == "REJECT":
            return RiskPlan(candidate=cand, approved=False, reasons=[f"risk agent: {out.rationale}"], source=src)
        mult = min(3.0, max(0.8, out.stop_atr_multiple))
        rr = min(4.0, max(r.min_reward_risk, out.reward_risk))
        entry = state.ask if cand.side == "long" else state.bid
        dist = mult * state.atr
        stop = entry - dist if cand.side == "long" else entry + dist
        tp = entry + rr * dist if cand.side == "long" else entry - rr * dist
        return self.apply_guardrails(cand, state, entry, stop, tp, out.requested_risk_pct, pstate, corr,
                                     closed, [out.rationale], src)

    def apply_guardrails(self, cand: TradeCandidate, state: AssetState, entry: float, stop: float, tp: float,
                         requested_risk_pct: float, pstate: PortfolioState, corr, closed=None,
                         notes: list[str] | None = None, source: str = "heuristic") -> RiskPlan:
        closed = closed if closed is not None else self.journal.closed_trades(limit=500)
        g = check_trade(asset=cand.asset, side=cand.side, confidence=cand.confidence, entry=entry, stop=stop,
                        take_profit=tp, spread_bps=state.spread_bps, requested_risk_pct=requested_risk_pct,
                        state=pstate, closed_trades=closed, risk=self.settings.risk,
                        cal=self.settings.calibration, corr=corr)
        dist = abs(entry - stop)
        return RiskPlan(candidate=cand, approved=g.approved, reasons=(notes or []) + g.reasons, risk_pct=g.risk_pct,
                        quantity=g.quantity, entry_price=entry, stop_price=stop, take_profit_price=tp,
                        reward_risk=abs(tp - entry) / dist if dist else 0.0, cluster=",".join(g.cluster), source=source)
