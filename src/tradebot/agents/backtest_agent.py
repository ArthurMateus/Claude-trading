"""🔬 Backtest Agent — validates each setup historically; only validated setups may be traded.

Runs pooled across the universe with fees + slippage, and requires the out-of-sample half to pass too.
An LLM review may VETO a numerically-passing setup (e.g. results driven by a handful of outliers), never
approve a failing one.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from ..backtest import backtest_setup_pooled
from ..data.providers import MarketDataProvider
from ..strategies import SETUPS
from .base import Agent


class BacktestReview(BaseModel):
    veto: bool
    rationale: str


class BacktestAgent(Agent):
    name = "backtest"
    job = ("review backtest statistics for a rule-based setup that already passed numeric thresholds and veto it "
           "only if the results look unreliable (too few trades, in-sample vs out-of-sample divergence, "
           "profit concentrated in rare outliers).")

    def passes(self, st: dict) -> list[str]:
        v = self.settings.validation
        fails = []
        if st["trades"] < v.min_trades:
            fails.append(f"trades {st['trades']} < {v.min_trades}")
        if st["profit_factor"] < v.min_profit_factor:
            fails.append(f"PF {st['profit_factor']:.2f} < {v.min_profit_factor}")
        if st["expectancy_bps"] < v.min_expectancy_bps:
            fails.append(f"expectancy {st['expectancy_bps']:.1f}bps < {v.min_expectancy_bps}")
        if st["max_drawdown_pct"] > v.max_drawdown_pct:
            fails.append(f"maxDD {st['max_drawdown_pct']:.1f}% > {v.max_drawdown_pct}")
        return fails

    def validate(self, provider: MarketDataProvider) -> dict[str, dict]:
        s = self.settings
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=s.validation.lookback_days)
        bars = {}
        for asset in s.universe:
            try:
                bars[asset] = provider.bars(asset, s.bar_timeframe_minutes, start=start, end=end)
            except Exception as e:
                self.journal.log_event("api_error", f"backtest bars {asset}: {e}", "WARN")
        results = {}
        for name in SETUPS:
            r = backtest_setup_pooled(bars, name, s.costs.taker_fee_bps, s.costs.sim_slippage_bps, s.risk.base_risk_pct)
            fails = self.passes(r["full"]) + [f"OOS {f}" for f in self.passes({**r["oos"], "trades": max(r["oos"]["trades"], s.validation.min_trades)})]
            passed = not fails
            review = None
            if passed and self.llm.online:
                review = self.llm.structured(self.name, self.system_prompt, r, BacktestReview)
                if review and review.veto:
                    passed, fails = False, [f"llm veto: {review.rationale}"]
            results[name] = {"passed": passed, "fails": fails, "full": r["full"], "oos": r["oos"],
                             "validated_at": end.isoformat()}
            self.journal.log_decision("*", "backtest", "PASS" if passed else "FAIL", results[name])
        self.journal.set_state("setup_validation", results)
        return results

    def validated_setups(self) -> dict[str, dict]:
        """setup -> full-sample stats, for setups that passed and are not stale."""
        res = self.journal.get_state("setup_validation", {}) or {}
        max_age = timedelta(hours=self.settings.validation.max_age_hours)
        now = datetime.now(timezone.utc)
        out = {}
        for name, r in res.items():
            if r.get("passed") and now - datetime.fromisoformat(r["validated_at"]) <= max_age:
                out[name] = r["full"]
        return out

    def is_stale(self) -> bool:
        res = self.journal.get_state("setup_validation")
        if not res:
            return True
        ts = min(datetime.fromisoformat(r["validated_at"]) for r in res.values())
        return datetime.now(timezone.utc) - ts > timedelta(hours=self.settings.validation.max_age_hours)
