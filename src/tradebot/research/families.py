"""Parametrized strategy families for research.

Every family returns a boolean entry-condition Series computed on CLOSED bars only (anything referencing a
prior level is shifted; higher-timeframe values are aligned to the bar on which they become known). Entries
are taken on the rising edge of the condition, at the next bar's open.

Each family has a long and a short form. Short forms need a venue that allows shorting (perps); the bot's
Alpaca spot venue is long-only. `live_ok=False` marks families needing data the live bot doesn't have.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Callable

import numpy as np
import pandas as pd

from ..indicators import atr, bollinger, ema, rsi, volume_z, vwap


@dataclass(frozen=True)
class Family:
    name: str
    description: str
    signal: Callable[..., pd.Series]           # signal(df, side, tf_minutes, **params) -> bool Series
    grid: dict[str, list] = field(default_factory=dict)
    live_ok: bool = True

    def param_sets(self) -> list[dict]:
        keys = list(self.grid)
        return [dict(zip(keys, vals)) for vals in product(*(self.grid[k] for k in keys))] or [{}]


def _bars(tf: int, minutes: float) -> int:
    return max(1, int(round(minutes / tf)))


def _htf_align(df: pd.DataFrame, values: pd.Series, htf_minutes: int, tf: int) -> pd.Series:
    """Align a series computed on higher-timeframe bars (open-time index) to base bars without lookahead:
    an HTF bar opened at t is known at t + htf; base bars are open-time indexed, so it applies from the base
    bar that opens at t + htf - tf (whose close is the HTF close)."""
    shifted = values.copy()
    shifted.index = values.index + pd.Timedelta(minutes=htf_minutes - tf)
    return shifted.reindex(df.index, method="ffill")


def _resample(df: pd.DataFrame, minutes: int, base: int) -> pd.DataFrame:
    from .history import resample
    return resample(df, minutes, base=base)


# ---------------------------------------------------------------- breakout / momentum

def donchian_breakout(df, side, tf, lookback_h, vol_z):
    n = _bars(tf, lookback_h * 60)
    vz = volume_z(df["volume"]) > vol_z
    if side == "long":
        return (df["close"] > df["high"].rolling(n).max().shift(1)) & vz
    return (df["close"] < df["low"].rolling(n).min().shift(1)) & vz


def squeeze_breakout(df, side, tf, quantile, window_h):
    close = df["close"]
    mid, upper, lower = bollinger(close)
    width = (upper - lower) / mid
    w = _bars(tf, window_h * 60)
    squeezed = width.shift(1) <= width.rolling(w, min_periods=w // 2).quantile(quantile).shift(1)
    vz = volume_z(df["volume"]) > 0.5
    return squeezed & vz & ((close > upper) if side == "long" else (close < lower))


def vol_breakout(df, side, tf, k):
    """Larry-Williams-style: today's (UTC) open +/- k x yesterday's range."""
    day = df.index.floor("D")
    daily = df.resample("1D").agg({"open": "first", "high": "max", "low": "min"})
    prev_range = (daily["high"] - daily["low"]).shift(1)
    day_open = pd.Series(daily["open"].reindex(day).to_numpy(), index=df.index)
    rng = pd.Series(prev_range.reindex(day).to_numpy(), index=df.index)
    if side == "long":
        return df["close"] > day_open + k * rng
    return df["close"] < day_open - k * rng


def momentum_burst(df, side, tf, z, window_h):
    n = _bars(tf, window_h * 60)
    r = df["close"].pct_change(n)
    rz = (r - r.rolling(500, min_periods=200).mean()) / r.rolling(500, min_periods=200).std()
    vz = volume_z(df["volume"]) > 1.0
    return ((rz > z) if side == "long" else (rz < -z)) & vz


def session_breakout(df, side, tf, session):
    """Break of the first hour's range of a session, within the following 4 hours."""
    if session == "us_open":
        local = df.index.tz_convert("America/New_York")
        start_min, weekdays_only = 9 * 60 + 30, True
    else:   # utc_day
        local = df.index
        start_min, weekdays_only = 0, False
    minutes = pd.Series(local.hour * 60 + local.minute, index=df.index)
    day = pd.Series(local.date, index=df.index)
    in_range = (minutes >= start_min) & (minutes < start_min + 60)
    hi = df["high"].where(in_range).groupby(day).transform("max")
    lo = df["low"].where(in_range).groupby(day).transform("min")
    window = (minutes >= start_min + 60) & (minutes < start_min + 300)
    if weekdays_only:
        window &= pd.Series(local.weekday < 5, index=df.index)
    close = df["close"]
    if side == "long":
        return window & (close > hi)
    return window & (close < lo)


# ---------------------------------------------------------------- trend

def ema_cross(df, side, tf, fast, slow, trend_filter):
    close = df["close"]
    f, s = ema(close, fast), ema(close, slow)
    trend = ema(close, _bars(tf, 24 * 60))      # ~1 day
    if side == "long":
        cond = f > s
        return cond & (close > trend) if trend_filter else cond
    cond = f < s
    return cond & (close < trend) if trend_filter else cond


def mtf_pullback(df, side, tf, rsi_level):
    """Higher-timeframe (4x) trend + pullback on this timeframe that resumes above/below EMA20."""
    htf = 4 * tf
    h = _resample(df, htf, base=tf)
    up = _htf_align(df, (ema(h["close"], 50) > ema(h["close"], 200)).astype(float), htf, tf) > 0.5
    close, e20 = df["close"], ema(df["close"], 20)
    r = rsi(close)
    if side == "long":
        return up & (r.shift(1) < rsi_level) & (close > e20)
    down = _htf_align(df, (ema(h["close"], 50) < ema(h["close"], 200)).astype(float), htf, tf) > 0.5
    return down & (r.shift(1) > 100 - rsi_level) & (close < e20)


