"""Typed messages passed between agents. Every hand-off in the pipeline is one of these."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional

import pandas as pd
from pydantic import BaseModel, Field

Direction = Literal["long", "short", "flat"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- market data

class OrderBookTop(BaseModel):
    bid: float
    ask: float
    bid_size: float = 0.0
    ask_size: float = 0.0
    imbalance: float = 0.0  # (bid depth - ask depth) / total depth over top levels, in [-1, 1]


class NewsItem(BaseModel):
    ts: datetime
    headline: str
    summary: str = ""
    symbols: list[str] = Field(default_factory=list)
    source: str = ""


class AssetState(BaseModel):
    """Market Data Agent output for one asset: the 'clean market state'."""
    asset: str
    ts: datetime
    last: float
    bid: float
    ask: float
    spread_bps: float
    atr: float
    atr_pct: float
    realized_vol_pct: float
    ret_1h_pct: float
    ret_24h_pct: float
    volume_z: float
    book_imbalance: float
    regime: str = "unknown"          # trend_up | trend_down | range | volatile | unknown
    data_ok: bool = True
    data_issues: list[str] = Field(default_factory=list)
    active_setups: list[str] = Field(default_factory=list)


@dataclass
class MarketSnapshot:
    ts: datetime
    assets: dict[str, AssetState]
    bars: dict[str, pd.DataFrame] = field(default_factory=dict)
    news: dict[str, list[NewsItem]] = field(default_factory=dict)
    correlations: Optional[pd.DataFrame] = None


# ---------------------------------------------------------------- intelligence layer

class AgentSignal(BaseModel):
    agent: str
    asset: str
    direction: Direction = "flat"
    score: float = 0.0                # -1 (strong short) .. +1 (strong long)
    confidence: float = 0.0           # 0..1, how sure the agent is of its own read
    rationale: str = ""
    probability: Optional[float] = None       # Quant: P(target before stop)
    expected_return_pct: Optional[float] = None
    abstain: bool = False
    source: str = "heuristic"         # llm | heuristic | abstain
    data: dict[str, Any] = Field(default_factory=dict)


class TradeCandidate(BaseModel):
    """Orchestrator output: the final trade candidate."""
    asset: str
    side: Literal["long", "short"]
    setup: str
    confidence: float                 # calibrated meaning: P(take-profit hit before stop)
    reason: str
    invalidation: str = ""
    expected_return_pct: float
    max_hold_minutes: int
    decision_price: float
    fused_score: float
    agents_agreed: list[str]
    agents_disagreed: list[str]
    signals: list[AgentSignal]
    market_conditions: dict[str, Any]
    source: str = "heuristic"


class RiskPlan(BaseModel):
    """Risk Agent output: APPROVE / REJECT with size and stops."""
    candidate: TradeCandidate
    approved: bool
    reasons: list[str] = Field(default_factory=list)
    risk_pct: float = 0.0
    quantity: float = 0.0
    entry_price: float = 0.0
    stop_price: float = 0.0
    take_profit_price: float = 0.0
    reward_risk: float = 0.0
    cluster: str = ""
    source: str = "heuristic"


# ---------------------------------------------------------------- execution

class OrderRequest(BaseModel):
    asset: str
    side: Literal["buy", "sell"]
    qty: float
    type: Literal["market", "limit", "stop_limit"]
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    tif: Literal["gtc", "ioc"] = "gtc"
    client_order_id: Optional[str] = None


class OrderResult(BaseModel):
    order_id: str
    status: str                       # new | filled | partially_filled | canceled | rejected ...
    filled_qty: float = 0.0
    avg_price: float = 0.0
    fee_usd: float = 0.0


class Account(BaseModel):
    equity: float
    cash: float
    buying_power: float


class KillSwitchStatus(BaseModel):
    level: Literal["OK", "PAUSE_ENTRIES", "HALT_DAY", "HALT"] = "OK"
    reasons: list[str] = Field(default_factory=list)
    flatten: bool = False

    @property
    def allows_entries(self) -> bool:
        return self.level == "OK"


# ---------------------------------------------------------------- journal

class TradeRecord(BaseModel):
    """One row of the trade journal. Field names follow the requested spec."""
    trade_id: str
    timestamp: datetime                       # entry time
    asset: str
    side: str
    setup: str
    mode: str
    status: Literal["OPEN", "CLOSED"] = "OPEN"
    entry: float
    exit: Optional[float] = None
    position_size: float                      # quantity in base asset
    notional_usd: float
    risk_pct: float
    risk_usd: float
    stop_price: float
    take_profit_price: float
    decision_price: float
    agents_agreed: list[str]
    agents_disagreed: list[str]
    agent_signals: list[dict[str, Any]]
    confidence: float
    reason: str
    expected_return_pct: float
    actual_return_pct: Optional[float] = None
    pnl_usd: Optional[float] = None
    fees_usd: float = 0.0
    market_conditions: dict[str, Any]
    entry_slippage_bps: float = 0.0
    exit_slippage_bps: Optional[float] = None
    slippage_bps: Optional[float] = None      # total entry + exit, adverse = positive
    result: Optional[Literal["WIN", "LOSS", "BREAKEVEN"]] = None
    exit_reason: Optional[str] = None
    closed_at: Optional[datetime] = None
    holding_minutes: Optional[float] = None
    r_multiple: Optional[float] = None
    entry_order_id: str = ""
    stop_order_id: str = ""
    post_trade: dict[str, Any] = Field(default_factory=dict)
    lessons: str = ""
