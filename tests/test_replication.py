"""Strategy-Replication Agent: follows hot internal agents and only earned external sources."""
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from tradebot.agents.base import AgentContext
from tradebot.agents.replication import ReplicationAgent
from tradebot.contracts import AgentSignal, AssetState
from tradebot.llm import LLMClient

from .conftest import make_trade

NOW = datetime.now(timezone.utc)


def state(asset="ETH/USD", setups=()):
    return AssetState(asset=asset, ts=NOW, last=100, bid=99.9, ask=100.1, spread_bps=20, atr=1, atr_pct=1,
                      realized_vol_pct=0.5, ret_1h_pct=0.2, ret_24h_pct=1, volume_z=0.5, book_imbalance=0.1,
                      active_setups=list(setups))


def agent(settings, journal, provider=None):
    return ReplicationAgent(AgentContext(settings, LLMClient(settings.llm, journal, offline=True), journal), provider)


def closed_with_votes(journal, n, technical_right_rate, flow_right_rate):
    """Closed winning trades where technical/flow voted long (right) or short (wrong) at the given rates."""
    for i in range(n):
        votes = [
            {"agent": "technical", "direction": "long" if i < n * technical_right_rate else "short", "abstain": False},
            {"agent": "flow", "direction": "long" if i < n * flow_right_rate else "short", "abstain": False},
        ]
        t = make_trade(i, asset=f"X{i}/USD", result="WIN", r_multiple=1.0, status="CLOSED")
        journal.open_trade(t.model_copy(update={"agent_signals": votes}))


def test_follows_hot_agents_and_ignores_cold_ones(settings, journal):
    closed_with_votes(journal, 20, technical_right_rate=0.8, flow_right_rate=0.3)
    rep = agent(settings, journal)
    votes = [AgentSignal(agent="technical", asset="ETH/USD", direction="long", score=0.8, confidence=0.7),
             AgentSignal(agent="flow", asset="ETH/USD", direction="short", score=-0.9, confidence=0.9)]
    sig = rep.analyze(state(), signals=votes)
    assert sig.direction == "long" and sig.score > 0
    assert any("technical" in f for f in sig.data["followed"])
    assert not any("flow" in f for f in sig.data["followed"])


def test_abstains_without_any_track_record(settings, journal):
    sig = agent(settings, journal).analyze(state(), signals=[])
    assert sig.abstain


class FwdProvider:
    """Price rises after `good` source's buys, falls after `bad` source's buys."""

    def __init__(self, moves):
        self.moves = moves   # ts iso -> % move over the horizon

    def bars(self, asset, tf, limit=None, start=None, end=None):
        move = self.moves[start.isoformat()]
        idx = pd.date_range(start, end, freq=f"{tf}min", tz="UTC")
        c = 100 * (1 + np.linspace(0, move / 100, len(idx)))
        return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0}, index=idx)


def test_external_sources_must_earn_trust(settings, journal, tmp_path):
    feed, moves = [], {}
    for i in range(25):
        for src, move in (("trader:good", 1.5), ("trader:bad", -1.0)):
            ts = (NOW - timedelta(hours=5 + i * 3, minutes=1 if src.endswith("bad") else 0)).replace(microsecond=0)
            feed.append({"source": src, "asset": "BTC/USD", "action": "buy", "ts": ts.isoformat()})
            moves[ts.isoformat()] = move
    recent = (NOW - timedelta(minutes=30)).replace(microsecond=0).isoformat()
    feed += [{"source": "trader:good", "asset": "ETH/USD", "action": "buy", "ts": recent},
             {"source": "trader:bad", "asset": "ETH/USD", "action": "sell", "ts": recent}]
    path = tmp_path / "feed.json"
    path.write_text(json.dumps(feed))
    settings.replication.external_feed = str(path)
    rep = agent(settings, journal, FwdProvider(moves))

    scores = rep.source_scores(refresh=True)
    assert scores["trader:good"]["trusted"] and not scores["trader:bad"]["trusted"]
    score, used = rep.external_component("ETH/USD")
    assert score > 0 and [u["source"] for u in used] == ["trader:good"]
