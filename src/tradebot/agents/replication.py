"""👀 Strategy-Replication Agent — follows whatever has been working, internally first.

Three components, combined into one replication signal:
1. Hot agents: the system's own agents whose calls have been right most often over the recent closed
   trades (from post-trade attribution). Their current votes are replicated, weighted by the edge they
   showed. This is fast and recent; the fusion weights in learning.py are the slow, bounded version.
2. Setup momentum: how the active setups have performed lately, from the journal's live/paper trades
   when there are enough, else from a recent-window replay on this asset vs its longer baseline.
3. External traders/strategies (optional): actions in `data/replication_feed.json` are followed ONLY from
   sources that earned it: each past action is scored on what the price did afterwards, and a source must
   clear `replication.external_*` thresholds before it counts at all.

The agent runs after the other intelligence agents because it reads their current signals.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

import pandas as pd
from pydantic import BaseModel

from ..backtest import run_setup, stats
from ..config import ROOT
from ..contracts import AgentSignal, AssetState
from ..strategies import SETUPS
from .base import SignalAgent, clamp
from .post_trade import attribution


class ReplicationOut(BaseModel):
    direction: Literal["long", "short", "flat"]
    score: float
    confidence: float
    followed: list[str]          # which agents / setups / sources this call replicates
    rationale: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ReplicationAgent(SignalAgent):
    name = "replication"
    job = ("decide which behavior is worth replicating right now: the system's own agents with the best recent "
           "track record, setups that have been working lately, and external traders/strategies that have earned "
           "trust. Small samples deserve low confidence; follow only demonstrated, recent edge.")

    def __init__(self, ctx, provider=None):
        super().__init__(ctx)
        self.provider = provider
        self.cfg = self.settings.replication

    # ------------------------------------------------------------ 1. internal agents
    def agent_track_records(self) -> dict[str, dict]:
        tally: dict[str, list[bool]] = {}
        for t in self.journal.closed_trades(limit=self.cfg.lookback_trades):
            for agent, ok in attribution(t).items():
                if ok is not None and agent != self.name:
                    tally.setdefault(agent, []).append(ok)
        return {a: {"n": len(h), "hit_rate": sum(h) / len(h)} for a, h in tally.items()}

    def hot_agent_component(self, signals: list[AgentSignal], records: dict[str, dict]) -> tuple[Optional[float], list[str]]:
        num = den = 0.0
        followed = []
        for s in signals:
            r = records.get(s.agent)
            if s.abstain or s.agent == self.name or not r or r["n"] < self.cfg.min_agent_observations:
                continue
            if r["hit_rate"] < self.cfg.hot_hit_rate:
                continue
            w = (r["hit_rate"] - 0.5) * 2
            num += w * s.score * s.confidence
            den += w
            followed.append(f"agent:{s.agent}({r['hit_rate']:.0%}/{r['n']})")
        return (num / den if den else None), followed

    # ------------------------------------------------------------ 2. setup momentum
    def setup_component(self, state: AssetState, history: Optional[pd.DataFrame]) -> tuple[Optional[float], dict]:
        detail: dict = {}
        scores = []
        closed = self.journal.closed_trades(limit=200)
        for name in state.active_setups:
            live = [t for t in closed if t.setup == name][:10]
            if len(live) >= 5:
                avg_r = sum(t.r_multiple or 0 for t in live) / len(live)
                detail[name] = {"live_trades": len(live), "avg_r": round(avg_r, 3)}
                scores.append(clamp(avg_r))
            elif history is not None and len(history) > 1000:
                c = self.settings.costs
                recent = stats(run_setup(history.tail(864), SETUPS[name], c.taker_fee_bps, c.sim_slippage_bps))  # ~3 days
                base = stats(run_setup(history, SETUPS[name], c.taker_fee_bps, c.sim_slippage_bps))
                detail[name] = {"recent_trades": recent.trades, "recent_expectancy_bps": round(recent.expectancy_bps, 1),
                                "baseline_expectancy_bps": round(base.expectancy_bps, 1)}
                if recent.trades >= 3:
                    scores.append(clamp(recent.expectancy_bps / max(20.0, abs(base.expectancy_bps))))
        return (sum(scores) / len(scores) if scores else None), detail

    # ------------------------------------------------------------ 3. external sources (earned trust only)
    def _feed(self) -> list[dict]:
        path = Path(self.cfg.external_feed)
        path = path if path.is_absolute() else ROOT / path
        if not path.exists():
            return []
        try:
            rows = json.loads(path.read_text())
        except Exception:
            return []
        out = []
        for r in rows:
            try:
                r = dict(r)
                r["_ts"] = datetime.fromisoformat(str(r["ts"]).replace("Z", "+00:00"))
                if r.get("action") in ("buy", "sell") and r.get("asset") and r.get("source"):
                    out.append(r)
            except Exception:
                continue
        return out

    def _forward_return(self, action: dict) -> Optional[float]:
        start = action["_ts"]
        end = start + timedelta(minutes=self.cfg.external_horizon_minutes)
        df = self.provider.bars(action["asset"], self.settings.bar_timeframe_minutes, start=start, end=end)
        if df is None or len(df) < 2:
            return None
        ret = float(df["close"].iloc[-1] / df["open"].iloc[0] - 1) * 100
        return ret if action["action"] == "buy" else -ret

    def source_scores(self, refresh: bool = False) -> dict[str, dict]:
        cached = self.journal.get_state("replication_source_scores")
        if cached and not refresh and _now() - datetime.fromisoformat(cached["at"]) < timedelta(hours=1):
            return cached["scores"]
        if self.provider is None:
            return {}
        evaluated: dict = self.journal.get_state("replication_evaluated", {}) or {}
        horizon = timedelta(minutes=self.cfg.external_horizon_minutes)
        by_source: dict[str, list[float]] = {}
        for a in self._feed():
            if _now() - a["_ts"] < horizon or _now() - a["_ts"] > timedelta(days=60):
                continue
            key = hashlib.sha1(f"{a['source']}|{a['asset']}|{a['action']}|{a['ts']}".encode()).hexdigest()[:16]
            if key not in evaluated:
                try:
                    evaluated[key] = self._forward_return(a)
                except Exception:
                    evaluated[key] = None
            if evaluated[key] is not None:
                by_source.setdefault(a["source"], []).append(evaluated[key])
        self.journal.set_state("replication_evaluated", evaluated)
        scores = {}
        for src, rets in by_source.items():
            hit = sum(r > 0 for r in rets) / len(rets)
            avg = sum(rets) / len(rets)
            trusted = (len(rets) >= self.cfg.external_min_actions and hit >= self.cfg.external_min_hit_rate
                       and avg >= self.cfg.external_min_avg_return_pct)
            scores[src] = {"n": len(rets), "hit_rate": round(hit, 3), "avg_return_pct": round(avg, 3), "trusted": trusted}
        self.journal.set_state("replication_source_scores", {"at": _now().isoformat(), "scores": scores})
        return scores

    def external_component(self, asset: str) -> tuple[Optional[float], list[dict]]:
        scores = self.source_scores()
        recent = [a for a in self._feed() if a["asset"] == asset and _now() - a["_ts"] <= timedelta(hours=6)]
        used, num, den = [], 0.0, 0.0
        for a in recent:
            sc = scores.get(a["source"])
            if not sc or not sc["trusted"]:
                continue
            w = sc["hit_rate"] - 0.5
            num += w * (1 if a["action"] == "buy" else -1)
            den += w
            used.append({"source": a["source"], "action": a["action"], "ts": a["ts"], "track": sc})
        return (clamp(num / den) if den else None), used

    # ------------------------------------------------------------ signal
    def analyze(self, state: AssetState, signals: list[AgentSignal] | None = None,
                history: pd.DataFrame | None = None, **_) -> AgentSignal:
        if not self.settings.agent_enabled(self.name):
            return self.abstain(state.asset, "replication agent disabled")
        records = self.agent_track_records() if self.cfg.follow_internal_agents else {}
        agent_score, followed_agents = self.hot_agent_component(signals or [], records)
        setup_score, setup_detail = self.setup_component(state, history)
        ext_score, ext_used = self.external_component(state.asset)
        components = {k: v for k, v in {"hot_agents": agent_score, "setup_momentum": setup_score,
                                         "external": ext_score}.items() if v is not None}
        if not components:
            return self.abstain(state.asset, "no track record to replicate yet")
        payload = {"asset": state.asset, "regime": state.regime, "components": components,
                   "agent_track_records": records, "followed_agents": followed_agents,
                   "current_votes": [s.model_dump(include={"agent", "direction", "score", "confidence"})
                                     for s in (signals or []) if not s.abstain],
                   "setup_momentum": setup_detail, "external_actions_used": ext_used}

        def heuristic() -> ReplicationOut:
            score = clamp(sum(components.values()) / len(components))
            conf = min(0.7, 0.2 + 0.15 * len(components))
            return ReplicationOut(direction="long" if score > 0.1 else "short" if score < -0.1 else "flat",
                                  score=score, confidence=conf,
                                  followed=followed_agents + [f"setup:{s}" for s in setup_detail]
                                  + [f"source:{u['source']}" for u in ext_used],
                                  rationale="; ".join(f"{k} {v:+.2f}" for k, v in components.items()))

        out, src = self.ask(payload, ReplicationOut, heuristic, lambda: None)
        if out is None:
            return self.abstain(state.asset, "LLM unavailable")
        return AgentSignal(agent=self.name, asset=state.asset, direction=out.direction, score=clamp(out.score),
                           confidence=clamp(out.confidence, 0, 1), rationale=out.rationale, source=src,
                           data={"followed": out.followed, "components": components})
