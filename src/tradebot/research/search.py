"""Design on 2022-2024, select on 2025, freeze. Nothing here can read 2026 data (history.load enforces it).

Selection rule, fixed before any test data is seen, per cost model and per (family, side):
  1. in-sample (2022-2024): >= 150 trades pooled over the universe, profit factor >= 1.1 after costs,
     positive expectancy in at least 2 of the 3 years; among those, the highest t-stat wins
  2. validation (2025): the champion must show >= 40 trades, profit factor >= 1.1 and positive expectancy
Champions that fail are still frozen (flagged) and reported, so the 2026 test shows everything that was tried.
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
from .engine import COST_MODELS, simulate, trade_stats
from .families import EXITS, FAMILIES, MAX_HOLD_MINUTES, SIDES, TIMEFRAMES, entry_edges, strategy_id

log = logging.getLogger(__name__)

FROZEN_PATH = ROOT / "research" / "frozen.json"
SOURCE_FILES = ["families.py", "engine.py", "history.py", "search.py"]
RULES = {"train_min_trades": 150, "train_min_pf": 1.1, "train_min_positive_years": 2,
         "val_min_trades": 40, "val_min_pf": 1.1, "val_min_expectancy_bps": 0.0}


def source_hash() -> str:
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update((Path(__file__).parent / name).read_bytes())
    return h.hexdigest()


def load_universe(assets: list[str], start: datetime, end: datetime, *, allow_test: bool = False,
                  loader=history.load) -> dict[str, pd.DataFrame]:
    data = {}
    for a in assets:
        df = loader(a, start, end, allow_test=allow_test)
        if len(df):
            data[a] = df
    return data


def generate(data: dict[str, pd.DataFrame], configs: list[dict] | None = None,
             keep_from: datetime | None = None) -> dict[str, pd.DataFrame]:
    """Gross trades per strategy id. `configs` restricts to frozen specs; `keep_from` drops earlier entries
    (indicator warm-up may use earlier bars, trades may not)."""
    wanted = {c["id"]: c for c in configs} if configs else None
    out: dict[str, list[pd.DataFrame]] = defaultdict(list)
    for asset, df5 in data.items():
        for tf in TIMEFRAMES:
            if wanted and not any(c["tf"] == tf for c in wanted.values()):
                continue
            d = history.resample(df5, tf)
            H = MAX_HOLD_MINUTES // tf
            for fam in FAMILIES.values():
                for params in fam.param_sets():
                    for side in SIDES:
                        ids = {ex: strategy_id(fam.name, side, tf, params, ex) for ex in EXITS}
                        exits = [ex for ex, i in ids.items() if not wanted or i in wanted]
                        if not exits:
                            continue
                        entries = entry_edges(fam.signal(d, side, tf, **params))
                        for ex, trades in simulate(d, entries, side, exits, H, asset).items():
                            if keep_from is not None:
                                trades = trades[trades["entry_ts"] >= keep_from]
                            if len(trades):
                                out[ids[ex]].append(trades)
    return {k: pd.concat(v, ignore_index=True).sort_values("entry_ts", ignore_index=True) for k, v in out.items()}


def _parse_id(sid: str) -> dict:
    family, side, tf, params, exit_ = sid.split("|")
    fam = FAMILIES[family]
    p = {}
    for kv in filter(None, params.split(",")):
        k, v = kv.split("=")
        typ = type(fam.grid[k][0])
        p[k] = (v == "True") if typ is bool else typ(v)
    stop, rr = exit_.replace("stop", "").replace("xATR", "").split(",rr")
    return {"id": sid, "family": family, "side": side, "tf": int(tf[:-1]), "params": p,
            "exit": [float(stop), float(rr)], "live_ok": fam.live_ok}


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
                net = cm.net(t["gross"].to_numpy(), t["hours"].to_numpy())
                train = (t["entry_ts"] < history.VALIDATION_START).to_numpy()
                years = t["entry_ts"].dt.year.to_numpy()
                tr = trade_stats(net[train], years[train])
                va = trade_stats(net[~train])
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
                         and va["expectancy_bps"] > RULES["val_min_expectancy_bps"])
            champions.append({**_parse_id(sid), "cost_model": cm.name, "train": tr, "validation": va,
                              "train_edge": eligible_any, "validated": validated, "configs_tried": len(sids)})
    return {"champions": champions, "rules": RULES, "configs_total": len(trades)}


def freeze(doc: dict, assets: list[str], path: Path = FROZEN_PATH) -> dict:
    body = {
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


def run_search(assets: list[str], loader=history.load) -> dict:
    data = load_universe(assets, history.TRAIN_START, history.TEST_START, loader=loader)
    if not data:
        raise RuntimeError("no history cached; run `tradebot research download` first")
    trades = generate(data)
    doc = select(trades)
    return freeze(doc, sorted(data))
