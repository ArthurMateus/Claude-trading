"""Typed configuration loaded from config/settings.yaml."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "settings.yaml"


class RiskConfig(BaseModel):
    base_risk_pct: float = 1.0
    max_risk_pct: float = 5.0
    daily_loss_limit_pct: float = 5.0
    max_drawdown_pct: float = 15.0
    max_portfolio_heat_pct: float = 6.0
    heat_respects_daily_limit: bool = True
    max_cluster_risk_pct: float = 5.0
    correlation_threshold: float = 0.7
    max_open_positions: int = 8
    min_reward_risk: float = 1.5
    max_spread_bps: float = 15
    max_position_notional_pct: float = 30
    max_entry_slippage_bps: float = 20
    min_order_notional_usd: float = 10
    min_confidence: float = 0.55
    allow_short: bool = False


class CalibrationConfig(BaseModel):
    bucket_edges: list[float] = [0.55, 0.65, 0.75, 0.85, 1.0]
    min_trades_per_bucket: int = 30
    tolerance: float = 0.05


class KillSwitchConfig(BaseModel):
    max_consecutive_losses: int = 5
    loss_streak_cooldown_minutes: int = 120
    max_data_staleness_seconds: int = 900
    max_api_errors_per_hour: int = 10
    max_avg_slippage_bps: float = 30
    max_orders_per_hour: int = 30
    llm_review: bool = True


class CostConfig(BaseModel):
    taker_fee_bps: float = 25
    maker_fee_bps: float = 15
    sim_slippage_bps: float = 5


class ValidationConfig(BaseModel):
    lookback_days: int = 60
    min_trades: int = 40
    min_profit_factor: float = 1.2
    min_expectancy_bps: float = 5
    max_drawdown_pct: float = 15.0
    max_age_hours: int = 168


class PromotionConfig(BaseModel):
    min_trades: int = 100
    min_days: int = 28
    min_profit_factor: float = 1.2
    max_drawdown_pct: float = 10.0
    max_slippage_vs_model_bps: float = 10


class NewsConfig(BaseModel):
    lookback_hours: float = 6
    refresh_minutes: int = 5
    rss_feeds: list[str] = ["coindesk", "cointelegraph", "decrypt"]
    reddit_subreddits: list[str] = ["CryptoCurrency", "Bitcoin", "ethereum", "solana"]
    x_enabled: bool = True              # only active when X_BEARER_TOKEN is set
    x_max_posts_per_day: int = 100      # pay-per-use ~$0.005/post => at most ~$0.50/day
    x_posts_per_query: int = 10
    cryptopanic_enabled: bool = True    # only active when CRYPTOPANIC_TOKEN is set
    cryptopanic_url: str = "https://cryptopanic.com/api/developer/v2/posts/"
    asset_keywords: dict[str, list[str]] = Field(default_factory=lambda: {
        "BTC/USD": ["bitcoin", "BTC"], "ETH/USD": ["ethereum", "ether", "ETH"], "SOL/USD": ["solana", "SOL"],
        "LTC/USD": ["litecoin", "LTC"], "LINK/USD": ["chainlink", "LINK"], "AVAX/USD": ["avalanche", "AVAX"],
        "DOGE/USD": ["dogecoin", "DOGE"]})


class ReplicationConfig(BaseModel):
    follow_internal_agents: bool = True
    lookback_trades: int = 30
    min_agent_observations: int = 10
    hot_hit_rate: float = 0.55
    external_feed: str = "data/replication_feed.json"
    external_horizon_minutes: int = 120
    external_min_actions: int = 20
    external_min_hit_rate: float = 0.55
    external_min_avg_return_pct: float = 0.5   # must beat a round trip of fees + slippage


class ModelConfig(BaseModel):
    model: str
    effort: Optional[Literal["low", "medium", "high", "xhigh", "max"]] = None
    max_tokens: int = 4000


class LLMConfig(BaseModel):
    backend: Literal["claude_cli", "api"] = "claude_cli"
    claude_cli_path: str = "claude"
    cli_timeout_seconds: int = 180
    cli_opus_fallback_model: str = "claude-sonnet-5-5"
    monthly_budget_usd: Optional[float] = 18.0
    daily_budget_usd: float = 0.6
    prefilter_min_abs_score: float = 0.15
    market_data_review: bool = True     # one cheap call per cycle; a fixed daily cost
    orchestrator: ModelConfig = ModelConfig(model="claude-opus-5-5", effort="high", max_tokens=16000)
    agents: dict[str, ModelConfig] = Field(default_factory=dict)

    def for_role(self, role: str) -> ModelConfig:
        if role == "orchestrator":
            return self.orchestrator
        return self.agents.get(role, ModelConfig(model="claude-haiku-4-5", max_tokens=2000))


class Settings(BaseModel):
    mode: Literal["simulated", "paper", "live"] = "paper"
    broker: Literal["alpaca", "simulated"] = "alpaca"
    data_provider: Literal["alpaca", "synthetic"] = "alpaca"
    journal_path: str = "data/journal.sqlite"
    allocated_capital_usd: Optional[float] = None   # trade a virtual sub-account of this size (None = whole account)
    universe: list[str] = ["BTC/USD", "ETH/USD"]
    cycle_interval_seconds: int = 300
    bar_timeframe_minutes: int = 5
    bars_lookback: int = 300
    max_hold_minutes: int = 240
    risk: RiskConfig = RiskConfig()
    calibration: CalibrationConfig = CalibrationConfig()
    kill_switch: KillSwitchConfig = KillSwitchConfig()
    costs: CostConfig = CostConfig()
    validation: ValidationConfig = ValidationConfig()
    promotion: PromotionConfig = PromotionConfig()
    llm: LLMConfig = LLMConfig()
    news: NewsConfig = NewsConfig()
    replication: ReplicationConfig = ReplicationConfig()
    fusion_weights: dict[str, float] = Field(default_factory=dict)
    agents_enabled: dict[str, bool] = Field(default_factory=dict)

    def agent_enabled(self, name: str) -> bool:
        return self.agents_enabled.get(name, True)


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader (no extra dependency). Existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")   # allow trailing comments
        if value:
            os.environ.setdefault(key.strip(), value)


def load_settings(path: Path | str | None = None) -> Settings:
    p = Path(path) if path else DEFAULT_CONFIG
    data = yaml.safe_load(p.read_text()) if p.exists() else {}
    return Settings.model_validate(data or {})
