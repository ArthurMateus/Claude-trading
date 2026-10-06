"""The one-shot out-of-sample test: frozen strategies on 2026 data, across risk levels and leverage caps.

Every run is appended to research/test_ledger.jsonl. Reported alongside each strategy:
- a timing null: the strategy's own entries shifted together by a random number of whole days, under the same
  exits and costs; a strategy only shows edge if it beats this
- a split into Jan-Jun 2026 and Jul 2026-today: the strategy families were authored by a model whose training
  data reaches mid-2026, so the second half is the cleaner out-of-sample window
"""
from __future__ import annotations

import json
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import ROOT
from . import history
from .engine import COST_MODELS, funding_paid, simulate, trade_stats
from .portfolio import price_frame, simulate_portfolio
from .search import (HEADLINE, generate, ledger_entries, load_frozen, load_funding, load_universe,
                     record_test_run)

RESULTS = ROOT / "research" / "results" / "2026"
RISKS = [1, 2, 3, 5, 7.5, 10, 15, 20]
LEVERAGES = [1, 2, 3, 5, 10, 20]
STRATEGY_RISKS = [1, 5, 10, 20]
STRATEGY_LEVERAGES = [1, 3, 10]
WARMUP = timedelta(days=120)            # EMA200 on 4h bars needs months of history to forget its seed
SECOND_HALF = datetime(2026, 7, 1, tzinfo=timezone.utc)
NULL_REPS = 200
MIN_TEST_TRADES = 40


_resampled: dict[tuple[int, str, int], pd.DataFrame] = {}


