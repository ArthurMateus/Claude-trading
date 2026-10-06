# Agentic SDLC

Two lifecycles run side by side: the **software** (built and changed by Claude Code agents under human
review) and the **strategy** (setups and parameters promoted by evidence, never by opinion).

## 1. Software lifecycle (Claude Code builds, a human approves)

| Phase | Who | Output | Gate |
|---|---|---|---|
| Spec | Human + Claude | Entry in `docs/AGENTS.md`, answers in `docs/OPEN_QUESTIONS.md` | Human agrees on the spec |
| Build | Claude Code (`agent-builder` sub-agent) | Code + tests on a feature branch | `pytest` green |
| Review | `risk-reviewer` (any diff touching guardrails/risk/kill-switch/execution/config limits), `quant-validator` (strategies/backtest) | Review notes | No unresolved red findings |
| Merge | Human | PR merged | CI green + human approval |

Rules (enforced by CLAUDE.md):
- every behavior change ships with a test; `tests/test_guardrails.py` must never be weakened
- guardrail **values** in `config/settings.yaml` (`risk:`, `kill_switch:`, `promotion:`) change only with
  explicit human approval in the PR description
- no code path may let an LLM raise risk above `guardrails.check_trade`

## 2. Strategy lifecycle (evidence decides)

```
 idea / post-trade lesson ─▶ new or changed SETUP (strategies.py, via PR)
        ▲                                │
        │                     🔬 Backtest gate (tradebot validate)
        │                     fees+slippage, OOS half, PF≥1.2, E≥5bps, DD≤15%
        │                                │ pass
        │                     🧪 Paper gate (≥28 days, ≥100 trades, PF≥1.2,
        │                        DD≤10%, slippage within 10bps of model)
        │                                │ PROMOTE
        │                     👤 Human: TRADEBOT_ALLOW_LIVE + mode: live
        │                        start with 10–20% of intended capital
        │                                │
        └──── 📋 Post-trade attribution, weekly review ◀── live trades
```

- Setups are re-validated weekly (`validation.max_age_hours`). A setup that stops passing stops trading automatically.
- The learning loop only adjusts **agent trust weights** (bounded ±20% per step, range 0.25–2.0) and feeds recent
  lessons into the orchestrator prompt. It never edits setups, limits or code. Those go through the PR loop.
- Confidence → size scaling is unlocked per bucket by the calibration rule in `guardrails.allowed_risk_pct`.

## 3. Autonomy matrix

| Action | Autonomous | Human |
|---|---|---|
| Scan, decide, size, enter, exit, journal | ✅ | |
| Pause entries (loss streak, errors, slippage, budget) and auto-resume | ✅ | |
| Halt for the day on daily loss limit | ✅ (resumes next UTC day) | |
| Hard HALT + flatten (drawdown ≥ 15%, runaway orders) | ✅ trigger | reset via `tradebot reset-halt` |
| Adjust agent weights / use lessons | ✅ bounded | |
| Change setups, limits, code | Claude drafts a PR | approve + merge |
| Go live / add capital | | ✅ |

## 4. Operating cadence

- **Every 5 min**: one pipeline cycle (`tradebot run`).
- **Daily**: equity, P&L and LLM-spend summary (`tradebot report`).
- **Weekly**: `weekly-review` skill: journal stats, calibration per bucket, agent accuracy, setup re-validation,
  proposals as PRs.
- **Promotion check**: Paper-Trading Agent verdict is updated every paper cycle (`tradebot report` shows it).
