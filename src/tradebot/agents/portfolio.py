"""🎯 Portfolio Agent — decides which approved trades to take and in what order -> target positions.

It can only choose a subset of risk-approved plans; every pick is re-checked by the guardrails against the
updated portfolio before execution.
"""
from __future__ import annotations

from pydantic import BaseModel

from ..contracts import RiskPlan
from ..guardrails import PortfolioState
from .base import Agent


class PortfolioOut(BaseModel):
    selected_assets: list[str]   # in execution priority order
    rationale: str


def expected_value(p: RiskPlan) -> float:
    c = p.candidate.confidence
    return c * p.reward_risk - (1 - c)    # in R units


class PortfolioAgent(Agent):
    name = "portfolio"
    job = ("choose which risk-approved trade plans to execute now and in what order, avoiding concentration in "
           "correlated assets and preferring the highest expected value in R. You may select none.")

    def select(self, plans: list[RiskPlan], pstate: PortfolioState) -> list[RiskPlan]:
        if not plans:
            return []
        ranked = sorted(plans, key=expected_value, reverse=True)
        payload = {
            "open_positions": [{"asset": t.asset, "risk_usd": t.risk_usd} for t in pstate.open_trades],
            "heat_pct": pstate.heat_pct, "equity": pstate.equity,
            "plans": [{"asset": p.candidate.asset, "setup": p.candidate.setup, "confidence": p.candidate.confidence,
                       "reward_risk": round(p.reward_risk, 2), "risk_pct": round(p.risk_pct, 3),
                       "cluster": p.cluster, "ev_r": round(expected_value(p), 3)} for p in ranked],
        }
        out, _ = self.ask(payload, PortfolioOut,
                          lambda: PortfolioOut(selected_assets=[p.candidate.asset for p in ranked if expected_value(p) > 0],
                                               rationale="positive-EV plans by EV"),
                          lambda: None)
        if out is None:
            return []
        by_asset = {p.candidate.asset: p for p in ranked}
        return [by_asset[a] for a in out.selected_assets if a in by_asset]
