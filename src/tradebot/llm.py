"""Thin Claude wrapper used by every agent.

Backends (config `llm.backend`):
- `claude_cli` (default): runs `claude -p` (Claude Code headless) logged in with YOUR Claude subscription.
  No API key, no pay-per-token billing: usage draws from the plan's monthly Agent SDK credit ($20 on Pro).
  When that credit is spent, calls fail (unless you enabled "usage credits" in claude.ai Settings > Usage),
  the agents abstain and the bot stops opening trades until the credit refreshes.
- `api`: the Anthropic API with ANTHROPIC_API_KEY (pay per token).
- `--offline`: no model calls at all; agents use deterministic heuristics (tests and dry runs).

Common behavior:
- one model per agent role (config `llm.agents`), Opus 5.5 for the orchestrator
- structured outputs: every call returns a validated pydantic object or None (caller abstains)
- cost of every call is recorded to the journal; past the daily or monthly budget every call returns None
"""
from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Callable, Optional, Type, TypeVar

from pydantic import BaseModel

from .config import LLMConfig

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

# $ per million tokens (input, output). Keep in sync with Anthropic's pricing page.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
# Models that accept the server-side refusal fallback and the `effort` parameter.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
_EFFORT_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
_UNSUPPORTED_KEYS = {"title", "default", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                     "minLength", "maxLength", "minItems", "maxItems", "multipleOf"}


def strict_schema(model: Type[BaseModel]) -> dict:
    """Pydantic JSON schema -> structured-outputs-compatible schema (all fields required, no extras)."""
    def fix(node):
        if isinstance(node, dict):
            for k in list(node):
                if k in _UNSUPPORTED_KEYS:
                    node.pop(k)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for v in node.values():
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)
        return node
    return fix(copy.deepcopy(model.model_json_schema()))


# Env vars that would make Claude Code bill an API key instead of the logged-in subscription.
_API_BILLING_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

Runner = Callable[..., subprocess.CompletedProcess]


