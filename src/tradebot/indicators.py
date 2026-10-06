"""Deterministic indicator math. Agents (LLM or not) reason over these numbers; they never compute them."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std()
    return mid, mid + k * sd, mid - k * sd


def volume_z(volume: pd.Series, n: int = 50) -> pd.Series:
    mu = volume.rolling(n).mean()
    sd = volume.rolling(n).std().replace(0, np.nan)
    return ((volume - mu) / sd).fillna(0)


def vwap(df: pd.DataFrame, n: int = 48) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    return (typical * df["volume"]).rolling(n).sum() / df["volume"].rolling(n).sum()


def pct_change_over(close: pd.Series, bars: int) -> float:
    if len(close) <= bars:
        return 0.0
    return float((close.iloc[-1] / close.iloc[-1 - bars] - 1) * 100)


def classify_regime(df: pd.DataFrame) -> str:
    """Cheap rule-based regime label; the Market Data Agent's LLM may refine it."""
    close = df["close"]
    if len(close) < 60:
        return "unknown"
    e20, e50 = ema(close, 20).iloc[-1], ema(close, 50).iloc[-1]
    slope = (ema(close, 50).iloc[-1] / ema(close, 50).iloc[-13] - 1) * 100
    vol = close.pct_change().tail(48).std() * 100
    long_vol = close.pct_change().std() * 100
    if long_vol > 0 and vol > 2.0 * long_vol:
        return "volatile"
    if e20 > e50 and slope > 0.3:
        return "trend_up"
    if e20 < e50 and slope < -0.3:
        return "trend_down"
    return "range"


def feature_row(df: pd.DataFrame, bar_minutes: int = 5) -> dict[str, float]:
    """Latest-bar feature vector shared by Technical, Quant and Flow agents."""
    close = df["close"]
    bars_per_hour = max(1, 60 // bar_minutes)
    mid, upper, lower = bollinger(close)
    a = atr(df)
    last = float(close.iloc[-1])
    return {
        "close": last,
        "ema20": float(ema(close, 20).iloc[-1]),
        "ema50": float(ema(close, 50).iloc[-1]),
        "rsi14": float(rsi(close).iloc[-1]),
        "atr": float(a.iloc[-1]),
        "atr_pct": float(a.iloc[-1] / last * 100),
        "bb_upper": float(upper.iloc[-1]),
        "bb_lower": float(lower.iloc[-1]),
        "bb_pos": float((last - lower.iloc[-1]) / max(1e-12, upper.iloc[-1] - lower.iloc[-1])),
        "vwap_dist_pct": float((last / vwap(df).iloc[-1] - 1) * 100),
        "volume_z": float(volume_z(df["volume"]).iloc[-1]),
        "ret_1h_pct": pct_change_over(close, bars_per_hour),
        "ret_4h_pct": pct_change_over(close, 4 * bars_per_hour),
        "ret_24h_pct": pct_change_over(close, 24 * bars_per_hour),
        "realized_vol_pct": float(close.pct_change().tail(48).std() * 100),
    }
