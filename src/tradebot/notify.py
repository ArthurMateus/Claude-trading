"""Discord alerts via a channel webhook (DISCORD_WEBHOOK_URL in .env).

Sends: startup, trade opened, trade closed, kill-switch level changes (incl. recovery), daily summary,
LLM credit exhausted, promotion verdict changes and repeated cycle errors. Delivery failures are logged
and never interrupt trading. Without a webhook URL every method is a no-op.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .contracts import KillSwitchStatus, TradeRecord

log = logging.getLogger(__name__)

GREEN, RED, ORANGE, BLUE, GREY = 0x2ECC71, 0xE74C3C, 0xE67E22, 0x3498DB, 0x95A5A6
Post = Callable[[str, dict], None]


def discord_post(url: str, payload: dict) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={
        "Content-Type": "application/json",
        # Discord rejects Python's default user agent.
        "User-Agent": "DiscordBot (https://github.com/ArthurMateus/Claude-trading, 0.3)"})
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


class Notifier:
    def __init__(self, journal=None, webhook_url: Optional[str] = None, post: Post = discord_post, mode: str = ""):
        self.url = webhook_url if webhook_url is not None else os.environ.get("DISCORD_WEBHOOK_URL", "")
        self.journal, self.post, self.mode = journal, post, mode

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def send(self, title: str, description: str = "", color: int = BLUE, fields: dict | None = None,
             dedupe_key: str | None = None, min_interval_minutes: float = 0) -> bool:
        if not self.enabled:
            return False
        now = datetime.now(timezone.utc)
        if dedupe_key and self.journal:
            last = self.journal.get_state(f"notify:{dedupe_key}")
            if last and now - datetime.fromisoformat(last) < timedelta(minutes=min_interval_minutes):
                return False
        embed = {"title": title[:256], "description": description[:4000], "color": color, "timestamp": now.isoformat(),
                 "footer": {"text": f"tradebot · {self.mode}" if self.mode else "tradebot"}}
        if fields:
            embed["fields"] = [{"name": str(k)[:256], "value": str(v)[:1024] or "-", "inline": True}
                               for k, v in list(fields.items())[:25]]
        try:
            self.post(self.url, {"username": "tradebot", "embeds": [embed]})
        except Exception as e:
            log.warning("discord notification failed: %s", e)
            return False
        if dedupe_key and self.journal:
            self.journal.set_state(f"notify:{dedupe_key}", now.isoformat())
        return True

    # ------------------------------------------------------------ events
    def startup(self, equity: float, setups: list[str], llm_backend: str) -> None:
        self.send("🚀 tradebot started", f"Validated setups: {', '.join(setups) or 'none yet'}", BLUE,
                  {"Equity": f"${equity:,.2f}", "AI backend": llm_backend})

    def trade_opened(self, t: TradeRecord) -> None:
        self.send(f"🟢 Opened {t.side.upper()} {t.asset}", t.reason, GREEN, {
            "Setup": t.setup, "Entry": f"{t.entry:,.4f}", "Size": f"{t.position_size:.6f} (${t.notional_usd:,.2f})",
            "Stop / Target": f"{t.stop_price:,.4f} / {t.take_profit_price:,.4f}",
            "Risk": f"{t.risk_pct:.2f}% (${t.risk_usd:,.2f})", "Confidence": f"{t.confidence:.0%}",
            "Agreed": ", ".join(t.agents_agreed) or "-", "Disagreed": ", ".join(t.agents_disagreed) or "-"})

    def trade_closed(self, t: TradeRecord) -> None:
        icon = {"WIN": "✅", "LOSS": "❌"}.get(t.result or "", "➖")
        self.send(f"{icon} Closed {t.asset}: {t.result}", t.lessons or "", GREEN if t.result == "WIN" else
                  RED if t.result == "LOSS" else GREY, {
                      "P&L": f"${(t.pnl_usd or 0):+,.2f} ({(t.actual_return_pct or 0):+.2f}%)",
                      "R": f"{(t.r_multiple or 0):+.2f}", "Exit reason": t.exit_reason or "-",
                      "Entry → Exit": f"{t.entry:,.4f} → {(t.exit or 0):,.4f}",
                      "Held": f"{(t.holding_minutes or 0):.0f} min", "Slippage": f"{(t.slippage_bps or 0):.1f} bps",
                      "Expected": f"{t.expected_return_pct:+.2f}%"})

    def kill_switch_changed(self, prev: str, status: KillSwitchStatus) -> None:
        if status.level == "OK":
            self.send("🟢 Kill-switch cleared", f"Back to normal (was {prev}).", GREEN)
            return
        color = ORANGE if status.level == "PAUSE_ENTRIES" else RED
        extra = "\nPositions were FLATTENED. Run `tradebot reset-halt` after reviewing." if status.flatten else ""
        self.send(f"🛡️ Kill-switch: {status.level}", "\n".join(f"• {r}" for r in status.reasons) + extra, color)

    def budget_exhausted(self, spent_today: float, spent_month: float) -> None:
        self.send("💸 AI credit budget reached", "Agents abstain and no new trades open until the budget resets. "
                  "Open positions keep their stops.", ORANGE,
                  {"Today": f"${spent_today:.2f}", "This month": f"${spent_month:.2f}"},
                  dedupe_key="budget", min_interval_minutes=12 * 60)

    def cycle_error(self, error: str) -> None:
        self.send("⚠️ Cycle error", error[:1500], RED, dedupe_key="cycle_error", min_interval_minutes=60)

    def promotion_changed(self, verdict: dict) -> None:
        self.send(f"🧪 Paper gate: {verdict.get('verdict')}", "\n".join(f"• {r}" for r in verdict.get("reasons", [])),
                  GREEN if verdict.get("verdict") == "PROMOTE" else BLUE,
                  {"Trades": verdict.get("trades"), "Profit factor": f"{verdict.get('profit_factor', 0):.2f}"})

    def daily_summary(self, day: str, closed: list[TradeRecord], equity: float, start_equity: Optional[float],
                      llm_day: float, llm_month: float, monthly_budget: Optional[float], open_positions: int) -> None:
        pnl = sum(t.pnl_usd or 0 for t in closed)
        wins = sum(t.result == "WIN" for t in closed)
        change = f"{(equity / start_equity - 1) * 100:+.2f}%" if start_equity else "-"
        budget = f"${llm_month:.2f} / ${monthly_budget:.0f}" if monthly_budget else f"${llm_month:.2f}"
        self.send(f"📊 Daily summary {day}", "", BLUE if pnl >= 0 else ORANGE, {
            "Trades closed": len(closed), "Win rate": f"{wins / len(closed):.0%}" if closed else "-",
            "P&L": f"${pnl:+,.2f}", "Equity": f"${equity:,.2f} ({change})", "Open positions": open_positions,
            "AI credit (day)": f"${llm_day:.2f}", "AI credit (month)": budget})
