"""Design on 2022-2024, select on 2025, freeze. Nothing here can read 2026 data (history.load enforces it).

Selection rule, fixed before any test data is seen, per cost model and per (family, side):
  1. in-sample (2022-2024): >= 150 non-overlapping trades pooled over the universe, profit factor >= 1.1 after
     costs, positive expectancy in at least 2 of the 3 years; among those, the highest day-clustered t-stat wins
  2. validation (2025): >= 100 trades, profit factor >= 1.1, positive expectancy AND day-clustered t-stat >= 2
     (a zero-edge strategy passes this rarely; a PF/expectancy-only gate passes 20-40% of the time)
Champions that fail are still frozen (flagged) and reported, so the 2026 test shows everything that was tried.

Integrity: the freeze hashes the champions AND all code that affects results. Every 2026 run is appended to
research/test_ledger.jsonl; once 2026 has been viewed, a new search is refused unless explicitly marked
contaminated, and that mark is carried into the report.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import ROOT
from . import history
from .engine import COST_MODELS, funding_paid, simulate, trade_stats
from .families import EXITS, FAMILIES, SIDES, TIMEFRAMES, build_context, entry_edges, signal_for, strategy_id

log = logging.getLogger(__name__)

FROZEN_PATH = ROOT / "research" / "frozen.json"
LEDGER_PATH = ROOT / "research" / "test_ledger.jsonl"
SOURCE_FILES = ["families.py", "engine.py", "history.py", "search.py", "portfolio.py", "test2026.py",
                "../indicators.py"]
RULES = {"train_min_trades": 150, "train_min_pf": 1.1, "train_min_positive_years": 2,
         "val_min_trades": 100, "val_min_pf": 1.1, "val_min_expectancy_bps": 0.0, "val_min_tstat": 3.0}
# Pre-registered headline result (chosen before testing): the venue the bot trades, validated strategies only,
# base risk, no leverage, with circuit breakers. Every other cell of the report is sensitivity analysis.
HEADLINE = {"cost_model": "alpaca_spot", "portfolio": "validated", "risk_pct": 1, "leverage": 1, "guardrails": True}


def source_hash() -> str:
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update((Path(__file__).parent / name).resolve().read_bytes())
    return h.hexdigest()


def ledger_entries(path: Path = LEDGER_PATH) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def record_test_run(entry: dict, path: Path = LEDGER_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def load_universe(assets: list[str], start: datetime, end: datetime, *, allow_test: bool = False,
                  loader=history.load) -> dict[str, pd.DataFrame]:
    data = {}
    for a in assets:
        df = loader(a, start, end, allow_test=allow_test)
        if len(df):
            data[a] = df
    return data


def simulate_exits(d: pd.DataFrame, entries, side: str, exits: list[tuple], asset: str, tf: int) -> dict:
    """Run simulate once per distinct hold time; keys are full (stop, rr, hold_h) exit tuples."""
    out = {}
    for hold in sorted({ex[2] for ex in exits}):
        group = [ex for ex in exits if ex[2] == hold]
        H = max(1, hold * 60 // tf)
        res = simulate(d, entries, side, [(ex[0], ex[1]) for ex in group], H, asset, tf)
        for ex in group:
            out[ex] = res[(ex[0], ex[1])]
    return out


def generate(data: dict[str, pd.DataFrame], configs: list[dict] | None = None,
             keep_from: datetime | None = None, funding: dict[str, pd.Series] | None = None) -> dict[str, pd.DataFrame]:
    """Gross trades per strategy id. `configs` restricts to frozen specs; `keep_from` drops earlier entries
    (indicator warm-up may use earlier bars, trades may not). `funding` feeds the funding-rate family."""
    wanted = {c["id"]: c for c in configs} if configs else None
    out: dict[str, list[pd.DataFrame]] = defaultdict(list)
    for tf in TIMEFRAMES:
        if wanted and not any(c["tf"] == tf for c in wanted.values()):
            continue
        ctx = build_context(data, tf, funding)
        for asset, df5 in data.items():
            d = history.resample(df5, tf)
            for fam in FAMILIES.values():
                for params in fam.param_sets():
                    for side in SIDES:
                        ids = {ex: strategy_id(fam.name, side, tf, params, ex) for ex in EXITS
                               if ex[2] * 60 >= tf}
                        exits = [ex for ex, i in ids.items() if not wanted or i in wanted]
                        if not exits:
                            continue
                        entries = entry_edges(signal_for(fam, d, side, tf, params, ctx, asset))
                        for ex, trades in simulate_exits(d, entries, side, exits, asset, tf).items():
                            if keep_from is not None:
                                trades = trades[trades["entry_ts"] >= keep_from]
                            if len(trades):
                                rates = (funding or {}).get(asset)
                                out[ids[ex]].append(trades.assign(funding=funding_paid(trades, rates)))
    return {k: pd.concat(v, ignore_index=True).sort_values("entry_ts", ignore_index=True) for k, v in out.items()}


def load_funding(assets: list[str], start: datetime, end: datetime, *, allow_test: bool = False,
                 loader=history.load_funding) -> dict[str, pd.Series]:
    out = {}
    for a in assets:
        s = loader(a, start, end, allow_test=allow_test)
        if len(s):
            out[a] = s
    return out


def _parse_id(sid: str) -> dict:
    family, side, tf, params, exit_ = sid.split("|")
    fam = FAMILIES[family]
    p = {}
    for kv in filter(None, params.split(",")):
        k, v = kv.split("=")
        typ = type(fam.grid[k][0])
        p[k] = (v == "True") if typ is bool else typ(v)
    stop, rest = exit_.replace("stop", "").replace("xATR", "").split(",rr")
    rr, hold = rest.split(",hold")
    return {"id": sid, "family": family, "side": side, "tf": int(tf[:-1]), "params": p,
            "exit": [float(stop), float(rr), int(hold.rstrip("h"))], "live_ok": fam.live_ok}


def select(trades: dict[str, pd.DataFrame]) -> dict:
    """Apply the fixed selection rule; returns the frozen-candidate document (not yet written)."""
    champions = []
    by_family_side: dict[tuple[str, str], list[str]] = defaultdict(list)
    for sid in trades:
        fam, side = sid.split("|")[:2]
        by_family_side[(fam, side)].append(sid)
    for cm in COST_MODELS.values():
        for (fam, side), sids in sorted(by_family_side.items()):
            if side == "short" and not cm.allow_short:
                continue
            scored = []
            for sid in sids:
                t = trades[sid]
                net = cm.net_trades(t)
                train = (t["entry_ts"] < history.VALIDATION_START).to_numpy()
                years = t["entry_ts"].dt.year.to_numpy()
                days = t["entry_ts"].dt.floor("D").to_numpy()
                tr = trade_stats(net[train], years[train], days[train])
                va = trade_stats(net[~train], days=days[~train])
                eligible = (tr["trades"] >= RULES["train_min_trades"] and tr["profit_factor"] >= RULES["train_min_pf"]
                            and tr["positive_years"] >= RULES["train_min_positive_years"])
                scored.append((eligible, tr["tstat"], sid, tr, va))
            if not scored:
                continue
            eligible_any = any(s[0] for s in scored)
            best = max((s for s in scored if s[0] == eligible_any), key=lambda s: s[1])
            _, _, sid, tr, va = best
            validated = (eligible_any and va["trades"] >= RULES["val_min_trades"]
                         and va["profit_factor"] >= RULES["val_min_pf"]
                         and va["expectancy_bps"] > RULES["val_min_expectancy_bps"]
                         and va["tstat"] >= RULES["val_min_tstat"])
            champions.append({**_parse_id(sid), "cost_model": cm.name, "train": tr, "validation": va,
                              "train_edge": eligible_any, "validated": validated, "configs_tried": len(sids)})
    return {"champions": champions, "rules": RULES, "configs_total": len(trades)}


def freeze(doc: dict, assets: list[str], path: Path = FROZEN_PATH, contaminated: bool = False,
           ledger: Path = LEDGER_PATH) -> dict:
    prior_views = len(ledger_entries(ledger))
    if prior_views and not contaminated:
        raise RuntimeError(f"2026 has already been tested {prior_views} time(s) (research/test_ledger.jsonl); a new "
                           "search would be fit with knowledge of 2026. Pass contaminated=True to proceed anyway.")
    body = {
        "contaminated": bool(prior_views), "prior_2026_views": prior_views, "headline": HEADLINE,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "train": [history.TRAIN_START.date().isoformat(), history.VALIDATION_START.date().isoformat()],
        "validation": [history.VALIDATION_START.date().isoformat(), history.TEST_START.date().isoformat()],
        "test_start": history.TEST_START.date().isoformat(),
        "assets": assets, "cost_models": {k: vars(v) for k, v in COST_MODELS.items()},
        "source_sha256": source_hash(), **doc,
    }
    canonical = json.dumps(body, sort_keys=True, default=str)
    body["frozen_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=1, default=str))
    return body


def load_frozen(path: Path = FROZEN_PATH, check_source: bool = True) -> dict:
    body = json.loads(path.read_text())
    digest = body.pop("frozen_sha256")
    if hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest() != digest:
        raise RuntimeError("frozen.json was modified after freezing")
    if check_source and body["source_sha256"] != source_hash():
        raise RuntimeError("research code changed since freezing; re-run the search before testing")
    body["frozen_sha256"] = digest
    return body


def run_search(assets: list[str], loader=history.load, contaminated: bool = False,
               funding_loader=history.load_funding) -> dict:
    data = load_universe(assets, history.TRAIN_START, history.TEST_START, loader=loader)
    if not data:
        raise RuntimeError("no history cached; run `tradebot research download` first")
    funding = load_funding(sorted(data), history.TRAIN_START, history.TEST_START, loader=funding_loader)
    trades = generate(data, funding=funding)
    doc = select(trades)
    return freeze(doc, sorted(data), contaminated=contaminated)
