"""Portfolio simulation of frozen strategies on the test period, marked to market on a 5-minute clock.

Sizing: notional = risk% x equity / stop distance, capped so total open notional <= leverage x equity (and the
venue's max leverage). Equity here is MARK-TO-MARKET: open positions are valued at the latest 5m close, so
drawdowns, the daily stop and the drawdown halt see open losses exactly like the live bot does.
One position per asset at a time (the live bot rejects a second position on an asset it already holds).

Liquidation (leverage > 1): each position posts isolated margin = notional / leverage; if its adverse excursion
reaches 1/leverage - 0.5% maintenance before the exit, the margin is lost plus a 0.5%-of-notional fee, and an
open position's unrealized loss can never exceed its margin.

`guardrails=True` applies the live bot's circuit breakers only (not its heat/notional caps or calibrated
sizing): no new entries once mark-to-market equity is down 5% since UTC midnight, and at a 15% drawdown from
the peak every position is closed at the last 5m close (plus costs) and trading stops for good.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .engine import CostModel

MAINTENANCE = 0.005
LIQ_FEE = 0.005
MIN_NOTIONAL = 10.0


@dataclass
class PortfolioResult:
    final_equity: float
    return_pct: float
    max_drawdown_pct: float          # mark-to-market, 5m closes
    trades: int
    skipped: int
    win_rate: float
    profit_factor: float
    sharpe_daily: float
    worst_trade_pct: float
    liquidations: int
    ruined: bool
    halted_at: str
    equity: pd.Series                # daily mark-to-market equity

    def row(self) -> dict:
        d = dict(vars(self))
        d.pop("equity")
        return d


def price_frame(data: dict[str, pd.DataFrame], start) -> pd.DataFrame:
    """5m closes per asset on a common clock (index = bar open time), forward-filled."""
    closes = pd.DataFrame({a: df["close"] for a, df in data.items()})
    closes = closes[closes.index >= start].sort_index()
    return closes.ffill()


def simulate_portfolio(trades: pd.DataFrame, cm: CostModel, risk_pct: float, leverage: float,
                       prices: pd.DataFrame, start_equity: float = 500.0, max_positions: int = 8,
                       guardrails: bool = False, daily_loss_pct: float = 5.0,
                       max_dd_pct: float = 15.0) -> PortfolioResult:
    """trades: strategy, asset, entry_ts, exit_ts (bar close), side, entry, stop_frac, gross, hours, mae.
    prices: 5m closes per asset (columns) indexed by bar open time."""
    lev = max(1.0, min(leverage, cm.max_leverage))
    t = trades if cm.allow_short else trades[trades["side"] == "long"]
    t = t.sort_values(["entry_ts", "strategy", "asset"], kind="stable").reset_index(drop=True)
    clock = prices.index
    closes = prices.to_numpy(dtype=float)
    col = {a: j for j, a in enumerate(prices.columns)}
    liq_dist = 1 / lev - MAINTENANCE if lev > 1 else math.inf

    net = cm.net(t["gross"].to_numpy(), t["hours"].to_numpy(), t["asset"].to_numpy()) if len(t) else np.array([])
    rt_cost = cm.round_trip_bps(t["asset"].to_numpy()) / 1e4 if len(t) else np.array([])
    entry_pos = clock.searchsorted(t["entry_ts"].to_numpy()) if len(t) else np.array([], dtype=int)
    exit_pos = clock.searchsorted(t["exit_ts"].to_numpy()) if len(t) else np.array([], dtype=int)
    by_step: dict[int, list[int]] = {}
    for i, p in enumerate(entry_pos):
        by_step.setdefault(int(p), []).append(i)

    realized = start_equity
    open_pos: list[dict] = []
    pnls, worst, liqs, skipped = [], 0.0, 0, 0
    peak, max_dd, ruined, halted_at = start_equity, 0.0, False, ""
    day, day_start = None, start_equity
    mtm_ts, mtm_eq = [], []

    def unrealized(p, step) -> float:
        """Open P&L at a 5m close, net of the entry-side half of the round-trip cost."""
        if step < 0:
            return -0.5 * p["notional"] * p["rt_cost"]
        c = closes[step, p["col"]]
        if not np.isfinite(c):
            return -0.5 * p["notional"] * p["rt_cost"]
        u = p["sign"] * p["notional"] * (c / p["entry"] - 1) - 0.5 * p["notional"] * p["rt_cost"]
        return max(u, -p["margin"]) if lev > 1 else u

    def close_now(p, step):
        """Forced close (drawdown halt / ruin) at the last close. It can never beat an outcome that already
        happened: a liquidation, or a stop/target inside an exit bar that is in progress."""
        mtm_exit = unrealized(p, step) - 0.5 * p["notional"] * p["rt_cost"]
        if p["liq"] or clock[i] >= p["exit_bar_ts"]:
            mtm_exit = min(mtm_exit, p["pnl"])
        realize(p, mtm_exit)

    def realize(p, pnl):
        nonlocal realized, worst, liqs
        open_pos.remove(p)
        realized += pnl
        pnls.append(pnl)
        worst = min(worst, p["worst"])
        liqs += p["liq"]

    steps = sorted(set(by_step) | {len(clock) - 1}) if len(clock) else []
    i = steps[0] if steps else 0
    next_entry = iter(steps)
    target = next(next_entry, None)
    while i < len(clock):
        # 1. exits whose bar has closed by now; positions a 5m close has pushed past liquidation are gone
        for p in [p for p in open_pos if p["exit_pos"] <= i]:
            realize(p, p["pnl"])
        if lev > 1 and i > 0:
            for p in list(open_pos):
                c = closes[i - 1, p["col"]]
                if np.isfinite(c) and p["sign"] * (c / p["entry"] - 1) <= -(1 / lev - MAINTENANCE):
                    p["liq"] = 1
                    realize(p, min(p["pnl"], -(p["margin"] + LIQ_FEE * p["notional"])))
        # 2. mark to market on the last completed 5m close
        equity = realized + sum(unrealized(p, i - 1) for p in open_pos)
        d = clock[i].floor("D")
        if d != day:
            day, day_start = d, equity
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100 if peak > 0 else 100.0
        max_dd = max(max_dd, dd)
        mtm_ts.append(clock[i])
        mtm_eq.append(equity)
        if equity <= start_equity * 0.01:
            for p in list(open_pos):
                close_now(p, i - 1)
            ruined = True
            skipped += sum(len(v) for k, v in by_step.items() if k >= i)
            break
        if guardrails and not halted_at and dd >= max_dd_pct:
            halted_at = str(clock[i])
            for p in list(open_pos):
                close_now(p, i - 1)
        # 3. entries at this bar's open
        for j in by_step.get(i, []):
            r = t.iloc[j]
            blocked = (guardrails and (halted_at or (equity / day_start - 1) * 100 <= -daily_loss_pct))
            if (blocked or len(open_pos) >= max_positions or any(p["asset"] == r.asset for p in open_pos)
                    or r.asset not in col):
                skipped += 1
                continue
            room = lev * equity - sum(p["notional"] for p in open_pos)
            notional = min(risk_pct / 100 * equity / r.stop_frac, room)
            if notional < MIN_NOTIONAL:
                skipped += 1
                continue
            liquidated = lev > 1 and r.mae >= liq_dist
            margin = notional / lev
            pnl = -(margin + LIQ_FEE * notional) if liquidated else notional * net[j]
            open_pos.append({"asset": r.asset, "col": col[r.asset], "sign": 1.0 if r.side == "long" else -1.0,
                             "entry": r.entry, "notional": notional, "margin": margin, "pnl": pnl,
                             "exit_pos": int(exit_pos[j]), "rt_cost": rt_cost[j], "liq": int(liquidated),
                             "exit_bar_ts": getattr(r, "exit_bar_ts", r.exit_ts),
                             "worst": -min(r.mae, liq_dist) * notional / max(equity, 1e-9) * 100})
        # 4. advance: bar by bar while exposed, otherwise jump to the next entry
        if open_pos:
            i += 1
        else:
            while target is not None and target <= i:
                target = next(next_entry, None)
            i = target if target is not None else len(clock)
    for p in list(open_pos):               # trades running past the data end settle at their simulated exit
        realize(p, p["pnl"])
    if not ruined:
        skipped += sum(len(v) for k, v in by_step.items() if k >= len(clock))   # entries after the price data
    final = realized

    curve = pd.Series(mtm_eq + [final], index=pd.DatetimeIndex(mtm_ts + [clock[-1] if len(clock) else pd.Timestamp.now(tz="UTC")]))
    curve = curve.groupby(level=0).last()
    daily = curve.clip(lower=0).resample("1D").last().ffill()
    rets = daily.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * math.sqrt(365)) if len(rets) > 2 and rets.std() > 0 else 0.0
    arr = np.array(pnls)
    gw, gl = arr[arr > 0].sum(), -arr[arr <= 0].sum()
    return PortfolioResult(
        final_equity=round(final, 2), return_pct=round((final / start_equity - 1) * 100, 2),
        max_drawdown_pct=round(max_dd, 2), trades=len(pnls), skipped=skipped,
        win_rate=round(float((arr > 0).mean()), 3) if len(arr) else 0.0,
        profit_factor=round(float(gw / gl), 3) if gl > 0 else (math.inf if gw > 0 else 0.0),
        sharpe_daily=round(sharpe, 2), worst_trade_pct=round(worst, 2), liquidations=liqs,
        ruined=ruined, halted_at=halted_at, equity=daily)
