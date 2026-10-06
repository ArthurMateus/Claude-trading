"""One trading cycle, end to end:

  Market Data -> manage open positions -> Kill-Switch -> (per asset with a validated active setup)
  Intelligence layer in parallel -> Orchestrator fusion -> Risk -> Portfolio -> guardrail re-check
  -> Execution -> Journal ; closed trades -> Post-Trade -> learning loop
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd

from .agents.backtest_agent import BacktestAgent
from .agents.base import AgentContext
from .agents.execution import ExecutionAgent
from .agents.flow import FlowAgent
from .agents.fundamental import FundamentalAgent
from .agents.kill_switch import KillSwitchAgent
from .agents.market_data import MarketDataAgent
from .agents.news import NewsAgent
from .agents.orchestrator import Orchestrator
from .agents.paper_trading import PaperTradingAgent
from .agents.portfolio import PortfolioAgent
from .agents.post_trade import PostTradeAgent
from .agents.quant import QuantAgent
from .agents.replication import ReplicationAgent
from .agents.risk import RiskAgent
from .agents.technical import TechnicalAgent
from .brokers.base import Broker
from .config import Settings
from .contracts import AgentSignal, AssetState, MarketSnapshot, utcnow
from .data.news_sources import NewsHub
from .data.providers import MarketDataProvider
from .guardrails import PortfolioState
from .journal import Journal, start_of_utc_day
from .learning import update_agent_weights
from .llm import LLMClient
from .strategies import setup_context

log = logging.getLogger(__name__)


@dataclass
class CycleReport:
    ts: datetime
    equity: float = 0.0
    kill_switch: str = "OK"
    kill_reasons: list[str] = field(default_factory=list)
    scanned: int = 0
    candidates: int = 0
    approved: int = 0
    opened: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)


class Pipeline:
    def __init__(self, settings: Settings, provider: MarketDataProvider, broker: Broker, journal: Journal,
                 llm: LLMClient):
        self.s, self.provider, self.broker, self.journal, self.llm = settings, provider, broker, journal, llm
        ctx = AgentContext(settings, llm, journal)
        self.market = MarketDataAgent(ctx, provider)
        self.signal_agents = [TechnicalAgent(ctx), QuantAgent(ctx), NewsAgent(ctx), FlowAgent(ctx),
                              FundamentalAgent(ctx)]
        self.replication = ReplicationAgent(ctx, provider)   # runs after the others: it reads their votes
        self.orchestrator = Orchestrator(ctx)
        self.backtester = BacktestAgent(ctx)
        self.risk = RiskAgent(ctx)
        self.portfolio = PortfolioAgent(ctx)
        self.execution = ExecutionAgent(ctx, broker)
        self.kill = KillSwitchAgent(ctx, broker)
        self.post_trade = PostTradeAgent(ctx)
        self.paper = PaperTradingAgent(ctx)
        self._history: dict[str, tuple[datetime, pd.DataFrame]] = {}
        self._last_snapshot: MarketSnapshot | None = None
        news_provider = provider if hasattr(provider, "news") else None
        self.news_hub = NewsHub(settings.news, news_provider, journal, daily_budget_usd=settings.llm.daily_budget_usd)

    # ------------------------------------------------------------ helpers
    def portfolio_state(self) -> PortfolioState:
        """Equity/cash the bot may use. With `allocated_capital_usd` set, this is a virtual sub-account:
        allocation + realized P&L + open positions marked at the bid, never more than the broker really has."""
        acct = self.broker.account()
        open_trades = self.journal.open_trades()
        equity, cash = acct.equity, acct.cash
        cap = self.s.allocated_capital_usd
        if cap:
            realized = self.journal.realized_pnl_total(self.s.mode)
            cost = sum(t.entry * t.position_size + t.fees_usd for t in open_trades)
            mark = sum(self._mark(t) * t.position_size for t in open_trades)
            v_cash = cap + realized - cost
            equity = min(acct.equity, v_cash + mark)
            cash = max(0.0, min(acct.cash, v_cash))
        day_start = self.journal.equity_at_or_after(start_of_utc_day()) or equity
        return PortfolioState(equity=equity, cash=cash, open_trades=open_trades, day_start_equity=day_start)

    def _mark(self, trade) -> float:
        st = self._last_snapshot.assets.get(trade.asset) if self._last_snapshot else None
        return st.bid if st else trade.entry

    def history(self, asset: str) -> pd.DataFrame:
        """~14 days of bars for the Quant agent, refreshed hourly."""
        now = utcnow()
        cached = self._history.get(asset)
        if cached and now - cached[0] < timedelta(hours=1):
            return cached[1]
        df = self.provider.bars(asset, self.s.bar_timeframe_minutes, start=now - timedelta(days=14), end=now)
        self._history[asset] = (now, df)
        return df

    def gather_signals(self, st: AssetState, snap: MarketSnapshot) -> list[AgentSignal]:
        try:
            news = snap.news.get(st.asset) or self.news_hub.for_asset(st.asset)
        except Exception as e:
            log.warning("news failed for %s: %s", st.asset, e)
            news = []
        inputs = {"bars": snap.bars.get(st.asset), "news": news, "history": self.history(st.asset)}
        with ThreadPoolExecutor(max_workers=len(self.signal_agents)) as pool:
            futures = [pool.submit(a.analyze, st, **inputs) for a in self.signal_agents]
            out = []
            for agent, f in zip(self.signal_agents, futures):
                try:
                    out.append(f.result())
                except Exception as e:
                    log.exception("agent %s failed", agent.name)
                    out.append(agent.abstain(st.asset, f"error: {e}"))
        try:
            out.append(self.replication.analyze(st, signals=list(out), **inputs))
        except Exception as e:
            log.exception("replication agent failed")
            out.append(self.replication.abstain(st.asset, f"error: {e}"))
        return out

    def _close(self, trade, reason: str, ref_price: float, report: CycleReport) -> None:
        closed = self.execution.exit(trade, reason, ref_price)
        self._after_close(closed, report)

    def _after_close(self, closed, report: CycleReport) -> None:
        report.closed.append(f"{closed.asset}:{closed.result}:{closed.exit_reason}")
        try:
            self.post_trade.analyze(closed)
            update_agent_weights(self.journal, self.s)
        except Exception:
            log.exception("post-trade failed for %s", closed.trade_id)

    # ------------------------------------------------------------ stages
    def manage_positions(self, snap: MarketSnapshot, report: CycleReport) -> None:
        now = utcnow()
        for t in self.journal.open_trades():
            if t.stop_order_id:
                try:
                    st = self.broker.order_status(t.stop_order_id)
                except Exception as e:
                    self.journal.log_event("api_error", f"stop status {t.asset}: {e}", "WARN")
                    continue
                if st.status == "filled":
                    closed = self.journal.close_trade(t.trade_id, st.avg_price, "stop", st.fee_usd, t.stop_price)
                    self._after_close(closed, report)
                    continue
            a = snap.assets.get(t.asset)
            if a is None:
                continue
            held = (now - t.timestamp).total_seconds() / 60
            if t.side == "long" and a.bid >= t.take_profit_price:
                self._close(t, "take_profit", a.bid, report)
            elif held >= self.s.max_hold_minutes:
                self._close(t, "time_stop", a.bid if t.side == "long" else a.ask, report)

    def flatten_all(self, snap: MarketSnapshot, reason: str, report: CycleReport) -> None:
        for t in self.journal.open_trades():
            a = snap.assets.get(t.asset)
            ref = a.bid if a else t.entry
            self._close(t, f"kill_switch:{reason}", ref, report)

    def run_cycle(self) -> CycleReport:
        report = CycleReport(ts=utcnow())
        if self.backtester.is_stale():
            log.info("setup validation stale; running backtests")
            self.backtester.validate(self.provider)

        snap = self.market.snapshot()
        self._last_snapshot = snap
        self.broker.on_market(snap)
        report.scanned = len(snap.assets)
        self.manage_positions(snap, report)

        pstate = self.portfolio_state()
        ks = self.kill.evaluate(pstate, snap)
        report.kill_switch, report.kill_reasons = ks.level, ks.reasons
        if ks.flatten:
            self.flatten_all(snap, ks.reasons[0] if ks.reasons else "halt", report)
            pstate = self.portfolio_state()
        self.journal.record_equity(pstate.equity, pstate.cash, pstate.heat_pct)
        report.equity = pstate.equity
        if not ks.allows_entries:
            return report

        validated = self.backtester.validated_setups()
        held_assets = {t.asset for t in pstate.open_trades}
        candidates = []
        for asset, st in snap.assets.items():
            if not st.data_ok:
                report.skipped[asset] = "data: " + "; ".join(st.data_issues)
                continue
            if asset in held_assets:
                report.skipped[asset] = "already held"
                continue
            tradeable = {s: validated[s] | setup_context(s) for s in st.active_setups if s in validated}
            if not tradeable:   # cost gate: no LLM spend without a validated setup firing
                report.skipped[asset] = "no validated setup active" if st.active_setups else "no setup"
                continue
            signals = self.gather_signals(st, snap)
            cand, why = self.orchestrator.fuse(st, signals, tradeable, sorted(held_assets))
            self.journal.log_decision(asset, "fusion", "CANDIDATE" if cand else "PASS",
                                      {"why": why, "signals": [s.model_dump(exclude={"data"}) for s in signals]})
            if cand:
                candidates.append(cand)
            else:
                report.skipped[asset] = why
        report.candidates = len(candidates)

        plans = []
        for c in candidates:
            plan = self.risk.evaluate(c, snap.assets[c.asset], pstate, snap.correlations)
            self.journal.log_decision(c.asset, "risk", "APPROVE" if plan.approved else "REJECT",
                                      {"reasons": plan.reasons, "risk_pct": plan.risk_pct, "qty": plan.quantity})
            if plan.approved:
                plans.append(plan)
            else:
                report.skipped[c.asset] = "risk: " + "; ".join(plan.reasons[-2:])
        report.approved = len(plans)

        for plan in self.portfolio.select(plans, pstate):
            st = snap.assets[plan.candidate.asset]
            fresh = self.portfolio_state()   # heat/cluster/cash change after every fill
            plan = self.risk.apply_guardrails(plan.candidate, st, plan.entry_price, plan.stop_price,
                                              plan.take_profit_price, plan.risk_pct, fresh, snap.correlations,
                                              source=plan.source)
            if not plan.approved:
                report.skipped[st.asset] = "re-check: " + "; ".join(plan.reasons[-1:])
                continue
            rec = self.execution.enter(plan, st, fresh.equity)
            if rec:
                report.opened.append(f"{rec.asset} {rec.position_size:.6f} @ {rec.entry:.4f}")
        if self.s.mode == "paper":
            self.paper.evaluate()
        return report

    def run_forever(self) -> None:
        while True:
            started = time.time()
            try:
                r = self.run_cycle()
                log.info("cycle: equity=%.2f ks=%s scanned=%d cand=%d approved=%d opened=%s closed=%s",
                         r.equity, r.kill_switch, r.scanned, r.candidates, r.approved, r.opened, r.closed)
            except Exception as e:
                log.exception("cycle failed")
                self.journal.log_event("api_error", f"cycle failed: {e}", "ERROR")
            time.sleep(max(5.0, self.s.cycle_interval_seconds - (time.time() - started)))
