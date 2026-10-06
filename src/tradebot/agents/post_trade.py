"""📋 Post-Trade Agent — attribution + lessons for every closed trade (feeds the learning loop)."""
from __future__ import annotations

from pydantic import BaseModel

from ..contracts import TradeRecord
from .base import Agent


class PostTradeOut(BaseModel):
    lessons: str
    what_worked: str
    what_failed: str
    tags: list[str]
    would_take_again: bool


def attribution(trade: TradeRecord) -> dict:
    """Which agents called it right. An agent is right if its direction matches the sign of the net result."""
    won = (trade.pnl_usd or 0) > 0
    per_agent = {}
    for s in trade.agent_signals:
        if s.get("abstain") or s.get("direction") == "flat":
            per_agent[s["agent"]] = None
            continue
        agreed_with_trade = s.get("direction") == trade.side
        per_agent[s["agent"]] = agreed_with_trade == won
    return per_agent


class PostTradeAgent(Agent):
    name = "post_trade"
    job = ("review one closed trade: compare the thesis and expected return with what happened, attribute the "
           "outcome (signal quality vs execution vs bad luck), and write one concrete, testable lesson. "
           "Do not draw conclusions from a single trade that contradict the validated statistics.")

    def analyze(self, trade: TradeRecord) -> TradeRecord:
        attr = attribution(trade)
        facts = {
            "r_multiple": trade.r_multiple, "expected_return_pct": trade.expected_return_pct,
            "actual_return_pct": trade.actual_return_pct, "exit_reason": trade.exit_reason,
            "slippage_bps": trade.slippage_bps, "modelled_slippage_bps": 2 * self.settings.costs.sim_slippage_bps,
            "holding_minutes": trade.holding_minutes, "agent_correct": attr,
            "confidence": trade.confidence, "result": trade.result,
        }
        payload = {"trade": trade.model_dump(exclude={"post_trade", "lessons"}), "facts": facts}

        def heuristic() -> PostTradeOut:
            right = [a for a, ok in attr.items() if ok]
            wrong = [a for a, ok in attr.items() if ok is False]
            slip_note = "slippage above model" if (trade.slippage_bps or 0) > facts["modelled_slippage_bps"] else ""
            return PostTradeOut(lessons=f"{trade.setup} {trade.result} via {trade.exit_reason}. {slip_note}".strip(),
                                what_worked=f"correct: {right}", what_failed=f"wrong: {wrong}",
                                tags=[trade.setup, trade.exit_reason or "", trade.result or ""], would_take_again=True)

        out, _ = self.ask(payload, PostTradeOut, heuristic, lambda: None)
        post = {"attribution": attr, "facts": facts}
        lessons = ""
        if out:
            post |= out.model_dump()
            lessons = out.lessons
            recent = (self.journal.get_state("recent_lessons", []) or [])[-19:]
            self.journal.set_state("recent_lessons", recent + [f"[{trade.asset} {trade.setup} {trade.result}] {lessons}"])
        self.journal.update_trade(trade.trade_id, post_trade=post, lessons=lessons)
        return self.journal.get_trade(trade.trade_id)
