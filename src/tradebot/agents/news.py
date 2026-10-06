"""📰 News Agent — headlines/announcements -> sentiment score + event-risk flag."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..contracts import AgentSignal, AssetState, NewsItem
from .base import SignalAgent, clamp

_POS = ("approval", "approved", "etf inflow", "partnership", "upgrade", "launch", "record high", "adoption", "buyback")
_NEG = ("hack", "exploit", "lawsuit", "sec sues", "ban", "delist", "outage", "bankrupt", "investigation", "liquidation")


class NewsOut(BaseModel):
    sentiment: float                                  # -1..1
    confidence: float
    event_risk: Literal["none", "low", "high"]        # high = scheduled/unscheduled event that can gap price
    key_events: list[str]
    rationale: str


class NewsAgent(SignalAgent):
    name = "news"
    job = ("score the net sentiment of recent headlines for one crypto asset and flag event risk "
           "(hacks, regulation, listings, macro releases) that could gap the price within hours. "
           "Old or irrelevant news should not move the score.")

    def analyze(self, state: AssetState, news: list[NewsItem] | None = None, **_) -> AgentSignal:
        news = news or []
        if not news:
            return AgentSignal(agent=self.name, asset=state.asset, direction="flat", score=0.0, confidence=0.2,
                               rationale="no recent news", source="heuristic", data={"event_risk": "none"})
        payload = {"asset": state.asset, "now": state.ts.isoformat(),
                   "headlines": [{"ts": n.ts.isoformat(), "headline": n.headline, "summary": n.summary[:300],
                                  "source": n.source} for n in news[:20]]}

        def heuristic() -> NewsOut:
            text = " ".join((n.headline + " " + n.summary).lower() for n in news)
            pos, neg = sum(w in text for w in _POS), sum(w in text for w in _NEG)
            s = clamp((pos - neg) / 3)
            return NewsOut(sentiment=s, confidence=0.3, event_risk="high" if neg >= 2 else "low" if neg else "none",
                           key_events=[n.headline for n in news[:3]], rationale=f"keyword +{pos}/-{neg}")

        out, src = self.ask(payload, NewsOut, heuristic, lambda: None)
        if out is None:
            return self.abstain(state.asset, "LLM unavailable")
        s = clamp(out.sentiment)
        return AgentSignal(agent=self.name, asset=state.asset, direction="long" if s > 0.15 else "short" if s < -0.15 else "flat",
                           score=s, confidence=clamp(out.confidence, 0, 1), rationale=out.rationale, source=src,
                           data={"event_risk": out.event_risk, "key_events": out.key_events})
