"""Deterministic trade setups.

The LLM agents do NOT invent trades from nothing: every trade must map to one of these named setups,
and the setup must currently pass the Backtest gate. This is what makes the system testable — an
LLM's judgment can't be backtested honestly (it has seen the history), but a rule can.

The library deliberately covers different market behaviors (breakout, trend continuation, mean
reversion, capitulation, session effects) so that some setup fits whatever regime the market is in.
Each one is a hypothesis: only those that pass the gate on real data, after fees, are ever traded.
Rules use closed bars only; anything referencing "prior" levels is shifted by one bar.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from .indicators import atr, bollinger, ema, rsi, volume_z, vwap


@dataclass(frozen=True)
class Setup:
    name: str
    side: str                 # long | short
    stop_atr_mult: float      # default stop distance in ATRs
    reward_risk: float        # default take-profit as a multiple of stop distance
    max_hold_bars: int        # time-stop (5m bars)
    description: str
    rule: Callable[[pd.DataFrame], pd.Series]
    regimes: tuple[str, ...] = ()   # regimes the setup is designed for (context for the LLM agents)


# ---------------------------------------------------------------- breakout

def _momentum_breakout(df: pd.DataFrame) -> pd.Series:
    close = df["close"]
    prior_high = df["high"].rolling(48).max().shift(1)
    return (close > prior_high) & (volume_z(df["volume"]) > 1.5) & (ema(close, 20) > ema(close, 50))


def _squeeze_breakout(df: pd.DataFrame) -> pd.Series:
    close = df["close"]
    mid, upper, lower = bollinger(close)
    width = (upper - lower) / mid
    squeezed = width.shift(1) <= width.rolling(288, min_periods=144).quantile(0.2).shift(1)
    return squeezed & (close > upper) & (volume_z(df["volume"]) > 1.0)


def _session_range_breakout(df: pd.DataFrame) -> pd.Series:
    """US cash-equity open (09:30-10:30 New York) range, broken upward before 14:00 New York on weekdays."""
    local = df.index.tz_convert("America/New_York")
    minutes = pd.Series(local.hour * 60 + local.minute, index=df.index)
    day = pd.Series(local.date, index=df.index)
    in_range = (minutes >= 570) & (minutes < 630)
    range_high = df["high"].where(in_range).groupby(day).transform("max")
    window = (minutes >= 630) & (minutes < 840) & pd.Series(local.weekday < 5, index=df.index)
    close = df["close"]
    first_break = (close > range_high) & (close.shift(1) <= range_high)
    return window & first_break & (volume_z(df["volume"]) > 0.5)


# ---------------------------------------------------------------- trend continuation

def _trend_pullback(df: pd.DataFrame) -> pd.Series:
    close = df["close"]
    e20, e50 = ema(close, 20), ema(close, 50)
    r = rsi(close)
    return (e20 > e50) & (df["low"] <= e20) & (close > e20) & r.between(40, 55) & (e50.pct_change(12) > 0)


def _ema_cross_trend(df: pd.DataFrame) -> pd.Series:
    close = df["close"]
    e9, e21, e_day = ema(close, 9), ema(close, 21), ema(close, 288)   # 288 x 5m = 24h trend filter
    cross_up = (e9 > e21) & (e9.shift(1) <= e21.shift(1))
    return cross_up & (close > e_day) & (rsi(close) < 70)


def _vwap_reclaim(df: pd.DataFrame) -> pd.Series:
    close = df["close"]
    v = vwap(df)
    was_below = (close.shift(1) < v.shift(1)) & (close.shift(2) < v.shift(2))
    not_downtrend = ema(close, 50) >= ema(close, 50).shift(12)
    return was_below & (close > v) & (volume_z(df["volume"]) > 1.0) & not_downtrend


# ---------------------------------------------------------------- mean reversion

def _mean_reversion(df: pd.DataFrame) -> pd.Series:
    close = df["close"]
    _, _, lower = bollinger(close)
    flat_trend = ema(close, 50).pct_change(12).abs() < 0.01
    return (rsi(close) < 28) & (close < lower) & flat_trend


def _capitulation_reversal(df: pd.DataFrame) -> pd.Series:
    """Climactic sell bar: huge volume, long lower wick, close back in the upper half of the bar."""
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    rng = (h - l).where(h > l)
    lower_wick = pd.concat([o, c], axis=1).min(axis=1) - l
    return (volume_z(df["volume"]) > 3.0) & (lower_wick / rng >= 0.5) & ((c - l) / rng >= 0.5) & (rsi(c) < 35)


SETUPS: dict[str, Setup] = {
    s.name: s
    for s in [
        Setup("momentum_breakout", "long", 1.5, 2.0, 36,
              "Close breaks the prior 4h high on a volume spike while EMA20 > EMA50.",
              _momentum_breakout, ("trend_up", "volatile")),
        Setup("squeeze_breakout", "long", 1.5, 2.0, 36,
              "Bollinger width in its lowest 20% of the past day, then a close above the upper band on volume.",
              _squeeze_breakout, ("range", "trend_up")),
        Setup("session_range_breakout", "long", 1.5, 2.0, 36,
              "Weekday break above the 09:30-10:30 New York range (US equity open) before 14:00 NY.",
              _session_range_breakout, ("trend_up", "range", "volatile")),
        Setup("trend_pullback", "long", 1.5, 2.0, 48,
              "Uptrend pullback that tags EMA20 and closes back above it with neutral RSI.",
              _trend_pullback, ("trend_up",)),
        Setup("ema_cross_trend", "long", 1.5, 2.0, 48,
              "EMA9 crosses above EMA21 while price is above the 24h EMA and RSI < 70.",
              _ema_cross_trend, ("trend_up",)),
        Setup("vwap_reclaim", "long", 1.2, 1.8, 24,
              "Two closes below rolling VWAP, then a close back above it on volume, not in a downtrend.",
              _vwap_reclaim, ("range", "trend_up")),
        Setup("mean_reversion", "long", 1.2, 1.5, 24,
              "Oversold (RSI<28) close below the lower Bollinger band in a flat regime.",
              _mean_reversion, ("range",)),
        Setup("capitulation_reversal", "long", 1.0, 2.0, 24,
              "Climactic sell bar (volume z>3) with a long lower wick closing in its upper half, RSI<35.",
              _capitulation_reversal, ("volatile", "trend_down", "range")),
    ]
}


def setup_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Boolean frame, one column per setup, evaluated on CLOSED bars only (no lookahead)."""
    return pd.DataFrame({name: s.rule(df).fillna(False).astype(bool) for name, s in SETUPS.items()}, index=df.index)


def active_setups(df: pd.DataFrame) -> list[str]:
    if len(df) < 60:
        return []
    last = setup_signals(df).iloc[-1]
    return [name for name, on in last.items() if on]


def setup_context(name: str) -> dict:
    s = SETUPS[name]
    return {"description": s.description, "fits_regimes": list(s.regimes), "default_stop_atr": s.stop_atr_mult,
            "default_reward_risk": s.reward_risk, "max_hold_minutes_at_5m": s.max_hold_bars * 5}
