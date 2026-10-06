"""SQLite trade journal: the bot's memory and the learning loop's training data.

Tables
- trades     one row per trade with every field from the spec (timestamp, asset, entry, exit, size,
             agents agreed/disagreed, confidence, reason, expected/actual return, market conditions,
             slippage, result) plus audit fields (stops, fees, R-multiple, post-trade lessons)
- decisions  every candidate the pipeline considered, including rejections and why
- events     kill-switch trips, errors, halts, resets
- equity     equity snapshots (drawdown / daily-loss source of truth)
- llm_usage  token usage and cost per call (budget enforcement)
- state      small key/value store (halt state, validated setups, agent weights)
"""
from __future__ import annotations

import csv
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .contracts import TradeRecord, utcnow

_JSON_FIELDS = {"agents_agreed", "agents_disagreed", "agent_signals", "market_conditions", "post_trade"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
  trade_id TEXT PRIMARY KEY,
  timestamp TEXT NOT NULL,
  asset TEXT NOT NULL,
  side TEXT NOT NULL,
  setup TEXT,
  mode TEXT,
  status TEXT NOT NULL,
  entry REAL,
  exit REAL,
  position_size REAL,
  notional_usd REAL,
  risk_pct REAL,
  risk_usd REAL,
  stop_price REAL,
  take_profit_price REAL,
  decision_price REAL,
  agents_agreed TEXT,
  agents_disagreed TEXT,
  agent_signals TEXT,
  confidence REAL,
  reason TEXT,
  expected_return_pct REAL,
  actual_return_pct REAL,
  pnl_usd REAL,
  fees_usd REAL,
  market_conditions TEXT,
  entry_slippage_bps REAL,
  exit_slippage_bps REAL,
  slippage_bps REAL,
  result TEXT,
  exit_reason TEXT,
  closed_at TEXT,
  holding_minutes REAL,
  r_multiple REAL,
  entry_order_id TEXT,
  stop_order_id TEXT,
  post_trade TEXT,
  lessons TEXT
);
CREATE INDEX IF NOT EXISTS trades_status ON trades(status);
CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, asset TEXT, stage TEXT,
  outcome TEXT, details TEXT);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, level TEXT, kind TEXT,
  message TEXT, details TEXT);
CREATE TABLE IF NOT EXISTS equity (ts TEXT, equity REAL, cash REAL, heat_pct REAL);
CREATE TABLE IF NOT EXISTS llm_usage (ts TEXT, role TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER,
  cache_read INTEGER, cache_write INTEGER, cost_usd REAL);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
