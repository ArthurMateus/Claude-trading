"""Discord alerts with a fake webhook (no network)."""
from datetime import timedelta

from tradebot.brokers.simulated import SimulatedBroker
from tradebot.data.providers import SyntheticProvider
from tradebot.journal import Journal
from tradebot.llm import LLMClient
from tradebot.notify import Notifier
from tradebot.pipeline import Pipeline

from .conftest import make_trade
from .test_pipeline_offline import fire_setups, force_validate


class Hook:
    def __init__(self, fail=False):
        self.msgs, self.fail = [], fail

    def __call__(self, url, payload):
        if self.fail:
            raise OSError("discord down")
        self.msgs.append(payload["embeds"][0])

    def titles(self):
        return [m["title"] for m in self.msgs]


def notifier(journal=None, fail=False, url="https://discord.example/webhook"):
    hook = Hook(fail)
    return Notifier(journal, webhook_url=url, post=hook, mode="paper"), hook


def test_disabled_without_webhook_and_failures_never_raise(journal):
    n, hook = notifier(journal, url="")
    assert not n.enabled and not n.send("x") and not hook.msgs
    n, _ = notifier(journal, fail=True)
    assert n.send("x") is False                      # logged, not raised


def test_trade_messages_contain_the_journal_fields(journal):
    n, hook = notifier(journal)
    t = make_trade(0)
    n.trade_opened(t)
    closed = t.model_copy(update={"status": "CLOSED", "result": "WIN", "pnl_usd": 4.2, "r_multiple": 1.4,
                                  "exit": 104.2, "exit_reason": "take_profit", "lessons": "worked"})
    n.trade_closed(closed)
    opened, done = hook.msgs
    assert "Opened" in opened["title"] and {f["name"] for f in opened["fields"]} >= {"Entry", "Agreed", "Confidence"}
    assert "WIN" in done["title"] and any("+4.20" in f["value"] for f in done["fields"])


def test_dedupe_interval(journal):
    n, hook = notifier(journal)
    n.budget_exhausted(0.6, 3.0)
    n.budget_exhausted(0.6, 3.0)
    assert len(hook.msgs) == 1


def build(settings, n):
    journal = n.journal
    return Pipeline(settings, SyntheticProvider(seed=3), SimulatedBroker(10_000), journal,
                    LLMClient(settings.llm, journal, offline=True), n)


def test_pipeline_sends_open_close_killswitch_and_daily_summary(settings, monkeypatch):
    fire_setups(monkeypatch, settings)
    settings.max_hold_minutes = 0
    n, hook = notifier(Journal(":memory:"))
    p = build(settings, n)
    force_validate(p)
    p.run_cycle()                                      # opens trades
    assert any(t.startswith("🟢 Opened") for t in hook.titles())
    p.run_cycle()                                      # time-stop closes them
    assert any(t.startswith(("✅", "❌", "➖")) for t in hook.titles())

    p.journal.set_state("halt", {"reason": "test"})
    p.run_cycle()
    p.run_cycle()
    assert hook.titles().count("🛡️ Kill-switch: HALT") == 1   # only on change
    p.journal.set_state("halt", None)
    p.run_cycle()
    ks_msgs = [t for t in hook.titles() if "Kill-switch" in t]
    assert len(ks_msgs) == 2 and ks_msgs[-1] != "🛡️ Kill-switch: HALT"   # the change away from HALT is reported
    expected = "🟢 Kill-switch cleared" if p.journal.get_state("kill_switch_level") == "OK" else \
        f"🛡️ Kill-switch: {p.journal.get_state('kill_switch_level')}"
    assert ks_msgs[-1] == expected

    yesterday = (p.journal.get_state("last_summary_day"))
    assert yesterday and not any("Daily summary" in t for t in hook.titles())   # first day: nothing to summarize
    from datetime import date
    p.journal.set_state("last_summary_day", (date.fromisoformat(yesterday) - timedelta(days=1)).isoformat())
    p.run_cycle()
    p.run_cycle()
    assert sum("Daily summary" in t for t in hook.titles()) == 1
