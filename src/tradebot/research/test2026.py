"""The one-shot out-of-sample test: frozen strategies on 2026 data, across risk levels and leverage caps.

Every run is appended to research/test_ledger.jsonl. Reported alongside each strategy:
- a random-entry null: the same exits, side and timeframe on random entries at the same frequency per asset,
  under the same costs; a strategy only shows edge if it beats this
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
from .engine import COST_MODELS, simulate, trade_stats
from .families import MAX_HOLD_MINUTES
from .portfolio import price_frame, simulate_portfolio
from .search import HEADLINE, generate, ledger_entries, load_frozen, load_universe, record_test_run

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


def null_expectancy(champion: dict, data: dict[str, pd.DataFrame], trades: pd.DataFrame, cm) -> tuple[float, float]:
    """Mean and 95th percentile of net expectancy (bps) of random entries with the champion's exits, sampled
    uniformly over the whole test window at the champion's per-asset trade count."""
    tf, side = champion["tf"], champion["side"]
    exit_ = tuple(champion["exit"])
    H = MAX_HOLD_MINUTES // tf
    rng = np.random.default_rng(zlib.crc32(champion["id"].encode()))
    counts = trades["asset"].value_counts().to_dict()
    reps = []
    for _ in range(NULL_REPS):
        nets = []
        for asset, n in counts.items():
            key = (id(data), asset, tf)
            if key not in _resampled:
                _resampled[key] = history.resample(data[asset], tf)
            d = _resampled[key]
            pool = np.flatnonzero(d.index >= history.TEST_START)
            if len(pool) == 0:
                continue
            ent = np.sort(rng.choice(pool, size=min(len(pool), 3 * n + 1), replace=False))
            t = simulate(d, ent, side, [exit_], H, asset, tf)[exit_]
            if len(t) > n:     # a random subset, not the earliest n (that would skip the end of the window)
                t = t.iloc[np.sort(rng.choice(len(t), size=n, replace=False))]
            if len(t):
                nets.append(cm.net(t["gross"].to_numpy(), t["hours"].to_numpy(), t["asset"].to_numpy()))
        if nets:
            reps.append(np.concatenate(nets).mean() * 1e4)
    return (float(np.mean(reps)), float(np.percentile(reps, 95))) if reps else (float("nan"), float("nan"))


def run(frozen: dict | None = None, loader=history.load, start_equity: float = 500.0,
        out_dir: Path = RESULTS, ledger: Path | None = None, peeked_files: list[str] | None = None) -> dict:
    frozen = frozen or load_frozen()
    end = pd.Timestamp.now(tz="UTC").floor("D").to_pydatetime()
    data = load_universe(frozen["assets"], history.TEST_START - WARMUP, end, allow_test=True, loader=loader)
    champions = frozen["champions"]
    trades = generate(data, configs=champions, keep_from=history.TEST_START)
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
                net = cm.net(t["gross"].to_numpy(), t["hours"].to_numpy(), t["asset"].to_numpy())
                st = trade_stats(net, days=t["entry_ts"].dt.floor("D").to_numpy())
                h2 = (t["entry_ts"] >= SECOND_HALF).to_numpy()
                null_mean, null_p95 = null_expectancy(c, data, t, cm)
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
            "headline": hl.iloc[0].to_dict() if len(hl) else None}
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
    entry = {"run_at": datetime.now(timezone.utc).isoformat(), "frozen_sha256": frozen["frozen_sha256"],
             "source_sha256": frozen["source_sha256"], "test_end": meta["test_end"],
             "headline_return_pct": meta["headline"]["return_pct"] if meta["headline"] else None}
    record_test_run(entry, ledger) if ledger else record_test_run(entry)
    return {"meta": meta, "strategies": strategies, "grid": grid, "curves": curves}
