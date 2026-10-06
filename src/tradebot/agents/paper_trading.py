"""🧪 Paper-Trading Agent — live validation without real money, and the promotion gate to live.

The pipeline runs unchanged in `paper` mode against Alpaca's paper account; this agent scores those
results and decides PROMOTE / CONTINUE / FAIL. Promotion to live is never automatic: it records the
verdict, and a human must still set TRADEBOT_ALLOW_LIVE and mode: live.
"""
from __future__ import annotations

from datetime import timezone

from .base import Agent


class PaperTradingAgent(Agent):
    name = "paper_trading"

    def evaluate(self) -> dict:
        p = self.settings.promotion
        trades = list(reversed(self.journal.closed_trades(mode="paper")))
        if not trades:
            verdict = {"verdict": "CONTINUE", "reasons": ["no paper trades yet"], "trades": 0}
            self.journal.set_state("promotion", verdict)
            return verdict
        pnl = [t.pnl_usd or 0 for t in trades]
        gross_win = sum(x for x in pnl if x > 0)
        gross_loss = -sum(x for x in pnl if x < 0)
        pf = gross_win / gross_loss if gross_loss else float("inf")
        days = (trades[-1].closed_at - trades[0].timestamp).total_seconds() / 86400
        slips = [t.slippage_bps for t in trades if t.slippage_bps is not None]
        avg_slip = sum(slips) / len(slips) if slips else 0.0
        model_slip = 2 * self.settings.costs.sim_slippage_bps
        equity, peak, max_dd = 1.0, 1.0, 0.0
        for t in trades:
            equity *= 1 + (t.r_multiple or 0) * t.risk_pct / 100
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak * 100)
        reasons, fail = [], []
        if len(trades) < p.min_trades:
            reasons.append(f"{len(trades)}/{p.min_trades} trades")
        if days < p.min_days:
            reasons.append(f"{days:.1f}/{p.min_days} days")
        if pf < p.min_profit_factor:
            (fail if len(trades) >= p.min_trades else reasons).append(f"profit factor {pf:.2f} < {p.min_profit_factor}")
        if max_dd > p.max_drawdown_pct:
            fail.append(f"drawdown {max_dd:.1f}% > {p.max_drawdown_pct}%")
        if avg_slip - model_slip > p.max_slippage_vs_model_bps:
            fail.append(f"slippage {avg_slip:.1f}bps vs model {model_slip:.1f}bps")
        verdict = "FAIL" if fail else "CONTINUE" if reasons else "PROMOTE"
        out = {"verdict": verdict, "reasons": fail + reasons, "trades": len(trades), "days": round(days, 1),
               "profit_factor": pf, "max_drawdown_pct": max_dd, "avg_slippage_bps": avg_slip,
               "win_rate": sum(t.result == "WIN" for t in trades) / len(trades),
               "net_pnl_usd": sum(pnl), "as_of": trades[-1].closed_at.astimezone(timezone.utc).isoformat()}
        self.journal.set_state("promotion", out)
        return out
