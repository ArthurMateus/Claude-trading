"""Vectorized trade simulation and cost models for research.

Trades are generated GROSS (prices only) once per configuration; cost models are applied afterwards, so the
same trades can be judged under Alpaca spot fees and under perpetual-futures fees + funding.

Conservative fills: entry at the next bar's open; if the stop and the target are both touched in one bar the
stop is assumed first; a gap through the stop fills at the (worse) open; take-profit fills exactly at the
target (no gap improvement); a time exit fills at the close of the last allowed bar.
Timestamps: `entry_ts` is the entry bar's open, `exit_ts` is the CLOSE of the bar in which the exit happened,
so nothing downstream can use an exit's P&L before that bar has finished.
Trades of one configuration on one asset never overlap: a new entry needs the previous trade to be closed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from ..indicators import atr


@dataclass(frozen=True)
class CostModel:
    name: str
    fee_bps: float                     # per side
    slippage_bps: float                # per side, default
    funding_bps_8h: float              # paid on notional per 8h held (perps); 0 for spot
    max_leverage: float
    allow_short: bool
    slippage_overrides: tuple = ()     # ((asset, bps), ...) per-asset slippage

    def slippage(self, assets) -> np.ndarray:
        over = dict(self.slippage_overrides)
        return np.array([over.get(a, self.slippage_bps) for a in assets], dtype=float)

    def round_trip_bps(self, assets) -> np.ndarray:
        return 2 * (self.fee_bps + self.slippage(assets))

    def net(self, gross: np.ndarray, hours: np.ndarray, assets, funding: np.ndarray | None = None) -> np.ndarray:
        """Net return per trade. On a funded venue, `funding` (the signed rates actually settled while the trade
        was open, positive = paid) replaces the flat estimate wherever it is known."""
        flat = self.funding_bps_8h / 1e4 * np.asarray(hours, dtype=float) / 8
        if self.funding_bps_8h and funding is not None:
            funding = np.asarray(funding, dtype=float)
            flat = np.where(np.isfinite(funding), funding, flat)
        return gross - self.round_trip_bps(assets) / 1e4 - (flat if self.funding_bps_8h else 0.0)

    def net_trades(self, t: pd.DataFrame) -> np.ndarray:
        if not len(t):
            return np.array([])
        return self.net(t["gross"].to_numpy(), t["hours"].to_numpy(), t["asset"].to_numpy(),
                        t["funding"].to_numpy() if "funding" in t else None)


# Alpaca's books outside BTC/ETH are thin: 15 bps slippage per side there, 5 bps on BTC/ETH.
ALPACA_SPOT = CostModel("alpaca_spot", fee_bps=25, slippage_bps=15, funding_bps_8h=0, max_leverage=1,
                        allow_short=False, slippage_overrides=(("BTC/USD", 5.0), ("ETH/USD", 5.0)))
PERP = CostModel("perp", fee_bps=5, slippage_bps=3, funding_bps_8h=1.0, max_leverage=20, allow_short=True)
COST_MODELS = {m.name: m for m in (ALPACA_SPOT, PERP)}

def funding_paid(trades: pd.DataFrame, rates: pd.Series | None) -> np.ndarray:
    """Signed funding a position paid (positive) or received over its life: every settlement after entry up to
    and including exit, longs pay positive rates. NaN when the rate history doesn't cover the trade."""
    out = np.full(len(trades), np.nan)
    if rates is None or not len(rates) or not len(trades):
        return out
    ts = rates.index.as_unit("ns").asi8
    cs = np.concatenate([[0.0], np.cumsum(rates.to_numpy(dtype=float))])
    entry = pd.DatetimeIndex(trades["entry_ts"]).as_unit("ns").asi8
    exit_ = pd.DatetimeIndex(trades["exit_ts"]).as_unit("ns").asi8
    paid = cs[np.searchsorted(ts, exit_, "right")] - cs[np.searchsorted(ts, entry, "right")]
    sgn = np.where(trades["side"].to_numpy() == "long", 1.0, -1.0)
    covered = (entry >= ts[0]) & (exit_ <= ts[-1] + pd.Timedelta(hours=8).value)
    out[covered] = (sgn * paid)[covered]
    return out


TRADE_COLUMNS = ["asset", "entry_ts", "exit_ts", "exit_bar_ts", "side", "entry", "exit", "stop_frac", "gross",
                 "hours", "mae", "exit_reason"]


