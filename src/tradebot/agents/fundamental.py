"""🏦 Fundamental Agent — tokenomics, unlocks, on-chain/valuation -> fundamental score.

Disabled by default: Alpaca provides no crypto fundamentals, and on a 1-4h horizon fundamentals mostly
act as a veto (e.g. a large token unlock today) rather than a signal. A data source must be chosen
before enabling it — see docs/OPEN_QUESTIONS.md.
"""
from __future__ import annotations

from ..contracts import AgentSignal, AssetState
from .base import SignalAgent, SignalOut


class FundamentalAgent(SignalAgent):
    name = "fundamental"
    job = ("assess token fundamentals (supply unlocks, emissions, treasury, on-chain activity, valuation vs peers) "
           "and flag anything that should veto a short-term long, such as a large unlock today.")

    def analyze(self, state: AssetState, fundamentals: dict | None = None, **_) -> AgentSignal:
        if not self.settings.agent_enabled(self.name) or not fundamentals:
            return self.abstain(state.asset, "fundamental agent disabled / no data source configured")
        payload = {"asset": state.asset, "fundamentals": fundamentals}
        out, src = self.ask(payload, SignalOut, lambda: SignalOut(direction="flat", score=0, confidence=0,
                                                                  rationale="no heuristic"), lambda: None)
        return self.to_signal(state.asset, out, src)
