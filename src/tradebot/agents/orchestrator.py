"""🧠 Orchestrator — Signal Fusion: "Should we trade?" -> final trade candidate (Opus 5.5).

Cost gate: Opus is only consulted when (a) a validated setup is active on the asset and (b) the
weighted vote of the cheap agents clears `llm.prefilter_min_abs_score`.
Who agreed / disagreed is computed in code from the signals, not taken from the LLM.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from ..contracts import AgentSignal, AssetState, TradeCandidate
from .base import Agent, clamp


class FusionOut(BaseModel):
    decision: Literal["enter_long", "enter_short", "no_trade"]
    setup: str
    confidence: float           # P(take-profit before stop)
    expected_return_pct: float  # net of fees, per trade
    max_hold_minutes: int
    reason: str
    invalidation: str           # what observation would prove the thesis wrong


class Orchestrator(Agent):
    name = "orchestrator"
    job = ("fuse the specialist agents' signals into one decision for one asset. Only trade a listed validated "
           "setup. Weigh agents by their track-record weights, respect disagreement, and prefer no_trade when the "
           "evidence is mixed. confidence must be your probability that take-profit is hit before the stop; it is "
           "audited against real outcomes and over-confidence reduces future position sizes.")

    def weights(self) -> dict[str, float]:
        learned = self.journal.get_state("agent_weights", {}) or {}
        return {**self.settings.fusion_weights, **learned}

    def fused_score(self, signals: list[AgentSignal]) -> float:
        w = self.weights()
        num = den = 0.0
        for s in signals:
            if s.abstain:
                continue
            wi = w.get(s.agent, 0.5)
            num += wi * s.score * s.confidence
            den += wi
        return num / den if den else 0.0

    def fuse(self, state: AssetState, signals: list[AgentSignal], tradeable_setups: dict[str, dict],
             open_positions: list[str]) -> tuple[Optional[TradeCandidate], str]:
        if not tradeable_setups:
            return None, "no validated setup active"
        score = self.fused_score(signals)
        allow_short = self.settings.risk.allow_short
        if score < self.settings.llm.prefilter_min_abs_score and not (allow_short and score < -self.settings.llm.prefilter_min_abs_score):
            return None, f"prefilter: fused score {score:+.2f} too weak"
        news = next((s for s in signals if s.agent == "news"), None)
        quant = next((s for s in signals if s.agent == "quant" and not s.abstain), None)
        best_setup = max(tradeable_setups, key=lambda k: tradeable_setups[k].get("expectancy_bps", 0))

        payload = {
            "asset": state.asset,
            "market_state": state.model_dump(exclude={"ts"}),
            "signals": [s.model_dump(exclude={"data"}) | {"weight": self.weights().get(s.agent, 0.5)} for s in signals],
            "quant_detail": quant.data if quant else None,
            "news_event_risk": news.data.get("event_risk") if news else None,
            "validated_setups": tradeable_setups,
            "fused_score": round(score, 3),
            "open_positions": open_positions,
            "recent_lessons": self.journal.get_state("recent_lessons", [])[-5:],
            "allow_short": allow_short,
            "max_hold_minutes": self.settings.max_hold_minutes,
        }

        def heuristic() -> FusionOut:
            base = quant.probability if quant and quant.probability else 0.5
            conf = clamp(base + 0.2 * score, 0.0, 0.9)
            exp = quant.expected_return_pct if quant and quant.expected_return_pct is not None else \
                tradeable_setups[best_setup].get("expectancy_bps", 0) / 100
            return FusionOut(decision="enter_long" if score > 0 else "enter_short", setup=best_setup,
                             confidence=conf, expected_return_pct=exp, max_hold_minutes=self.settings.max_hold_minutes,
                             reason=f"weighted vote {score:+.2f}; {best_setup} validated",
                             invalidation="price closes below stop / setup trigger level")

        out, src = self.ask(payload, FusionOut, heuristic, lambda: None)
        if out is None:
            return None, "orchestrator LLM unavailable"
        if out.decision == "no_trade":
            return None, f"orchestrator: {out.reason}"
        side = "long" if out.decision == "enter_long" else "short"
        if side == "short" and not allow_short:
            return None, "orchestrator wanted short; shorting disabled"
        setup = out.setup if out.setup in tradeable_setups else best_setup
        agreed = [s.agent for s in signals if not s.abstain and s.direction == side]
        disagreed = [s.agent for s in signals if not s.abstain and s.direction not in (side, "flat")]
        mid = (state.bid + state.ask) / 2
        cand = TradeCandidate(
            asset=state.asset, side=side, setup=setup, confidence=clamp(out.confidence, 0, 1), reason=out.reason,
            invalidation=out.invalidation, expected_return_pct=out.expected_return_pct,
            max_hold_minutes=max(5, min(out.max_hold_minutes, self.settings.max_hold_minutes)),
            decision_price=mid, fused_score=score, agents_agreed=agreed, agents_disagreed=disagreed,
            signals=signals, source=src,
            market_conditions={
                "regime": state.regime, "atr_pct": round(state.atr_pct, 4), "realized_vol_pct": round(state.realized_vol_pct, 4),
                "spread_bps": round(state.spread_bps, 2), "ret_1h_pct": round(state.ret_1h_pct, 3),
                "ret_24h_pct": round(state.ret_24h_pct, 3), "volume_z": round(state.volume_z, 2),
                "book_imbalance": round(state.book_imbalance, 3),
                "news_event_risk": news.data.get("event_risk") if news else None,
            },
        )
        return cand, "candidate"
