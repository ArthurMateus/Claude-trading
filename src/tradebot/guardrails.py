"""HARD risk guardrails. Pure functions, no LLM, fully unit-tested.

Every limit here is a ceiling: an LLM agent may ask for less risk, never more. If anything is
ambiguous the answer is REJECT. Changes to this file require explicit human approval (CLAUDE.md).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from .config import CalibrationConfig, RiskConfig
from .contracts import TradeRecord


@dataclass
class PortfolioState:
    equity: float
    cash: float
    open_trades: list[TradeRecord]
    day_start_equity: float
    halted: bool = False
    entries_paused: bool = False

    def open_risk_usd(self, assets: set[str] | None = None) -> float:
        return sum(t.risk_usd for t in self.open_trades if assets is None or t.asset in assets)

    @property
    def heat_pct(self) -> float:
        return self.open_risk_usd() / self.equity * 100 if self.equity > 0 else math.inf

    @property
    def daily_pnl_pct(self) -> float:
        return (self.equity / self.day_start_equity - 1) * 100 if self.day_start_equity > 0 else 0.0


@dataclass
class GuardrailResult:
    approved: bool
    risk_pct: float = 0.0
    quantity: float = 0.0
    risk_usd: float = 0.0
    cluster: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- calibration ("earned" sizing)

def allowed_risk_pct(confidence: float, closed_trades: list[TradeRecord], risk: RiskConfig,
                     cal: CalibrationConfig) -> tuple[float, str]:
    """Risk above base_risk_pct is unlocked per confidence bucket only when the journal proves the
    bucket's realized win rate keeps up with its predicted confidence AND its expectancy is positive."""
    edges = cal.bucket_edges
    bucket = None
    for lo, hi in zip(edges[:-1], edges[1:]):
        if lo <= confidence < hi or (hi == edges[-1] and confidence == hi):
            bucket = (lo, hi)
            break
    if bucket is None:
        return 0.0, f"confidence {confidence:.2f} outside tradable buckets"
    in_bucket = [t for t in closed_trades if bucket[0] <= t.confidence < bucket[1]
                 or (bucket[1] == edges[-1] and t.confidence == bucket[1])]
    if len(in_bucket) < cal.min_trades_per_bucket:
        return risk.base_risk_pct, f"bucket {bucket} unproven ({len(in_bucket)}/{cal.min_trades_per_bucket} trades)"
    win_rate = sum(t.result == "WIN" for t in in_bucket) / len(in_bucket)
    mean_conf = sum(t.confidence for t in in_bucket) / len(in_bucket)
    expectancy = sum((t.r_multiple or 0.0) for t in in_bucket) / len(in_bucket)
    if win_rate + cal.tolerance < mean_conf or expectancy <= 0:
        return risk.base_risk_pct, (f"bucket {bucket} not calibrated (win {win_rate:.2f} vs conf {mean_conf:.2f}, "
                                    f"E[R] {expectancy:.2f})")
    span = max(1e-9, 1.0 - edges[0])
    scaled = risk.base_risk_pct + (risk.max_risk_pct - risk.base_risk_pct) * (confidence - edges[0]) / span
    return min(risk.max_risk_pct, scaled), f"bucket {bucket} calibrated over {len(in_bucket)} trades"


# ---------------------------------------------------------------- correlation clusters

def correlated_assets(asset: str, open_assets: set[str], corr: pd.DataFrame | None, threshold: float) -> set[str]:
    cluster = {asset}
    if corr is None:
        return cluster | ({asset} & open_assets)
    for other in open_assets:
        if other == asset:
            cluster.add(other)
        elif asset in corr.index and other in corr.columns and corr.loc[asset, other] >= threshold:
            cluster.add(other)
    return cluster


# ---------------------------------------------------------------- the check

