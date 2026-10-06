"""🛡️ Kill-Switch Agent — detects abnormal behavior -> HALT.

Levels: OK < PAUSE_ENTRIES < HALT_DAY < HALT. Deterministic checks decide; an optional cheap-LLM review
of the same metrics can only ESCALATE to PAUSE_ENTRIES, never relax anything. A HALT persists in the
journal and needs `tradebot reset-halt` by a human.
"""
from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel

from ..brokers.base import Broker
from ..contracts import KillSwitchStatus, MarketSnapshot, utcnow
from ..guardrails import PortfolioState
from ..journal import hours_ago, start_of_utc_day
from .base import Agent

_ORDER = ["OK", "PAUSE_ENTRIES", "HALT_DAY", "HALT"]


class KillReview(BaseModel):
    pause_entries: bool
    reason: str


class KillSwitchAgent(Agent):
    name = "kill_switch"
    job = ("look at system health metrics and decide whether anything looks abnormal enough to pause new entries "
           "(e.g. behavior that does not match the strategy, odd fill prices, error bursts). You can only pause.")

    def __init__(self, ctx, broker: Broker):
        super().__init__(ctx)
        self.broker = broker

    def evaluate(self, pstate: PortfolioState, snap: MarketSnapshot) -> KillSwitchStatus:
        ks, r = self.settings.kill_switch, self.settings.risk
        status = KillSwitchStatus()

        def raise_to(level: str, why: str, flatten: bool = False):
            if _ORDER.index(level) > _ORDER.index(status.level):
                status.level = level
            status.reasons.append(why)
            status.flatten = status.flatten or flatten

        halt = self.journal.get_state("halt")
        if halt:
            raise_to("HALT", f"persisted halt: {halt.get('reason')}")

        peak = max(self.journal.peak_equity() or pstate.equity, pstate.equity)
        dd = (peak - pstate.equity) / peak * 100 if peak > 0 else 0.0
        if dd >= r.max_drawdown_pct:
            raise_to("HALT", f"drawdown {dd:.1f}% >= {r.max_drawdown_pct}%", flatten=True)
            if not halt:
                self.journal.set_state("halt", {"reason": f"drawdown {dd:.1f}%", "ts": utcnow().isoformat()})
                self.journal.log_event("kill_switch", f"HARD HALT: drawdown {dd:.1f}%", "CRITICAL")

        if pstate.daily_pnl_pct <= -r.daily_loss_limit_pct:
            raise_to("HALT_DAY", f"daily loss {pstate.daily_pnl_pct:.2f}%")

        recent = self.journal.closed_trades(limit=ks.max_consecutive_losses)
        if len(recent) == ks.max_consecutive_losses and all(t.result == "LOSS" for t in recent):
            until = recent[0].closed_at + timedelta(minutes=ks.loss_streak_cooldown_minutes)
            if utcnow() < until:
                raise_to("PAUSE_ENTRIES", f"{ks.max_consecutive_losses} straight losses; cooling down until {until:%H:%M}Z")

        errors = self.journal.count_events("api_error", hours_ago(1)) + self.journal.count_events("llm_error", hours_ago(1))
        if errors >= ks.max_api_errors_per_hour:
            raise_to("PAUSE_ENTRIES", f"{errors} API/LLM errors in the last hour")

        orders = self.journal.count_events("order", hours_ago(1))
        if orders >= ks.max_orders_per_hour:
            raise_to("HALT", f"{orders} orders in the last hour (runaway loop?)")
            self.journal.set_state("halt", {"reason": "order rate", "ts": utcnow().isoformat()})

        slips = [t.slippage_bps for t in self.journal.closed_trades(limit=5) if t.slippage_bps is not None]
        if len(slips) >= 3 and sum(slips) / len(slips) > ks.max_avg_slippage_bps:
            raise_to("PAUSE_ENTRIES", f"avg slippage {sum(slips) / len(slips):.1f}bps")

        if self.llm.online and self.llm.budget_exhausted():
            raise_to("PAUSE_ENTRIES", "LLM daily budget exhausted")

        if not any(a.data_ok for a in snap.assets.values()):
            raise_to("PAUSE_ENTRIES", "no clean market data")

        try:
            held = self.broker.positions()
            for t in pstate.open_trades:
                if held.get(t.asset, 0.0) < 0.5 * t.position_size:
                    raise_to("PAUSE_ENTRIES", f"position mismatch on {t.asset}")
        except Exception as e:
            raise_to("PAUSE_ENTRIES", f"cannot read broker positions: {e}")

        if ks.llm_review and self.llm.online and status.level == "OK":
            metrics = {"drawdown_pct": dd, "daily_pnl_pct": pstate.daily_pnl_pct, "heat_pct": pstate.heat_pct,
                       "errors_1h": errors, "orders_1h": orders, "recent_results": [t.result for t in recent],
                       "recent_slippage_bps": slips,
                       "trades_today": len([t for t in self.journal.closed_trades(limit=200)
                                            if t.closed_at and t.closed_at >= start_of_utc_day()])}
            review = self.llm.structured(self.name, self.system_prompt, metrics, KillReview)
            if review and review.pause_entries:
                raise_to("PAUSE_ENTRIES", f"llm review: {review.reason}")

        if status.level != "OK":
            self.journal.log_event("kill_switch", f"{status.level}: {'; '.join(status.reasons)}",
                                   "WARN" if status.level == "PAUSE_ENTRIES" else "CRITICAL")
        return status
