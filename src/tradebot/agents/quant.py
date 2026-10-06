"""📈 Quant Agent — statistical edge -> probability + expected return.

The numbers are computed in code: for each active setup, the setup is replayed on this asset's recent
history (after fees) to get an empirical P(win) and expected return. The LLM judges whether that
history is relevant to the current regime and adjusts confidence — it never invents the statistics.
"""
from __future__ import annotations

from typing import Literal

import pandas as pd
from pydantic import BaseModel

from ..backtest import run_setup, stats
from ..contracts import AgentSignal, AssetState
from ..strategies import SETUPS
from .base import SignalAgent, clamp


class QuantOut(BaseModel):
    direction: Literal["long", "short", "flat"]
    score: float
    confidence: float
    regime_relevance: float     # 0..1, how comparable recent history is to now
    rationale: str


class QuantAgent(SignalAgent):
    name = "quant"
    job = ("judge whether empirical setup statistics from recent history apply to the current regime, "
           "and turn them into a calibrated directional signal.")

    def setup_stats(self, bars: pd.DataFrame) -> dict[str, dict]:
        c = self.settings.costs
        out = {}
        for name in SETUPS:
            trades = run_setup(bars, SETUPS[name], c.taker_fee_bps, c.sim_slippage_bps)
            st = stats(trades)
            out[name] = {"trades": st.trades, "win_rate": st.win_rate, "expectancy_bps": st.expectancy_bps,
                         "profit_factor": st.profit_factor if st.profit_factor != float("inf") else 99.0}
        return out

    def analyze(self, state: AssetState, history: pd.DataFrame | None = None, **_) -> AgentSignal:
        if history is None or len(history) < 500 or not state.active_setups:
            return self.abstain(state.asset, "no active setup or insufficient history")
        all_stats = self.setup_stats(history)
        active = {n: all_stats[n] for n in state.active_setups}
        best = max(active.items(), key=lambda kv: kv[1]["expectancy_bps"])
        name, st = best
        p_win = st["win_rate"] if st["trades"] >= 10 else 0.5
        exp_ret = st["expectancy_bps"] / 100 if st["trades"] >= 10 else 0.0
        payload = {"asset": state.asset, "regime": state.regime, "atr_pct": state.atr_pct,
                   "active_setup_stats": active, "best_setup": name}

        def heuristic() -> QuantOut:
            score = clamp(exp_ret / 0.5) if st["trades"] >= 10 else 0.0
            return QuantOut(direction="long" if score > 0 else "flat", score=score,
                            confidence=p_win if score > 0 else 0.3, regime_relevance=0.5,
                            rationale=f"{name}: {st['trades']} trades, win {p_win:.2f}, E {st['expectancy_bps']:.1f}bps")

        out, src = self.ask(payload, QuantOut, heuristic, lambda: None)
        if out is None:
            return self.abstain(state.asset, "LLM unavailable")
        direction = out.direction
        return AgentSignal(agent=self.name, asset=state.asset, direction=direction, score=clamp(out.score),
                           confidence=clamp(out.confidence, 0, 1), rationale=out.rationale, source=src,
                           probability=p_win, expected_return_pct=exp_ret,
                           data={"setup_stats": active, "best_setup": name, "regime_relevance": out.regime_relevance})
