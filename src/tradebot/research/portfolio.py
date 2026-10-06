"""Portfolio simulation of frozen strategies on the test period at a given risk per trade and leverage cap.

Sizing: notional = risk% x equity / stop distance, capped so total open notional <= leverage x equity
(and per-venue max leverage). Spot (1x) can therefore never risk more than the stop distance allows.
Liquidation (leverage > 1): each position posts margin = notional / leverage; if the adverse excursion reaches
1/leverage - 0.5% maintenance before the exit, the margin is lost (plus a 0.5%-of-notional liquidation fee).
Equity is realized equity (positions marked at exit), so drawdowns are slightly understated vs mark-to-market;
the worst intra-trade loss is reported separately.

`guardrails=True` applies the live bot's circuit breakers: no new entries after a 5% realized daily loss
(until the next UTC day) and a permanent halt at a 15% drawdown from peak.
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
    max_drawdown_pct: float
    trades: int
    skipped: int
    win_rate: float
    profit_factor: float
    sharpe_daily: float
    worst_trade_pct: float
    liquidations: int
    ruined: bool
    halted_at: str
    equity: pd.Series

    def row(self) -> dict:
        d = dict(vars(self))
        d.pop("equity")
        return d


def simulate_portfolio(trades: pd.DataFrame, cm: CostModel, risk_pct: float, leverage: float,
                       start_equity: float = 500.0, max_positions: int = 8, guardrails: bool = False,
                       daily_loss_pct: float = 5.0, max_dd_pct: float = 15.0) -> PortfolioResult:
    """trades: columns strategy, asset, entry_ts, exit_ts, side, stop_frac, gross, hours, mae."""
    lev = max(1.0, min(leverage, cm.max_leverage))
    t = trades if cm.allow_short else trades[trades["side"] == "long"]
    t = t.sort_values("entry_ts").reset_index(drop=True)
    net = cm.net(t["gross"].to_numpy(), t["hours"].to_numpy())
    equity, peak = start_equity, start_equity
    open_pos: list[dict] = []
    curve_ts, curve_eq = [t["entry_ts"].iloc[0] if len(t) else pd.Timestamp.now(tz="UTC")], [equity]
    pnls, worst, liqs, skipped = [], 0.0, 0, 0
    max_dd, ruined, halted_at = 0.0, False, ""
    day, day_start = None, equity
    liq_dist = 1 / lev - MAINTENANCE if lev > 1 else math.inf

    def close_until(ts):
        nonlocal equity, peak, max_dd, ruined, worst, liqs
        for p in sorted([p for p in open_pos if p["exit_ts"] <= ts], key=lambda p: p["exit_ts"]):
            open_pos.remove(p)
            equity += p["pnl"]
            pnls.append(p["pnl"])
            worst = min(worst, p["worst"])
            liqs += p["liq"]
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak * 100 if peak > 0 else 100.0)
            curve_ts.append(p["exit_ts"])
            curve_eq.append(equity)
            if equity <= start_equity * 0.01:
                ruined = True

    for i, r in enumerate(t.itertuples(index=False)):
        if ruined:
            break
        close_until(r.entry_ts)
        if ruined:
            break
        d = r.entry_ts.floor("D")
        if d != day:
            day, day_start = d, equity
        if guardrails:
            if halted_at:
                skipped += 1
                continue
            if (peak - equity) / peak * 100 >= max_dd_pct:
                halted_at = str(r.entry_ts)
                skipped += 1
                continue
            if (equity / day_start - 1) * 100 <= -daily_loss_pct:
                skipped += 1
                continue
        if len(open_pos) >= max_positions or any(p["key"] == (r.strategy, r.asset) for p in open_pos):
            skipped += 1
            continue
        room = lev * equity - sum(p["notional"] for p in open_pos)
        notional = min(risk_pct / 100 * equity / r.stop_frac, room)
        if notional < MIN_NOTIONAL:
            skipped += 1
            continue
        liquidated = lev > 1 and r.mae >= liq_dist
        if liquidated:
            pnl = -(notional / lev) - LIQ_FEE * notional
        else:
            pnl = notional * net[i]
        open_pos.append({"key": (r.strategy, r.asset), "exit_ts": r.exit_ts, "notional": notional, "pnl": pnl,
                         "worst": (-min(r.mae, liq_dist) * notional) / equity * 100, "liq": int(liquidated)})
    close_until(pd.Timestamp.max.tz_localize("UTC"))

    curve = pd.Series(curve_eq, index=pd.DatetimeIndex(curve_ts)).groupby(level=0).last()
    daily = curve.resample("1D").last().ffill()
    rets = daily.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * math.sqrt(365)) if len(rets) > 2 and rets.std() > 0 else 0.0
    arr = np.array(pnls)
    gw, gl = arr[arr > 0].sum(), -arr[arr <= 0].sum()
    return PortfolioResult(
        final_equity=round(equity, 2), return_pct=round((equity / start_equity - 1) * 100, 2),
        max_drawdown_pct=round(max_dd, 2), trades=len(pnls), skipped=skipped,
        win_rate=round(float((arr > 0).mean()), 3) if len(arr) else 0.0,
        profit_factor=round(float(gw / gl), 3) if gl > 0 else (math.inf if gw > 0 else 0.0),
        sharpe_daily=round(sharpe, 2), worst_trade_pct=round(worst, 2), liquidations=liqs,
        ruined=ruined, halted_at=halted_at, equity=curve)
