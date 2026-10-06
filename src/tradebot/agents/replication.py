"""👀 Strategy-Replication Agent — watches selected traders/strategies and reads their behavior.

Input is a feed of observed actions (`data/replication_feed.json`): one entry per observed action, e.g.
{"source": "wallet:0xabc / trader:@name / strategy:turtle", "asset": "ETH/USD", "action": "buy",
 "ts": "2026-10-06T12:00:00Z", "size_usd": 250000, "note": "..."}.
Which sources to follow is an open question; until configured the agent abstains.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import ROOT
from ..contracts import AgentSignal, AssetState
from .base import SignalAgent, SignalOut, clamp

FEED = ROOT / "data" / "replication_feed.json"


class ReplicationAgent(SignalAgent):
    name = "replication"
    job = ("read recent actions by the tracked traders/strategies for one asset, judge whether they are "
           "informative (size, track record, recency, whether they are entering or exiting) and turn them into "
           "a replication signal. Ignore stale actions (>6h).")

    def recent_actions(self, asset: str, hours: float = 6) -> list[dict]:
        if not FEED.exists():
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        try:
            rows = json.loads(Path(FEED).read_text())
        except Exception:
            return []
        out = []
        for r in rows:
            try:
                ts = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
            except Exception:
                continue
            if r.get("asset") == asset and ts >= cutoff:
                out.append(r)
        return out

    def analyze(self, state: AssetState, **_) -> AgentSignal:
        if not self.settings.agent_enabled(self.name):
            return self.abstain(state.asset, "replication agent disabled / no sources selected")
        actions = self.recent_actions(state.asset)
        if not actions:
            return self.abstain(state.asset, "no recent tracked actions")

        def heuristic() -> SignalOut:
            net = sum(1 if a.get("action") == "buy" else -1 for a in actions)
            s = clamp(net / 3)
            return SignalOut(direction="long" if s > 0 else "short" if s < 0 else "flat", score=s,
                             confidence=0.3, rationale=f"net {net:+d} tracked actions")

        out, src = self.ask({"asset": state.asset, "actions": actions[:30]}, SignalOut, heuristic, lambda: None)
        return self.to_signal(state.asset, out, src, data={"n_actions": len(actions)})
