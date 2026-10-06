"""The one-shot out-of-sample test: frozen strategies on 2026 data, across risk levels and leverage caps."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pandas as pd

from ..config import ROOT
from . import history
from .engine import COST_MODELS, trade_stats
from .portfolio import simulate_portfolio
from .search import generate, load_frozen, load_universe

RESULTS = ROOT / "research" / "results" / "2026"
RISKS = [1, 2, 3, 5, 7.5, 10, 15, 20]
LEVERAGES = [1, 2, 3, 5, 10, 20]
WARMUP = timedelta(days=45)


def run(frozen: dict | None = None, loader=history.load, start_equity: float = 500.0,
        out_dir: Path = RESULTS) -> dict:
    frozen = frozen or load_frozen()
    end = pd.Timestamp.now(tz="UTC").floor("D").to_pydatetime()
    data = load_universe(frozen["assets"], history.TEST_START - WARMUP, end, allow_test=True, loader=loader)
    champions = frozen["champions"]
    trades = generate(data, configs=champions, keep_from=history.TEST_START)
    test_days = (end - history.TEST_START).days

    strat_rows, grid_rows, curves = [], [], {}
    for cm_name, cm in COST_MODELS.items():
        champs = [c for c in champions if c["cost_model"] == cm_name]
        frames = []
        for c in champs:
            t = trades.get(c["id"], pd.DataFrame())
            if len(t):
                t = t.assign(strategy=c["id"])
            net = cm.net(t["gross"].to_numpy(), t["hours"].to_numpy()) if len(t) else []
            st = trade_stats(pd.Series(net, dtype=float).to_numpy())
            row = {"cost_model": cm_name, "strategy": c["id"], "family": c["family"], "side": c["side"],
                   "tf": c["tf"], "validated": c["validated"], "train_edge": c["train_edge"],
                   "live_ok": c["live_ok"],
                   **{f"train_{k}": v for k, v in c["train"].items() if k in ("trades", "profit_factor", "expectancy_bps")},
                   **{f"val_{k}": v for k, v in c["validation"].items() if k in ("trades", "profit_factor", "expectancy_bps")},
                   **{f"test_{k}": v for k, v in st.items() if k in ("trades", "win_rate", "profit_factor", "expectancy_bps")}}
            if len(t):
                for risk in RISKS:
                    for lev in ([1] if cm.max_leverage == 1 else [1, 3, 10, 20]):
                        r = simulate_portfolio(t, cm, risk, lev, start_equity)
                        row[f"ret_r{risk}_x{lev}"] = r.return_pct
                        row[f"dd_r{risk}_x{lev}"] = r.max_drawdown_pct
            strat_rows.append(row)
            if len(t):
                frames.append((c, t))
        # Combined portfolios: every validated strategy (the honest one), and for reference every champion
        # that showed an in-sample edge even if 2025 rejected it.
        sets = {"validated": [t for c, t in frames if c["validated"]],
                "train_edge_reference": [t for c, t in frames if c["train_edge"]]}
        for set_name, members in sets.items():
            if not members:
                continue
            combo = pd.concat(members, ignore_index=True)
            for risk in RISKS:
                for lev in ([1] if cm.max_leverage == 1 else LEVERAGES):
                    for guard in (False, True):
                        r = simulate_portfolio(combo, cm, risk, lev, start_equity, guardrails=guard)
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
    pd.DataFrame({k: v for k, v in curves.items()}).to_csv(out_dir / "equity_curves.csv")
    meta = {"frozen_sha256": frozen["frozen_sha256"], "frozen_created_at": frozen["created_at"],
            "test_start": history.TEST_START.date().isoformat(), "test_end": end.date().isoformat(),
            "test_days": test_days, "assets": sorted(data), "start_equity": start_equity,
            "configs_searched": frozen["configs_total"]}
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=1))
    return {"meta": meta, "strategies": strategies, "grid": grid, "curves": curves}
