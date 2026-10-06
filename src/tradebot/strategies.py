"""Deterministic trade setups.

The LLM agents do NOT invent trades from nothing: every trade must map to one of these named setups,
and the setup must currently pass the Backtest gate. This is what makes the system testable — an
LLM's judgment can't be backtested honestly (it has seen the history), but a rule can.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from .indicators import atr, bollinger, ema, rsi, volume_z


@dataclass(frozen=True)
class Setup:
    name: str
    side: str                 # long | short
    stop_atr_mult: float      # default stop distance in ATRs
    reward_risk: float        # default take-profit as a multiple of stop distance
    max_hold_bars: int        # time-stop (5m bars)
    description: str
    rule: Callable[[pd.DataFrame], pd.Series]


def _momentum_breakout(df: pd.DataFrame) -> pd.Series:
    close = df["close"]
    prior_high = df["high"].rolling(48).max().shift(1)
    return (close > prior_high) & (volume_z(df["volume"]) > 1.5) & (ema(close, 20) > ema(close, 50))


def _mean_reversion(df: pd.DataFrame) -> pd.Series:
    close = df["close"]
    _, _, lower = bollinger(close)
    flat_trend = ema(close, 50).pct_change(12).abs() < 0.01
    return (rsi(close) < 28) & (close < lower) & flat_trend


def _trend_pullback(df: pd.DataFrame) -> pd.Series:
    close = df["close"]
    e20, e50 = ema(close, 20), ema(close, 50)
    r = rsi(close)
    return (e20 > e50) & (df["low"] <= e20) & (close > e20) & r.between(40, 55) & (e50.pct_change(12) > 0)


SETUPS: dict[str, Setup] = {
    s.name: s
    for s in [
        Setup("momentum_breakout", "long", 1.5, 2.0, 36,
              "Close breaks the prior 4h high on a volume spike while EMA20 > EMA50.", _momentum_breakout),
        Setup("mean_reversion", "long", 1.2, 1.5, 24,
              "Oversold (RSI<28) close below the lower Bollinger band in a flat regime.", _mean_reversion),
        Setup("trend_pullback", "long", 1.5, 2.0, 48,
              "Uptrend pullback that tags EMA20 and closes back above it with neutral RSI.", _trend_pullback),
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


def default_atr(df: pd.DataFrame) -> float:
    return float(atr(df).iloc[-1])