"""


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


class Journal:
    def __init__(self, path: str | Path = "data/journal.sqlite"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._lock = threading.RLock()   # intelligence-layer agents run in threads and share this connection
        with self._lock:
            self.db.executescript(_SCHEMA)
            self.db.commit()

    def _write(self, sql: str, args=()) -> None:
        with self._lock:
            self.db.execute(sql, args)
            self.db.commit()

    def _all(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(sql, args).fetchall()

    def _one(self, sql: str, args=()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self.db.execute(sql, args).fetchone()

    # ------------------------------------------------------------ trades
    def open_trade(self, rec: TradeRecord) -> None:
        row = rec.model_dump()
        for k in _JSON_FIELDS:
            row[k] = json.dumps(row[k], default=str)
        row["timestamp"] = _iso(rec.timestamp)
        row["closed_at"] = _iso(rec.closed_at)
        cols = ", ".join(row)
        self._write(f"INSERT INTO trades ({cols}) VALUES ({', '.join('?' for _ in row)})", list(row.values()))

    def update_trade(self, trade_id: str, **fields: Any) -> None:
        for k in list(fields):
            if k in _JSON_FIELDS:
                fields[k] = json.dumps(fields[k], default=str)
            elif isinstance(fields[k], datetime):
                fields[k] = fields[k].isoformat()
        sets = ", ".join(f"{k} = ?" for k in fields)
        self._write(f"UPDATE trades SET {sets} WHERE trade_id = ?", [*fields.values(), trade_id])

    def close_trade(self, trade_id: str, exit_price: float, exit_reason: str, exit_fee_usd: float,
                    exit_reference_price: float, closed_at: Optional[datetime] = None) -> TradeRecord:
        rec = self.get_trade(trade_id)
        closed_at = closed_at or utcnow()
        sign = 1 if rec.side == "long" else -1
        gross = sign * (exit_price - rec.entry) * rec.position_size
        fees = rec.fees_usd + exit_fee_usd
        pnl = gross - fees
        actual_ret = pnl / rec.notional_usd * 100 if rec.notional_usd else 0.0
        exit_slip = sign * (exit_reference_price - exit_price) / exit_reference_price * 1e4 if exit_reference_price else 0.0
        result = "WIN" if pnl > 0.0005 * rec.notional_usd else "LOSS" if pnl < -0.0005 * rec.notional_usd else "BREAKEVEN"
        self.update_trade(
            trade_id, status="CLOSED", exit=exit_price, exit_reason=exit_reason, closed_at=closed_at,
            pnl_usd=pnl, fees_usd=fees, actual_return_pct=actual_ret, exit_slippage_bps=exit_slip,
            slippage_bps=rec.entry_slippage_bps + exit_slip, result=result,
            holding_minutes=(closed_at - rec.timestamp).total_seconds() / 60,
            r_multiple=pnl / rec.risk_usd if rec.risk_usd else 0.0,
        )
        return self.get_trade(trade_id)

    def _row_to_record(self, r: sqlite3.Row) -> TradeRecord:
        d = dict(r)
        for k in _JSON_FIELDS:
            d[k] = json.loads(d[k]) if d.get(k) else ([] if k.startswith("agent") else {})
        d["lessons"] = d.get("lessons") or ""
        return TradeRecord.model_validate(d)

    def get_trade(self, trade_id: str) -> TradeRecord:
        r = self._one("SELECT * FROM trades WHERE trade_id = ?", (trade_id,))
        if r is None:
            raise KeyError(trade_id)
        return self._row_to_record(r)

    def open_trades(self) -> list[TradeRecord]:
        return [self._row_to_record(r) for r in self._all("SELECT * FROM trades WHERE status = 'OPEN' ORDER BY timestamp")]

    def closed_trades(self, limit: Optional[int] = None, mode: Optional[str] = None) -> list[TradeRecord]:
        q, args = "SELECT * FROM trades WHERE status = 'CLOSED'", []
        if mode:
            q += " AND mode = ?"
            args.append(mode)
        q += " ORDER BY closed_at DESC"
        if limit:
            q += f" LIMIT {int(limit)}"
        return [self._row_to_record(r) for r in self._all(q, args)]

    def realized_pnl_since(self, since: datetime) -> float:
        r = self._one("SELECT COALESCE(SUM(pnl_usd), 0) FROM trades WHERE status='CLOSED' AND closed_at >= ?",
                      (since.isoformat(),))
        return float(r[0])

    def realized_pnl_total(self, mode: Optional[str] = None) -> float:
        q, args = "SELECT COALESCE(SUM(pnl_usd), 0) FROM trades WHERE status='CLOSED'", []
        if mode:
            q += " AND mode = ?"
            args.append(mode)
        return float(self._one(q, args)[0])

    def export_csv(self, path: str | Path) -> int:
        rows = self._all("SELECT * FROM trades ORDER BY timestamp")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if rows:
                w.writerow(rows[0].keys())
            w.writerows([tuple(r) for r in rows])
        return len(rows)

    # ------------------------------------------------------------ decisions / events
    def log_decision(self, asset: str, stage: str, outcome: str, details: dict | None = None) -> None:
        self._write("INSERT INTO decisions (ts, asset, stage, outcome, details) VALUES (?,?,?,?,?)",
                    (utcnow().isoformat(), asset, stage, outcome, json.dumps(details or {}, default=str)))

    def log_event(self, kind: str, message: str, level: str = "INFO", details: dict | None = None) -> None:
        self._write("INSERT INTO events (ts, level, kind, message, details) VALUES (?,?,?,?,?)",
                    (utcnow().isoformat(), level, kind, message, json.dumps(details or {}, default=str)))

    def count_events(self, kind: str, since: datetime) -> int:
        return int(self._one("SELECT COUNT(*) FROM events WHERE kind = ? AND ts >= ?", (kind, since.isoformat()))[0])

    # ------------------------------------------------------------ equity
    def record_equity(self, equity: float, cash: float, heat_pct: float) -> None:
        self._write("INSERT INTO equity VALUES (?,?,?,?)", (utcnow().isoformat(), equity, cash, heat_pct))

    def peak_equity(self) -> Optional[float]:
        r = self._one("SELECT MAX(equity) FROM equity")
        return float(r[0]) if r and r[0] is not None else None

    def equity_at_or_after(self, since: datetime) -> Optional[float]:
        r = self._one("SELECT equity FROM equity WHERE ts >= ? ORDER BY ts LIMIT 1", (since.isoformat(),))
        return float(r[0]) if r else None

    # ------------------------------------------------------------ llm usage
    def record_llm_usage(self, role: str, model: str, usage: dict[str, int], cost: float) -> None:
        self._write("INSERT INTO llm_usage VALUES (?,?,?,?,?,?,?,?)",
                    (utcnow().isoformat(), role, model, usage.get("input", 0), usage.get("output", 0),
                     usage.get("cache_read", 0), usage.get("cache_write", 0), cost))

    def llm_spend_since(self, since: datetime) -> float:
        return float(self._one("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE ts >= ?", (since.isoformat(),))[0])

    def llm_spend_today(self) -> float:
        return self.llm_spend_since(start_of_utc_day())

    # ------------------------------------------------------------ state
    def set_state(self, key: str, value: Any) -> None:
        self._write("INSERT INTO state (key, value, updated_at) VALUES (?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                    (key, json.dumps(value, default=str), utcnow().isoformat()))

    def get_state(self, key: str, default: Any = None) -> Any:
        r = self._one("SELECT value FROM state WHERE key = ?", (key,))
        return json.loads(r[0]) if r else default


def start_of_utc_day(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def hours_ago(h: float) -> datetime:
    return utcnow() - timedelta(hours=h)
