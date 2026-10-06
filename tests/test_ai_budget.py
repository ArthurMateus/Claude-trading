"""Spreading the Claude subscription credit across 24/7: pacing, billing period, cooldown, free pre-screen."""
from datetime import datetime, timezone

from tradebot.brokers.simulated import SimulatedBroker
from tradebot.config import LLMConfig
from tradebot.data.providers import SyntheticProvider
from tradebot.journal import Journal
from tradebot.llm import LLMClient
from tradebot.pipeline import Pipeline

from .test_pipeline_offline import fire_setups, force_validate


def client(journal, **cfg) -> LLMClient:
    c = LLMClient(LLMConfig(**cfg), journal, offline=True)
    return c


def test_billing_period_follows_the_reset_day():
    c = client(None, credit_reset_day=17)
    start, end = c.billing_period(datetime(2026, 10, 6, 12, tzinfo=timezone.utc))
    assert (start.month, start.day, end.month, end.day) == (9, 17, 10, 17)
    start, end = c.billing_period(datetime(2026, 12, 20, tzinfo=timezone.utc))
    assert (start.month, start.day, end.year, end.month, end.day) == (12, 17, 2027, 1, 17)


def test_pacing_spreads_the_month_and_carries_unused_credit_forward(monkeypatch):
    j = Journal(":memory:")
    c = client(j, monthly_budget_usd=20.0, pacing_burst_usd=1.0, daily_budget_usd=5.0)
    start, end = c.billing_period()
    frac = (datetime.now(timezone.utc) - start) / (end - start)
    expected = min(20.0, 20.0 * frac + 1.0)
    assert abs(c.paced_allowance() - expected) < 0.01
    ok, _ = c.budget_status()
    assert ok                                             # nothing spent yet
    j.record_llm_usage("orchestrator", "claude-opus-5-5", {}, expected + 0.01)
    ok, why = c.budget_status()
    assert not ok and ("pacing" in why or "monthly" in why or "daily" in why)


def test_monthly_cap_and_daily_safety_cap():
    j = Journal(":memory:")
    c = client(j, monthly_budget_usd=1.0, pacing=False, daily_budget_usd=5.0)
    j.record_llm_usage("x", "m", {}, 1.0)
    assert c.monthly_exhausted() and "monthly" in c.budget_status()[1]
    j2 = Journal(":memory:")
    c2 = client(j2, monthly_budget_usd=100.0, pacing=False, daily_budget_usd=0.5)
    j2.record_llm_usage("x", "m", {}, 0.5)
    assert not c2.monthly_exhausted() and "daily" in c2.budget_status()[1]


class CountingLLM(LLMClient):
    """Pretends to be online; every call is counted and abstains (returns None)."""

    def __init__(self, cfg, journal):
        super().__init__(cfg, journal, offline=True)
        self.online, self.backend, self.calls = True, "claude_cli", 0

    def structured(self, role, system, payload, schema):
        if self.budget_exhausted():
            return None
        self.calls += 1
        return None


def build(settings, llm_cfg_overrides=None):
    for k, v in (llm_cfg_overrides or {}).items():
        setattr(settings.llm, k, v)
    j = Journal(":memory:")
    llm = CountingLLM(settings.llm, j)
    p = Pipeline(settings, SyntheticProvider(seed=3), SimulatedBroker(10_000), j, llm)
    force_validate(p)
    return p, llm


def test_cooldown_avoids_reanalysing_the_same_setup(settings, monkeypatch):
    fire_setups(monkeypatch, settings)
    p, llm = build(settings, {"prescreen_min_score": None, "reanalyze_cooldown_minutes": 30})
    p.run_cycle()
    first = llm.calls
    assert first > 0
    r = p.run_cycle()
    assert llm.calls == first and any("cooldown" in v for v in r.skipped.values())


def test_pacing_wait_skips_ai_without_tripping_the_kill_switch(settings, monkeypatch):
    fire_setups(monkeypatch, settings)
    p, llm = build(settings, {"prescreen_min_score": None, "pacing_burst_usd": 0.0, "monthly_budget_usd": 19.5})
    p.journal.record_llm_usage("orchestrator", "claude-opus-5-5", {}, 19.0)   # far ahead of the even pace
    r = p.run_cycle()
    assert llm.calls == 0 and r.kill_switch == "OK"
    assert all("AI pacing" in v or "monthly" in v for v in r.skipped.values())


def test_monthly_budget_used_pauses_entries(settings, monkeypatch):
    fire_setups(monkeypatch, settings)
    p, llm = build(settings, {"monthly_budget_usd": 1.0})
    p.journal.record_llm_usage("orchestrator", "claude-opus-5-5", {}, 1.0)
    r = p.run_cycle()
    assert llm.calls == 0 and r.kill_switch == "PAUSE_ENTRIES"


def test_free_prescreen_blocks_ai_spend_on_weak_setups(settings, monkeypatch):
    fire_setups(monkeypatch, settings)
    p, llm = build(settings, {"prescreen_min_score": 0.99})        # nothing can clear this
    r = p.run_cycle()
    assert llm.calls == 0 and all("pre-screen" in v for v in r.skipped.values())
