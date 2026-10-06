"""Learning loop: closed-trade attribution -> bounded updates of agent trust weights.

Deliberately slow and bounded so a lucky/unlucky streak can't swing the system:
- needs >= MIN_OBS non-abstaining calls per agent before moving its weight
- target weight = base * (0.5 + hit_rate), clamped to [0.25, 2.0]
- each update moves at most MAX_STEP (20%) toward the target
"""
from __future__ import annotations

from .agents.post_trade import attribution
from .config import Settings
from .journal import Journal

MIN_OBS = 20
MAX_STEP = 0.2
LOOKBACK = 100


def update_agent_weights(journal: Journal, settings: Settings) -> dict[str, float]:
    trades = journal.closed_trades(limit=LOOKBACK)
    tally: dict[str, list[bool]] = {}
    for t in trades:
        for agent, ok in attribution(t).items():
            if ok is not None:
                tally.setdefault(agent, []).append(ok)
    current = {**settings.fusion_weights, **(journal.get_state("agent_weights", {}) or {})}
    new = dict(current)
    for agent, hits in tally.items():
        if len(hits) < MIN_OBS:
            continue
        base = settings.fusion_weights.get(agent, 0.5)
        target = min(2.0, max(0.25, base * (0.5 + sum(hits) / len(hits))))
        cur = current.get(agent, base)
        step = max(-MAX_STEP * cur, min(MAX_STEP * cur, target - cur))
        new[agent] = round(cur + step, 4)
    journal.set_state("agent_weights", new)
    return new
