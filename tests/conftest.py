from __future__ import annotations

from datetime import timedelta

import pytest

from tradebot.config import Settings, load_settings
from tradebot.contracts import TradeRecord, utcnow
from tradebot.journal import Journal


@pytest.fixture
def settings() -> Settings:
    s = load_settings()
    s.mode = "simulated"
    s.broker = "simulated"
    s.data_provider = "synthetic"
    s.journal_path = ":memory:"
    return s


@pytest.fixture
def journal() -> Journal:
    return Journal(":memory:")


def make_trade(i: int = 0, *, asset: str = "BTC/USD", confidence: float = 0.6, result: str | None = None,
               r_multiple: float | None = None, risk_usd: float = 100.0, status: str = "OPEN") -> TradeRecord:
    now = utcnow() - timedelta(hours=i)
    rec = TradeRecord(
        trade_id=f"t{i}-{asset}", timestamp=now, asset=asset, side="long", setup="trend_pullback", mode="paper",
        entry=100.0, position_size=10.0, notional_usd=1000.0, risk_pct=1.0, risk_usd=risk_usd,
        stop_price=90.0, take_profit_price=120.0, decision_price=100.0, agents_agreed=["technical"],
        agents_disagreed=[], agent_signals=[{"agent": "technical", "direction": "long", "abstain": False}],
        confidence=confidence, reason="test", expected_return_pct=1.0, market_conditions={"regime": "trend_up"},
    )
    if status == "CLOSED":
        rec = rec.model_copy(update={"status": "CLOSED", "result": result, "r_multiple": r_multiple,
                                     "closed_at": now + timedelta(minutes=30), "pnl_usd": (r_multiple or 0) * risk_usd})
    return rec