def null_expectancy(champion: dict, data: dict[str, pd.DataFrame], trades: pd.DataFrame, cm,
                    funding: dict[str, pd.Series] | None = None) -> tuple[float, float]:
    """Mean and 95th percentile of net expectancy (bps) when the champion's own entries are all shifted by the
    same random whole number of days (wrapping within the test window) and re-priced with its exits. This keeps
    the trade count per asset, time of day and cross-asset clustering, and removes only the timing skill."""
    tf, side = champion["tf"], champion["side"]
    stop, rr, hold = champion["exit"]
    exit_ = (stop, rr)
    H = max(1, int(hold) * 60 // tf)
    start = pd.Timestamp(history.TEST_START)
    end = max(df.index.max() for df in data.values()) + pd.Timedelta(minutes=5)
    days = int((end - start) / pd.Timedelta(days=1))
    per_day = 24 * 60 // tf
    n_bars = days * per_day
    if days < 2 or not len(trades):
        return float("nan"), float("nan")
    rng = np.random.default_rng(zlib.crc32(champion["id"].encode()))
    offsets = rng.integers(1, days, size=NULL_REPS) * per_day
    sums, counts = np.zeros(NULL_REPS), np.zeros(NULL_REPS)
    bar = pd.Timedelta(minutes=tf)
    for asset, g in trades.groupby("asset"):
        key = (id(data), asset, tf)
        if key not in _resampled:
            _resampled[key] = history.resample(data[asset], tf)
        d = _resampled[key]
        # A trade from every possible entry bar of the window, on a regular grid of bar positions.
        t = simulate(d, np.flatnonzero(d.index >= start - bar), side, [exit_], H, asset, tf,
                     non_overlapping=False)[exit_]
        t = t[t["entry_ts"] >= start]
        if not len(t):
            continue
        t = t.assign(funding=funding_paid(t, (funding or {}).get(asset)))
        grid = np.full(n_bars, np.nan)
        pos = ((t["entry_ts"] - start) // bar).to_numpy(dtype=np.int64)
        ok = pos < n_bars
        grid[pos[ok]] = cm.net_trades(t)[ok]
        own = ((g["entry_ts"] - start) // bar).to_numpy(dtype=np.int64)
        vals = grid[(own[None, :] + offsets[:, None]) % n_bars]
        sums += np.nansum(vals, axis=1)
        counts += np.isfinite(vals).sum(axis=1)
    valid = counts > 0
    if not valid.any():
        return float("nan"), float("nan")
    reps = sums[valid] / counts[valid] * 1e4
    return float(np.mean(reps)), float(np.percentile(reps, 95))


def run(frozen: dict | None = None, loader=history.load, start_equity: float = 500.0,
        out_dir: Path = RESULTS, ledger: Path | None = None, peeked_files: list[str] | None = None,
        funding_loader=history.load_funding) -> dict:
    frozen = frozen or load_frozen()
    end = pd.Timestamp.now(tz="UTC").floor("D").to_pydatetime()
    data = load_universe(frozen["assets"], history.TEST_START - WARMUP, end, allow_test=True, loader=loader)
    funding = load_funding(sorted(data), history.TEST_START - WARMUP, end, allow_test=True, loader=funding_loader)
    champions = frozen["champions"]
    trades = generate(data, configs=champions, keep_from=history.TEST_START, funding=funding)
    prices = price_frame(data, history.TEST_START)
    test_days = (end - history.TEST_START).days

    strat_rows, grid_rows, curves = [], [], {}
    for cm_name, cm in COST_MODELS.items():
        frames = []
        for c in [c for c in champions if c["cost_model"] == cm_name]:
            t = trades.get(c["id"], pd.DataFrame())
            row = {"cost_model": cm_name, "strategy": c["id"], "family": c["family"], "side": c["side"],
                   "tf": c["tf"], "validated": c["validated"], "train_edge": c["train_edge"], "live_ok": c["live_ok"],
                   "train_trades": c["train"]["trades"], "train_pf": c["train"]["profit_factor"],
                   "train_t": c["train"]["tstat"], "val_trades": c["validation"]["trades"],
                   "val_pf": c["validation"]["profit_factor"], "val_t": c["validation"]["tstat"]}
            if len(t):
                t = t.assign(strategy=c["id"])
                net = cm.net_trades(t)
                st = trade_stats(net, days=t["entry_ts"].dt.floor("D").to_numpy())
                h2 = (t["entry_ts"] >= SECOND_HALF).to_numpy()
                null_mean, null_p95 = null_expectancy(c, data, t, cm, funding)
                row.update({"test_trades": st["trades"], "test_win_rate": st["win_rate"], "test_pf": st["profit_factor"],
                            "test_exp_bps": st["expectancy_bps"], "test_t": st["tstat"],
                            "exp_bps_jan_jun": float(net[~h2].mean() * 1e4) if (~h2).any() else float("nan"),
                            "exp_bps_jul_now": float(net[h2].mean() * 1e4) if h2.any() else float("nan"),
                            "null_exp_bps": null_mean, "null_p95_bps": null_p95,
                            "beats_null": bool(st["expectancy_bps"] > null_p95),
                            "noise": bool(st["trades"] < MIN_TEST_TRADES)})
                for risk in STRATEGY_RISKS:
                    for lev in ([1] if cm.max_leverage == 1 else STRATEGY_LEVERAGES):
                        r = simulate_portfolio(t, cm, risk, lev, prices, start_equity)
                        row[f"ret_r{risk}_x{lev}"] = r.return_pct
                        row[f"dd_r{risk}_x{lev}"] = r.max_drawdown_pct
                frames.append((c, t))
            else:
                row.update({"test_trades": 0, "noise": True})
            strat_rows.append(row)
        # Combined portfolios: validated strategies (the honest one), and for reference every champion that
        # showed an in-sample edge even if 2025 rejected it.
        sets = {"validated": [t for c, t in frames if c["validated"]],
                "train_edge_reference": [t for c, t in frames if c["train_edge"]]}
        for set_name, members in sets.items():
            if not members:
                continue
            combo = pd.concat(members, ignore_index=True)
            for risk in RISKS:
                for lev in ([1] if cm.max_leverage == 1 else LEVERAGES):
                    for guard in (False, True):
                        r = simulate_portfolio(combo, cm, risk, lev, prices, start_equity, guardrails=guard)
                        grid_rows.append({"cost_model": cm_name, "portfolio": set_name, "risk_pct": risk,
                                          "leverage": lev, "guardrails": guard, "strategies": len(members),
                                          **r.row()})
                        if not guard:
                            curves[f"{cm_name}|{set_name}|r{risk}|x{lev}"] = r.equity

    out_dir.mkdir(parents=True, exist_ok=True)
    strategies = pd.DataFrame(strat_rows)
    grid = pd.DataFrame(grid_rows)
    strategies.to_csv(out_dir / "strategies.csv", index=False)
    grid.to_csv(out_dir / "portfolio_grid.csv", index=False)
    pd.DataFrame(curves).to_csv(out_dir / "equity_curves.csv")
    headline = HEADLINE
    hl = grid[(grid.get("cost_model") == headline["cost_model"]) & (grid.get("portfolio") == headline["portfolio"])
              & (grid.get("risk_pct") == headline["risk_pct"]) & (grid.get("leverage") == headline["leverage"])
              & (grid.get("guardrails") == headline["guardrails"])] if len(grid) else pd.DataFrame()
    prior = ledger_entries(ledger) if ledger else ledger_entries()
    meta = {"frozen_sha256": frozen["frozen_sha256"], "frozen_created_at": frozen["created_at"],
            "source_sha256": frozen["source_sha256"], "contaminated": frozen.get("contaminated", False),
            "test_start": history.TEST_START.date().isoformat(), "test_end": end.date().isoformat(),
            "test_days": test_days, "assets": sorted(data), "start_equity": start_equity,
            "configs_searched": frozen["configs_total"], "prior_2026_runs": len(prior),
            "test_files_cached_before_freeze": peeked_files or [], "headline_spec": headline,
            "beats_null": int(strategies.get("beats_null", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()),
            "beats_null_expected_by_chance": round(0.05 * int((strategies.get("test_trades", pd.Series(dtype=float)) > 0).sum()), 1),
            "funding_last_settlement": {a: str(f.index.max()) for a, f in funding.items()},
            "val_false_passes_expected": round(0.00135 * len(champions), 2),
            "headline": hl.iloc[0].to_dict() if len(hl) else None}
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
    entry = {"run_at": datetime.now(timezone.utc).isoformat(), "frozen_sha256": frozen["frozen_sha256"],
             "source_sha256": frozen["source_sha256"], "test_end": meta["test_end"],
             "headline_return_pct": meta["headline"]["return_pct"] if meta["headline"] else None}
    record_test_run(entry, ledger) if ledger else record_test_run(entry)
    return {"meta": meta, "strategies": strategies, "grid": grid, "curves": curves}