class LLMClient:
    def __init__(self, cfg: LLMConfig, journal=None, offline: bool = False, run: Runner = subprocess.run):
        self.cfg = cfg
        self.journal = journal
        self.backend = "offline" if offline else cfg.backend
        self._client = None
        self._cli: Optional[str] = None
        self._run = run
        if self.backend == "claude_cli":
            self._cli = shutil.which(cfg.claude_cli_path) or (cfg.claude_cli_path if os.path.isfile(cfg.claude_cli_path) else None)
            self._cwd = tempfile.mkdtemp(prefix="tradebot-cli-")   # empty dir: no project files leak into prompts
        elif self.backend == "api" and (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            import anthropic
            self._client = anthropic.Anthropic()
        self.online = self._cli is not None or self._client is not None

    # ------------------------------------------------------------ budget
    def spend_today(self) -> float:
        return self.journal.llm_spend_today() if self.journal else 0.0

    def spend_this_month(self) -> float:
        if not self.journal:
            return 0.0
        from .journal import start_of_utc_day
        return self.journal.llm_spend_since(start_of_utc_day().replace(day=1))

    def budget_exhausted(self) -> bool:
        if not self.journal:
            return False
        return (self.spend_today() >= self.cfg.daily_budget_usd
                or (self.cfg.monthly_budget_usd is not None and self.spend_this_month() >= self.cfg.monthly_budget_usd))

    # ------------------------------------------------------------ calls
    def structured(self, role: str, system: str, payload: dict, schema: Type[T]) -> Optional[T]:
        """Ask the role's model for a `schema` object. Returns None on any failure (caller abstains)."""
        if not self.online:
            return None
        if self.budget_exhausted():
            log.warning("LLM budget exhausted; %s abstains", role)
            return None
        if self._cli:
            return self._structured_cli(role, system, payload, schema)
        return self._structured_api(role, system, payload, schema)

    def _structured_cli(self, role: str, system: str, payload: dict, schema: Type[T]) -> Optional[T]:
        mc = self.cfg.for_role(role)
        cmd = [self._cli, "-p", "--output-format", "json", "--model", mc.model,
               "--safe-mode", "--tools", "", "--no-session-persistence",
               "--system-prompt", system, "--json-schema", json.dumps(strict_schema(schema))]
        if mc.effort and mc.model in _EFFORT_MODELS:
            cmd += ["--effort", mc.effort]
        if mc.model.startswith("claude-opus"):
            cmd += ["--fallback-model", self.cfg.cli_opus_fallback_model]   # plans without Opus fall back
        env = {k: v for k, v in os.environ.items() if k not in _API_BILLING_ENV}
        try:
            proc = self._run(cmd, input=json.dumps(payload, default=str, indent=1), capture_output=True, text=True,
                             timeout=self.cfg.cli_timeout_seconds, env=env, cwd=self._cwd)
            data = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except Exception as e:
            return self._fail(role, mc.model, f"{type(e).__name__}: {e}")
        if data.get("total_cost_usd") is not None:
            u = data.get("usage") or {}
            self._record(role, mc.model, {"input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0),
                                          "cache_read": u.get("cache_read_input_tokens", 0),
                                          "cache_write": u.get("cache_creation_input_tokens", 0)},
                         float(data["total_cost_usd"]))
        if proc.returncode != 0 or data.get("is_error") or data.get("subtype") != "success":
            detail = data.get("result") or proc.stderr or f"exit {proc.returncode}"
            return self._fail(role, mc.model, str(detail)[:300])
        out = data.get("structured_output")
        if out is None:
            return self._fail(role, mc.model, "no structured_output in CLI response")
        try:
            return schema.model_validate(out)
        except Exception as e:
            return self._fail(role, mc.model, f"invalid {schema.__name__}: {e}")

    def _structured_api(self, role: str, system: str, payload: dict, schema: Type[T]) -> Optional[T]:
        mc = self.cfg.for_role(role)
        output_config: dict = {"format": {"type": "json_schema", "schema": strict_schema(schema)}}
        if mc.effort and mc.model in _EFFORT_MODELS:
            output_config["effort"] = mc.effort
        kwargs = dict(
            model=mc.model,
            max_tokens=mc.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": json.dumps(payload, default=str, indent=1)}],
            output_config=output_config,
        )
        try:
            if mc.model in _FALLBACK_MODELS:
                resp = self._client.beta.messages.create(
                    **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            else:
                resp = self._client.messages.create(**kwargs)
        except Exception as e:  # network, rate limit, 5xx, bad request: abstain, never guess
            return self._fail(role, mc.model, f"{type(e).__name__}: {e}")
        self._record_api_usage(role, mc.model, resp)
        if resp.stop_reason in ("refusal", "max_tokens"):
            log.warning("LLM %s stopped with %s", role, resp.stop_reason)
            return None
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), None)
        if not text:
            return None
        try:
            return schema.model_validate_json(text)
        except Exception as e:
            log.error("LLM %s returned invalid %s: %s", role, schema.__name__, e)
            return None

    def _fail(self, role: str, model: str, error: str) -> None:
        log.error("LLM call failed for %s (%s): %s", role, model, error)
        if self.journal:
            self.journal.log_event("llm_error", f"{role}: {error[:120]}", "WARN", {"model": model, "error": error})
        return None

    def _record(self, role: str, model: str, usage: dict[str, int], cost: float) -> None:
        if self.journal:
            self.journal.record_llm_usage(role, model, usage, cost)

    def _record_api_usage(self, role: str, model: str, resp) -> None:
        u = resp.usage
        usage = {
            "input": getattr(u, "input_tokens", 0) or 0,
            "output": getattr(u, "output_tokens", 0) or 0,
            "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
        }
        p_in, p_out = PRICES.get(model, (4.0, 20.0))
        cost = (usage["input"] * p_in + usage["output"] * p_out
                + usage["cache_read"] * p_in * 0.1 + usage["cache_write"] * p_in * 1.25) / 1e6
        self._record(role, model, usage, cost)