# ---------------------------------------------------------------- mean reversion

def rsi2_reversion(df, side, tf, level):
    """Connors RSI(2): buy short-term oversold in an uptrend (sell overbought in a downtrend)."""
    close = df["close"]
    r2, trend = rsi(close, 2), ema(close, 200)
    if side == "long":
        return (r2 < level) & (close > trend)
    return (r2 > 100 - level) & (close < trend)


def bb_reversion(df, side, tf, k, max_slope):
    close = df["close"]
    _, upper, lower = bollinger(close, 20, k)
    flat = ema(close, 50).pct_change(12).abs() < max_slope
    return flat & ((close < lower) if side == "long" else (close > upper))


def vwap_reclaim(df, side, tf, window_h, vol_z):
    close = df["close"]
    v = vwap(df, _bars(tf, window_h * 60))
    vz = volume_z(df["volume"]) > vol_z
    if side == "long":
        return (close.shift(1) < v.shift(1)) & (close.shift(2) < v.shift(2)) & (close > v) & vz
    return (close.shift(1) > v.shift(1)) & (close.shift(2) > v.shift(2)) & (close < v) & vz


def climax_reversal(df, side, tf, vol_z, wick):
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    rng = (h - l).where(h > l)
    vz = volume_z(df["volume"]) > vol_z
    if side == "long":   # capitulation: long lower wick, close in upper half
        lower_wick = pd.concat([o, c], axis=1).min(axis=1) - l
        return vz & (lower_wick / rng >= wick) & ((c - l) / rng >= 0.5) & (rsi(c) < 35)
    upper_wick = h - pd.concat([o, c], axis=1).max(axis=1)   # blow-off top
    return vz & (upper_wick / rng >= wick) & ((h - c) / rng >= 0.5) & (rsi(c) > 65)


# ---------------------------------------------------------------- order flow (Binance taker data; not on Alpaca)

def taker_flow(df, side, tf, threshold, window_h):
    n = _bars(tf, window_h * 60)
    if "taker_buy_base" not in df:
        return pd.Series(False, index=df.index)
    ratio = df["taker_buy_base"].rolling(n).sum() / df["volume"].rolling(n).sum()
    e20 = ema(df["close"], 20)
    if side == "long":
        return (ratio > threshold) & (df["close"] > e20)
    return (ratio < 1 - threshold) & (df["close"] < e20)


FAMILIES: dict[str, Family] = {f.name: f for f in [
    Family("donchian_breakout", "Close beyond the prior N-hour high/low, optional volume filter.",
           donchian_breakout, {"lookback_h": [4, 12, 24], "vol_z": [0.0, 1.5]}),
    Family("squeeze_breakout", "Bollinger width in its lowest quantile, then a close outside the band on volume.",
           squeeze_breakout, {"quantile": [0.1, 0.2], "window_h": [24, 72]}),
    Family("vol_breakout", "Close beyond today's UTC open +/- k x yesterday's range.",
           vol_breakout, {"k": [0.3, 0.5, 0.8]}),
    Family("momentum_burst", "Return z-score over the window beyond z, with a volume spike.",
           momentum_burst, {"z": [2.0, 3.0], "window_h": [1, 4]}),
    Family("session_breakout", "Break of the first-hour range of the US equity open or the UTC day.",
           session_breakout, {"session": ["us_open", "utc_day"]}),
    Family("ema_cross", "Fast EMA above/below slow EMA, optional ~1-day trend filter.",
           ema_cross, {"fast": [9, 20], "slow": [50, 100], "trend_filter": [True, False]}),
    Family("mtf_pullback", "4x-timeframe EMA50/200 trend with a pullback that resumes through EMA20.",
           mtf_pullback, {"rsi_level": [35, 45]}),
    Family("rsi2_reversion", "Connors RSI(2) extreme in the direction of the EMA200 trend.",
           rsi2_reversion, {"level": [5, 10, 20]}),
    Family("bb_reversion", "Close outside a Bollinger band while the EMA50 trend is flat.",
           bb_reversion, {"k": [2.0, 2.5], "max_slope": [0.005, 0.01]}),
    Family("vwap_reclaim", "Two closes on one side of rolling VWAP, then a close back through it on volume.",
           vwap_reclaim, {"window_h": [4, 12], "vol_z": [0.5, 1.0]}),
    Family("climax_reversal", "Volume-climax bar with a long rejection wick (capitulation / blow-off).",
           climax_reversal, {"vol_z": [2.5, 3.5], "wick": [0.5, 0.6]}),
    Family("taker_flow", "Aggressor (taker-buy) volume share beyond a threshold, with the EMA20 trend.",
           taker_flow, {"threshold": [0.55, 0.6], "window_h": [1, 4]}, live_ok=False),
]}

TIMEFRAMES = [5, 15, 60]
SIDES = ["long", "short"]
EXITS = [(1.0, 2.0), (1.5, 2.0), (1.5, 3.0), (2.0, 1.5)]   # (stop ATR multiple, reward:risk)
MAX_HOLD_MINUTES = 240


def entry_edges(cond: pd.Series) -> np.ndarray:
    """Indices where the condition turns on (rising edge) — one entry per episode."""
    c = cond.fillna(False).to_numpy(dtype=bool)
    prev = np.concatenate([[False], c[:-1]])
    return np.flatnonzero(c & ~prev)


def strategy_id(family: str, side: str, tf: int, params: dict, exit_: tuple[float, float]) -> str:
    p = ",".join(f"{k}={v}" for k, v in params.items())
    return f"{family}|{side}|{tf}m|{p}|stop{exit_[0]}xATR,rr{exit_[1]}"