def simulate(df: pd.DataFrame, entries: np.ndarray, side: str, exits: list[tuple[float, float]],
             max_hold_bars: int, asset: str = "", tf_minutes: int = 5,
             non_overlapping: bool = True) -> dict[tuple[float, float], pd.DataFrame]:
    """Trades for the entry indices (signal bars) under every (stop ATR, reward:risk) exit rule; non-overlapping
    per configuration unless `non_overlapping` is False (the random-entry null samples from all of them)."""
    n, H = len(df), max_hold_bars
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    a = atr(df).to_numpy(dtype=float)
    idx = df.index
    tf = pd.Timedelta(minutes=tf_minutes)
    entries = entries[(entries + 1 + H <= n)]
    entries = entries[np.isfinite(a[entries]) & (a[entries] > 0)]
    # Drop windows that span a data gap (exchange outage), including a gap between the signal bar and the entry
    # bar: signal + H hold bars must be consecutive in wall-clock time.
    if len(entries):
        ns = idx.as_unit("ns").asi8
        span = ns[entries + H] - ns[entries]
        entries = entries[span <= H * tf.value]
    if len(entries) == 0:
        return {ex: pd.DataFrame(columns=TRADE_COLUMNS) for ex in exits}
    e = entries + 1
    W_o, W_h, W_l = (sliding_window_view(x, H)[e] for x in (o, h, l))
    last_close = c[e + H - 1]
    entry = o[e]
    sgn = 1.0 if side == "long" else -1.0
    adverse = W_l if side == "long" else W_h
    run_adverse = np.minimum.accumulate(adverse, axis=1) if side == "long" else np.maximum.accumulate(adverse, axis=1)
    rows = np.arange(len(e))
    out: dict[tuple[float, float], pd.DataFrame] = {}
    for stop_mult, rr in exits:
        d = stop_mult * a[entries]
        stop = entry - sgn * d
        tp = entry + sgn * rr * d
        if side == "long":
            hit_s, hit_t = W_l <= stop[:, None], W_h >= tp[:, None]
        else:
            hit_s, hit_t = W_h >= stop[:, None], W_l <= tp[:, None]
        k_s = np.where(hit_s.any(axis=1), hit_s.argmax(axis=1), H)
        k_t = np.where(hit_t.any(axis=1), hit_t.argmax(axis=1), H)
        stopped = (k_s <= k_t) & (k_s < H)
        targeted = (k_t < k_s) & (k_t < H)
        k = np.where(stopped, k_s, np.where(targeted, k_t, H - 1))
        # one open trade at a time per configuration and asset
        exit_bar = e + k
        keep = np.ones(len(e), dtype=bool)
        if non_overlapping:
            keep[:], busy_until = False, -1
            for j in range(len(e)):
                if e[j] > busy_until:
                    keep[j] = True
                    busy_until = exit_bar[j]
        gap_open = W_o[rows, k]
        stop_fill = np.minimum(gap_open, stop) if side == "long" else np.maximum(gap_open, stop)
        exit_px = np.where(stopped, stop_fill, np.where(targeted, tp, last_close))
        gross = sgn * (exit_px / entry - 1)
        worst = run_adverse[rows, k]
        mae = np.maximum(0.0, sgn * (entry - worst) / entry)
        reason = np.where(stopped, "stop", np.where(targeted, "take_profit", "time_stop"))
        out[(stop_mult, rr)] = pd.DataFrame({
            "asset": asset, "entry_ts": idx[e], "exit_ts": idx[exit_bar] + tf, "exit_bar_ts": idx[exit_bar],
            "side": side, "entry": entry,
            "exit": exit_px, "stop_frac": d / entry, "gross": gross, "hours": (k + 1) * tf_minutes / 60,
            "mae": mae, "exit_reason": reason})[keep].reset_index(drop=True)
    return out


def trade_stats(net: np.ndarray, years: np.ndarray | None = None, days: np.ndarray | None = None) -> dict:
    """Per-trade stats. With `days`, the t-stat is clustered by day (daily sums), so many simultaneous,
    correlated trades (e.g. BTC/ETH/SOL on the same move) don't count as independent evidence."""
    n = len(net)
    if n == 0:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "expectancy_bps": 0.0, "tstat": 0.0,
                "positive_years": 0, "years": 0}
    wins, losses = net[net > 0].sum(), -net[net <= 0].sum()
    if days is not None:
        sample = pd.Series(net).groupby(np.asarray(days)).sum().to_numpy()
    else:
        sample = net
    sd = sample.std(ddof=1) if len(sample) > 1 else 0.0
    out = {"trades": int(n), "win_rate": float((net > 0).mean()),
           "profit_factor": float(wins / losses) if losses > 0 else math.inf,
           "expectancy_bps": float(net.mean() * 1e4),
           "tstat": float(sample.mean() / sd * math.sqrt(len(sample))) if sd > 0 else 0.0}
    if years is not None and n:
        by_year = pd.Series(net).groupby(years).mean()
        out["positive_years"], out["years"] = int((by_year > 0).sum()), int(len(by_year))
    return out
