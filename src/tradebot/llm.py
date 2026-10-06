"""Thin Claude wrapper used by every agent.

- one model per agent role (config `llm.agents`), Opus 5.5 for the orchestrator
- structured outputs: every call returns a validated pydantic object or None
- refusal fallback (`fallbacks: "default"`) on models that support it
- token cost is recorded to the journal; past the daily budget every call returns None
- offline mode (no ANTHROPIC_API_KEY or mode=simulated with --offline): `online` is False and agents
  use their deterministic heuristics instead
"""
from __future__ import annotations

import copy
import json
import logging
import os
from typing import Optional, Type, TypeVar

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


class LLMClient:
    def __init__(self, cfg: LLMConfig, journal=None, offline: bool = False):
        self.cfg = cfg
        self.journal = journal
        self.online = not offline and bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        self._client = None
        if self.online:
            import anthropic
            self._client = anthropic.Anthropic()

    def budget_exhausted(self) -> bool:
        return bool(self.journal) and self.journal.llm_spend_today() >= self.cfg.daily_budget_usd

    def structured(self, role: str, system: str, payload: dict, schema: Type[T]) -> Optional[T]:
        """Ask the role's model for a `schema` object. Returns None on any failure (caller abstains)."""
        if not self.online or self._client is None:
            return None
        if self.budget_exhausted():
            log.warning("LLM daily budget exhausted; %s abstains", role)
            return None
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
            log.error("LLM call failed for %s (%s): %s", role, mc.model, e)
            if self.journal:
                self.journal.log_event("llm_error", f"{role}: {type(e).__name__}", "WARN", {"error": str(e)[:500]})
            return None
        self._record_usage(role, mc.model, resp)
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

    def _record_usage(self, role: str, model: str, resp) -> None:
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
        if self.journal:
            self.journal.record_llm_usage(role, model, usage, cost)
