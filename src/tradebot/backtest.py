"""Event-driven bar backtester for the deterministic setups.

Conservative by construction:
- signals use closed bars only; entries fill at the NEXT bar's open plus slippage
- taker fees on both sides
- if stop and take-profit are both touched inside one bar, the stop is assumed to fill first
- gaps through the stop fill at the (worse) open
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .indicators import atr
from .strategies import SETUPS, Setup


@dataclass
class BTTrade:
    setup: str
    entry_ts: str
    exit_ts: str
    entry: float
    exit: float
    ret: float          # net of fees and slippage, fraction
    r_multiple: float
    exit_reason: str
    bars_held: int


@dataclass
class BTStats:
    trades: int
    win_rate: float
    profit_factor: float
    expectancy_bps: float
    avg_win_bps: float
    avg_loss_bps: float
    sharpe_per_trade: float
    max_drawdown_pct: float      # equity curve when risking base_risk_pct per trade

    def to_dict(self) -> dict:
        return asdict(self)


def run_setup(df: pd.DataFrame, setup: Setup, fee_bps: float, slippage_bps: float) -> list[BTTrade]:
    if len(df) < 80:
        return []
    sig = setup.rule(df).fillna(False).to_numpy()
    a = atr(df).to_numpy()
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    idx = df.index
    fee, slip = fee_bps / 1e4, slippage_bps / 1e4
    trades: list[BTTrade] = []
    i, n = 60, len(df)
    while i < n - 1:
        if not sig[i] or not np.isfinite(a[i]) or a[i] <= 0:
            i += 1
            continue
        e_i = i + 1
        entry = o[e_i] * (1 + slip)
        stop = entry - setup.stop_atr_mult * a[i]
        tp = entry + setup.reward_risk * (entry - stop)
        exit_px, reason, j = None, "", e_i
        for j in range(e_i, min(n, e_i + setup.max_hold_bars)):
            if l[j] <= stop:
                exit_px, reason = min(o[j], stop) * (1 - slip), "stop"
                break
            if h[j] >= tp:
                exit_px, reason = tp, "take_profit"
                break
        if exit_px is None:
            j = min(n - 1, e_i + setup.max_hold_bars - 1)
            exit_px, reason = c[j] * (1 - slip), "time_stop"
        ret = exit_px / entry - 1 - 2 * fee
        risk_frac = (entry - stop) / entry
        trades.append(BTTrade(setup.name, str(idx[e_i]), str(idx[j]), float(entry), float(exit_px), float(ret),
                              float(ret / risk_frac) if risk_frac > 0 else 0.0, reason, j - e_i + 1))
        i = j + 1   # one position per setup at a time
    return trades


def stats(trades: list[BTTrade], risk_pct: float = 1.0) -> BTStats:
    if not trades:
        return BTStats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    rets = np.array([t.ret for t in trades])
    wins, losses = rets[rets > 0], rets[rets <= 0]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    pf = float(gross_win / gross_loss) if gross_loss > 0 else math.inf
    sharpe = float(rets.mean() / rets.std() * math.sqrt(len(rets))) if rets.std() > 0 else 0.0
    equity, peak, max_dd = 1.0, 1.0, 0.0
    for t in trades:
        equity *= 1 + risk_pct / 100 * t.r_multiple
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
    return BTStats(
        trades=len(trades),
        win_rate=float(len(wins) / len(rets)),
        profit_factor=pf,
        expectancy_bps=float(rets.mean() * 1e4),
        avg_win_bps=float(wins.mean() * 1e4) if len(wins) else 0.0,
        avg_loss_bps=float(losses.mean() * 1e4) if len(losses) else 0.0,
        sharpe_per_trade=sharpe,
        max_drawdown_pct=float(max_dd * 100),
    )


def backtest_setup_pooled(bars: dict[str, pd.DataFrame], setup_name: str, fee_bps: float,
                          slippage_bps: float, risk_pct: float = 1.0) -> dict:
    """Run one setup across all assets; report full-sample and out-of-sample (second half) stats."""
    setup = SETUPS[setup_name]
    all_trades: list[BTTrade] = []
    oos_trades: list[BTTrade] = []
    for df in bars.values():
        trades = run_setup(df, setup, fee_bps, slippage_bps)
        all_trades += trades
        if len(df):
            split = str(df.index[len(df) // 2])
            oos_trades += [t for t in trades if t.entry_ts >= split]
    all_trades.sort(key=lambda t: t.entry_ts)
    oos_trades.sort(key=lambda t: t.entry_ts)
    return {"setup": setup_name, "full": stats(all_trades, risk_pct).to_dict(),
            "oos": stats(oos_trades, risk_pct).to_dict()}