def check_trade(*, asset: str, side: str, confidence: float, entry: float, stop: float, take_profit: float,
                spread_bps: float, requested_risk_pct: float, state: PortfolioState, closed_trades: list[TradeRecord],
                risk: RiskConfig, cal: CalibrationConfig, corr: pd.DataFrame | None = None,
                qty_step: float = 1e-6) -> GuardrailResult:
    reasons: list[str] = []

    def reject(msg: str) -> GuardrailResult:
        return GuardrailResult(False, reasons=reasons + [msg])

    if state.halted:
        return reject("system HALTED")
    if state.entries_paused:
        return reject("entries paused by kill-switch")
    if state.daily_pnl_pct <= -risk.daily_loss_limit_pct:
        return reject(f"daily loss limit hit ({state.daily_pnl_pct:.2f}%)")
    if side == "short" and not risk.allow_short:
        return reject("shorting disabled")
    if len(state.open_trades) >= risk.max_open_positions:
        return reject(f"max open positions ({risk.max_open_positions})")
    if any(t.asset == asset for t in state.open_trades):
        return reject(f"already holding {asset}")
    if confidence < risk.min_confidence:
        return reject(f"confidence {confidence:.2f} < {risk.min_confidence}")
    if spread_bps > risk.max_spread_bps:
        return reject(f"spread {spread_bps:.1f}bps > {risk.max_spread_bps}")
    if not (entry > 0 and stop > 0 and take_profit > 0):
        return reject("invalid prices")
    stop_dist = entry - stop if side == "long" else stop - entry
    reward = take_profit - entry if side == "long" else entry - take_profit
    if stop_dist <= 0 or reward <= 0:
        return reject("stop/target on wrong side of entry")
    rr = reward / stop_dist
    if rr < risk.min_reward_risk:
        return reject(f"reward:risk {rr:.2f} < {risk.min_reward_risk}")

    allowed, why = allowed_risk_pct(confidence, closed_trades, risk, cal)
    reasons.append(why)
    if allowed <= 0:
        return reject("no risk allowed for this confidence")
    risk_pct = min(max(0.0, requested_risk_pct), allowed, risk.max_risk_pct)

    # Portfolio heat: all stops hit at once must not exceed the heat cap NOR today's remaining loss budget.
    daily_headroom = risk.daily_loss_limit_pct + min(0.0, state.daily_pnl_pct)
    heat_cap = min(risk.max_portfolio_heat_pct, daily_headroom) if risk.heat_respects_daily_limit \
        else risk.max_portfolio_heat_pct
    heat_room = heat_cap - state.heat_pct
    if heat_room <= 0.05:
        return reject(f"portfolio heat {state.heat_pct:.2f}% at cap {heat_cap:.2f}%")
    if risk_pct > heat_room:
        reasons.append(f"risk trimmed {risk_pct:.2f}% -> {heat_room:.2f}% by heat cap")
        risk_pct = heat_room

    open_assets = {t.asset for t in state.open_trades}
    cluster = correlated_assets(asset, open_assets, corr, risk.correlation_threshold)
    cluster_risk_pct = state.open_risk_usd(cluster - {asset}) / state.equity * 100
    cluster_room = risk.max_cluster_risk_pct - cluster_risk_pct
    if cluster_room <= 0.05:
        return reject(f"correlated cluster {sorted(cluster)} at cap")
    if risk_pct > cluster_room:
        reasons.append(f"risk trimmed to {cluster_room:.2f}% by cluster cap {sorted(cluster)}")
        risk_pct = cluster_room

    risk_usd = state.equity * risk_pct / 100
    qty = risk_usd / stop_dist
    max_notional = min(state.equity * risk.max_position_notional_pct / 100, state.cash * 0.98)
    worst_fill = entry * (1 + risk.max_entry_slippage_bps / 1e4)   # caps must hold at the worst allowed fill
    if qty * worst_fill > max_notional:
        qty = max_notional / worst_fill
        reasons.append(f"size capped by notional/cash limit ({max_notional:.2f} USD)")
    qty = math.floor(qty / qty_step) * qty_step
    if qty * entry < risk.min_order_notional_usd:
        return reject(f"order notional {qty * entry:.2f} below minimum")
    risk_usd = qty * stop_dist
    return GuardrailResult(True, risk_pct=risk_usd / state.equity * 100, quantity=qty, risk_usd=risk_usd,
                           cluster=sorted(cluster), reasons=reasons)
