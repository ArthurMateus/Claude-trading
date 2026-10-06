"""📊 Technical Agent — price action, indicators, patterns -> technical signal."""
from __future__ import annotations

import pandas as pd

from ..contracts import AgentSignal, AssetState
from ..indicators import feature_row
from ..strategies import SETUPS
from .base import SignalAgent, SignalOut, clamp


class TechnicalAgent(SignalAgent):
    name = "technical"
    job = ("read indicator values and active rule-based setups for one asset and give a directional "
           "technical signal for the next 1-4 hours.")

    def analyze(self, state: AssetState, bars: pd.DataFrame | None = None, **_) -> AgentSignal:
        if bars is None or len(bars) < 60:
            return self.abstain(state.asset, "not enough bars")
        f = feature_row(bars, self.settings.bar_timeframe_minutes)
        payload = {"asset": state.asset, "regime": state.regime, "features": f,
                   "active_setups": {n: SETUPS[n].description for n in state.active_setups}}

        def heuristic() -> SignalOut:
            trend = 1 if f["ema20"] > f["ema50"] else -1
            score = 0.4 * trend + 0.3 * clamp((50 - f["rsi14"]) / 25) * (1 if state.regime == "range" else -0.3)
            score += 0.3 * (1 if state.active_setups else 0)
            score = clamp(score)
            return SignalOut(direction="long" if score > 0.1 else "short" if score < -0.1 else "flat",
                             score=score, confidence=min(0.8, 0.4 + abs(score) / 2),
                             rationale=f"EMA trend {trend:+d}, RSI {f['rsi14']:.0f}, setups {state.active_setups}")

        out, src = self.ask(payload, SignalOut, heuristic, lambda: None)
        return self.to_signal(state.asset, out, src, data={"features": f})
