"""LLMClient behavior with a fake Anthropic client / fake `claude` CLI (no network, no spend)."""
import json
from datetime import datetime, timedelta, timezone
import subprocess
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
    cfg = LLMConfig(backend="api", daily_budget_usd=budget, agents={
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


# ---------------------------------------------------------------- claude_cli backend (Claude subscription)


def cli_client(monkeypatch, tmp_path, stdout: dict | str, returncode=0, monthly=18.0, daily=0.6):
    fake_bin = tmp_path / "claude"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-reach-the-cli")
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        out = stdout if isinstance(stdout, str) else json.dumps(stdout)
        return subprocess.CompletedProcess(cmd, returncode, stdout=out, stderr="")

    cfg = LLMConfig(backend="claude_cli", claude_cli_path=str(fake_bin), daily_budget_usd=daily,
                    monthly_budget_usd=monthly, orchestrator=ModelConfig(model="claude-opus-5-5", effort="medium"),
                    agents={"news": ModelConfig(model="claude-haiku-4-5")})
    j = Journal(":memory:")
    return LLMClient(cfg, j, run=run), j, calls


OK = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.004,
      "usage": {"input_tokens": 1800, "output_tokens": 300},
      "structured_output": {"direction": "long", "score": 0.4, "confidence": 0.6, "rationale": "ok"}}


def test_cli_uses_subscription_not_api_key(monkeypatch, tmp_path):
    c, j, calls = cli_client(monkeypatch, tmp_path, OK)
    assert c.online and c.backend == "claude_cli"
    out = c.structured("news", "sys prompt", {"headline": "x"}, SignalOut)
    assert out and out.direction == "long"
    cmd, kw = calls[0]
    assert "ANTHROPIC_API_KEY" not in kw["env"] and "ANTHROPIC_AUTH_TOKEN" not in kw["env"]
    for flag in ("-p", "--safe-mode", "--no-session-persistence", "--json-schema", "--system-prompt"):
        assert flag in cmd
    assert cmd[cmd.index("--tools") + 1] == ""                    # no tools: pure text generation
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-4-5"
    assert json.loads(kw["input"]) == {"headline": "x"}            # payload via stdin, not argv
    assert abs(j.llm_spend_today() - 0.004) < 1e-12


def test_cli_opus_gets_effort_and_fallback(monkeypatch, tmp_path):
    c, _, calls = cli_client(monkeypatch, tmp_path, OK)
    c.structured("orchestrator", "s", {}, SignalOut)
    cmd = calls[0][0]
    assert cmd[cmd.index("--effort") + 1] == "medium"
    assert cmd[cmd.index("--fallback-model") + 1] == "claude-sonnet-5-5"


def test_cli_errors_abstain_and_are_logged(monkeypatch, tmp_path):
    for stdout, rc in ((OK | {"is_error": True, "subtype": "error_during_execution", "result": "credit exhausted"}, 1),
                       ("not json", 0), (OK | {"structured_output": None}, 0),
                       (OK | {"structured_output": {"direction": "up"}}, 0)):
        c, j, _ = cli_client(monkeypatch, tmp_path, stdout, rc)
        assert c.structured("news", "s", {}, SignalOut) is None
        assert j.count_events("llm_error", datetime.now(timezone.utc) - timedelta(minutes=1)) == 1


def test_cli_monthly_and_daily_budgets(monkeypatch, tmp_path):
    c, j, calls = cli_client(monkeypatch, tmp_path, OK, monthly=1.0, daily=5.0)
    j.record_llm_usage("orchestrator", "claude-opus-5-5", {}, 1.0)
    assert c.structured("news", "s", {}, SignalOut) is None and not calls
    c, j, calls = cli_client(monkeypatch, tmp_path, OK, monthly=18.0, daily=0.5)
    j.record_llm_usage("orchestrator", "claude-opus-5-5", {}, 0.5)
    assert c.structured("news", "s", {}, SignalOut) is None and not calls


def test_missing_cli_means_not_online(tmp_path):
    c = LLMClient(LLMConfig(claude_cli_path=str(tmp_path / "nope")), Journal(":memory:"))
    assert not c.online
