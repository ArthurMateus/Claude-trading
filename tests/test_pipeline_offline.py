"""End-to-end cycles with synthetic data, the simulated broker and no LLM (deterministic heuristics)."""
from datetime import datetime, timezone

from tradebot.brokers.simulated import SimulatedBroker
from tradebot.data.providers import SyntheticProvider
from tradebot.journal import Journal
from tradebot.llm import LLMClient
from tradebot.pipeline import Pipeline
from tradebot.strategies import SETUPS


def build(settings):
    journal = Journal(":memory:")
    llm = LLMClient(settings.llm, journal, offline=True)
    broker = SimulatedBroker(10_000, settings.costs.taker_fee_bps, settings.costs.sim_slippage_bps)
    return Pipeline(settings, SyntheticProvider(seed=3), broker, journal, llm)


def force_validate(p: Pipeline):
    now = datetime.now(timezone.utc).isoformat()
    full = {"trades": 100, "win_rate": 0.55, "profit_factor": 1.5, "expectancy_bps": 20.0, "avg_win_bps": 80,
            "avg_loss_bps": -60, "sharpe_per_trade": 1.0, "max_drawdown_pct": 5.0}
    p.journal.set_state("setup_validation", {n: {"passed": True, "fails": [], "full": full, "oos": full,
                                                  "validated_at": now} for n in SETUPS})


def test_random_walk_setups_do_not_pass_the_gate(settings):
    p = build(settings)
    res = p.backtester.validate(p.provider)
    assert set(res) == set(SETUPS)
    # A random walk has no edge after fees; the gate must not let every setup through.
    assert not all(r["passed"] for r in res.values())


def fire_setups(monkeypatch, settings):
    """Make a validated setup fire on every asset and let any positive vote through the prefilter."""
    import tradebot.agents.market_data as md
    from tradebot.agents.quant import QuantAgent
    from tradebot.contracts import AgentSignal

    monkeypatch.setattr(md, "active_setups", lambda df: ["trend_pullback"])
    monkeypatch.setattr(QuantAgent, "analyze", lambda self, st, **_: AgentSignal(
        agent="quant", asset=st.asset, direction="long", score=0.8, confidence=0.7, probability=0.65,
        expected_return_pct=0.4, rationale="test edge"))
    settings.llm.prefilter_min_abs_score = 0.0


def test_cycle_runs_and_journals_trades(settings, monkeypatch):
    fire_setups(monkeypatch, settings)
    p = build(settings)
    force_validate(p)
    report = p.run_cycle()
    assert report.kill_switch == "OK"
    assert report.scanned == len(settings.universe)
    assert report.opened, report.skipped          # the full path reached the broker
    for t in p.journal.open_trades():
        assert t.stop_price < t.entry < t.take_profit_price
        assert t.stop_order_id                       # every open trade has a resting protective stop
        assert t.risk_pct <= settings.risk.base_risk_pct + 1e-6   # nothing calibrated yet -> base risk
        assert t.reason and t.market_conditions and t.agent_signals
    heat = sum(t.risk_usd for t in p.journal.open_trades()) / p.portfolio_state().equity * 100
    assert heat <= settings.risk.max_portfolio_heat_pct + 1e-6


def test_bot_trades_only_its_allocated_capital(settings, monkeypatch):
    """Broker holds $10k (like an Alpaca paper account); the bot must size and account on its $500 only."""
    fire_setups(monkeypatch, settings)
    settings.allocated_capital_usd = 500
    p = build(settings)
    force_validate(p)
    assert p.portfolio_state().equity == 500
    p.run_cycle()
    opened = p.journal.open_trades()
    assert opened
    cap = 500 * settings.risk.max_position_notional_pct / 100
    assert all(t.notional_usd <= cap + 1e-6 for t in opened)
    assert sum(t.notional_usd + t.fees_usd for t in opened) <= 500
    st = p.portfolio_state()
    assert st.cash <= 500 - sum(t.notional_usd for t in opened) + 1e-6
    assert abs(st.equity - 500) < 10                # only spread/fees/marks moved it


def test_hard_halt_blocks_entries_and_persists(settings):
    p = build(settings)
    force_validate(p)
    p.journal.set_state("halt", {"reason": "test"})
    r = p.run_cycle()
    assert r.kill_switch == "HALT" and not r.opened


def test_time_stop_closes_and_post_trade_runs(settings, monkeypatch):
    fire_setups(monkeypatch, settings)
    settings.max_hold_minutes = 0
    p = build(settings)
    force_validate(p)
    p.run_cycle()
    opened = p.journal.open_trades()
    assert opened
    p.run_cycle()
    for t in opened:
        closed = p.journal.get_trade(t.trade_id)
        assert closed.status == "CLOSED" and closed.result in ("WIN", "LOSS", "BREAKEVEN")
        assert closed.post_trade.get("attribution") is not None
