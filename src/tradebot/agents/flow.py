"""🐋 Flow Agent — order-book imbalance, volume spikes, liquidity -> flow signal.

Crypto spot has no options-flow feed on Alpaca; this agent uses order-book depth imbalance, volume
z-scores and spread. Deribit options / exchange whale-flow feeds are open questions (docs/OPEN_QUESTIONS.md).
"""
from __future__ import annotations

from ..contracts import AgentSignal, AssetState
from .base import SignalAgent, SignalOut, clamp


class FlowAgent(SignalAgent):
    name = "flow"
    job = ("read order-book imbalance, volume anomalies and liquidity for one asset and say whether "
           "aggressive buying or selling pressure supports a move in the next hour. Wide spreads or thin books "
           "mean low confidence.")

    def analyze(self, state: AssetState, **_) -> AgentSignal:
        payload = state.model_dump(include={"asset", "book_imbalance", "volume_z", "spread_bps", "ret_1h_pct",
                                            "realized_vol_pct", "regime"})

        def heuristic() -> SignalOut:
            vol_push = clamp(state.volume_z / 3) * (1 if state.ret_1h_pct >= 0 else -1)
            score = clamp(0.5 * state.book_imbalance + 0.5 * vol_push)
            conf = 0.35 if state.spread_bps < 10 else 0.2
            return SignalOut(direction="long" if score > 0.15 else "short" if score < -0.15 else "flat",
                             score=score, confidence=conf,
                             rationale=f"imbalance {state.book_imbalance:+.2f}, volume z {state.volume_z:.1f}")

        out, src = self.ask(payload, SignalOut, heuristic, lambda: None)
        return self.to_signal(state.asset, out, src)
