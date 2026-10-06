"""LLMClient behavior with a fake Anthropic client (no network, no spend)."""
from types import SimpleNamespace

from tradebot.agents.base import SignalOut
from tradebot.config import LLMConfig, ModelConfig
from tradebot.journal import Journal
from tradebot.llm import LLMClient


class FakeMessages:
    def __init__(self, text='{"direction":"long","score":0.5,"confidence":0.6,"rationale":"ok"}', stop="end_turn"):
        self.text, self.stop, self.calls = text, stop, []

    def create(self, **kw):
        self.calls.append(kw)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=100, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(stop_reason=self.stop, usage=usage,
                               content=[SimpleNamespace(type="text", text=self.text)])


def client(monkeypatch, fake: FakeMessages, budget=10.0) -> tuple[LLMClient, Journal]:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cfg = LLMConfig(daily_budget_usd=budget, agents={
        "news": ModelConfig(model="claude-haiku-4-5", max_tokens=500),
        "risk": ModelConfig(model="claude-sonnet-5-5", effort="medium", max_tokens=500)})
    j = Journal(":memory:")
    c = LLMClient(cfg, j)
    c._client = SimpleNamespace(messages=fake, beta=SimpleNamespace(messages=fake))
    return c, j


def test_parses_structured_output_and_records_cost(monkeypatch):
    fake = FakeMessages()
    c, j = client(monkeypatch, fake)
    out = c.structured("news", "sys", {"x": 1}, SignalOut)
    assert out and out.direction == "long"
    kw = fake.calls[0]
    assert kw["model"] == "claude-haiku-4-5"
    assert "effort" not in kw["output_config"] and "fallbacks" not in kw     # Haiku: no effort, no fallback
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert abs(j.llm_spend_today() - (1000 * 1.0 + 100 * 5.0) / 1e6) < 1e-12


def test_sonnet_gets_effort_and_refusal_fallback(monkeypatch):
    fake = FakeMessages()
    c, _ = client(monkeypatch, fake)
    c.structured("risk", "sys", {}, SignalOut)
    kw = fake.calls[0]
    assert kw["output_config"]["effort"] == "medium"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]


def test_refusal_invalid_json_and_budget_return_none(monkeypatch):
    c, _ = client(monkeypatch, FakeMessages(stop="refusal"))
    assert c.structured("news", "s", {}, SignalOut) is None
    c, _ = client(monkeypatch, FakeMessages(text="not json"))
    assert c.structured("news", "s", {}, SignalOut) is None
    fake = FakeMessages()
    c, _ = client(monkeypatch, fake, budget=0.0)
    assert c.structured("news", "s", {}, SignalOut) is None and not fake.calls
