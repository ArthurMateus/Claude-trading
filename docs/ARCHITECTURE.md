# Architecture

```
                 ┌────────────────────────────┐
                 │  📡 MARKET DATA AGENT       │  bars, order book, news (Alpaca)
                 │  clean AssetState / asset   │  + Haiku data-sanity/regime review
                 └─────────────┬──────────────┘
                               ↓
  manage open positions (stop filled? take-profit? time-stop?) → 📋 Post-Trade → learning loop
                               ↓
                 ┌────────────────────────────┐
                 │  🛡️ KILL-SWITCH             │  OK / PAUSE_ENTRIES / HALT_DAY / HALT(+flatten)
                 └─────────────┬──────────────┘
                               ↓  only assets where a VALIDATED setup is firing (cost gate)
        ┌──────────────────────────────────────────────────┐
        │              INTELLIGENCE LAYER (parallel)        │
        │ 📊 Technical  📈 Quant  📰 News  🐋 Flow           │
        │ 🏦 Fundamental (off) → then 👀 Replication         │
        │ (reads the others' votes + track records)         │
        └──────────────────────┬───────────────────────────┘
                               ↓  weighted vote ≥ prefilter
                 ┌────────────────────────────┐
                 │ 🧠 ORCHESTRATOR (Opus 5.5)  │  TradeCandidate: setup, confidence, reason
                 └─────────────┬──────────────┘
                               ↓  setup must be in 🔬 Backtest Agent's validated set
                 ┌────────────────────────────┐
                 │ ⚠️ RISK AGENT + guardrails  │  APPROVE/REJECT, qty, stop, target
                 └─────────────┬──────────────┘
                               ↓
                 ┌────────────────────────────┐
                 │ 🎯 PORTFOLIO AGENT          │  which approved plans, in what order
                 └─────────────┬──────────────┘
                               ↓  guardrails re-checked against the updated portfolio
                 ┌────────────────────────────┐
                 │ ⚡ EXECUTION AGENT          │  IOC marketable limit + resting stop
                 └─────────────┬──────────────┘
                               ↓
                     ALPACA (paper | live)  →  📒 Journal (SQLite)
                               ↓
                 🧪 Paper-Trading Agent: PROMOTE / CONTINUE / FAIL
```

## Design principles

1. **Code computes, LLMs judge.** Indicators, setup statistics, sizing, stops and P&L are deterministic Python.
   LLMs read those numbers and make judgment calls (direction, confidence, event risk, veto).
2. **LLMs can only make things more conservative in safety paths.** Risk, Kill-Switch, Market-Data and Backtest
   LLM outputs can reject, shrink, pause or veto, never expand. `guardrails.py` has the last word.
3. **No trade without a validated setup.** This makes the system testable and caps LLM spend.
4. **Fail closed.** An LLM error, refusal, invalid JSON or exhausted budget means the agent abstains, which means
   no trade. Offline mode (`--offline`) swaps every LLM call for a deterministic heuristic, used for tests and dry runs.
5. **Every decision is journaled**: trades, rejected candidates and their reasons, kill-switch events, LLM spend.

## Modules

| Path | Role |
|---|---|
| `src/tradebot/contracts.py` | Typed messages between agents (AssetState, AgentSignal, TradeCandidate, RiskPlan, TradeRecord …) |
| `src/tradebot/config.py` + `config/settings.yaml` | All tunables; `risk:` / `kill_switch:` are human-owned |
| `src/tradebot/guardrails.py` | Hard limits + earned (calibrated) sizing — pure functions, heavily tested |
| `src/tradebot/strategies.py` | Named rule-based setups the LLMs are allowed to trade |
| `src/tradebot/backtest.py` | Conservative bar backtester (next-bar fills, fees, stop-first) |
| `src/tradebot/llm.py` | Claude wrapper: per-role model, structured output, refusal fallback, cost + budget |
| `src/tradebot/journal.py` | SQLite journal: trades, decisions, events, equity, llm_usage, state |
| `src/tradebot/pipeline.py` | One cycle end-to-end; `run_forever` loop |
| `src/tradebot/learning.py` | Bounded agent-weight updates from attribution |
| `src/tradebot/agents/*.py` | The 15 agents |
| `src/tradebot/brokers/` | `AlpacaBroker` (paper/live), `SimulatedBroker` |
| `src/tradebot/data/providers.py` | `AlpacaProvider`, `SyntheticProvider` |
| `src/tradebot/data/news_sources.py` | `NewsHub`: Alpaca, RSS, Reddit, X (capped, budgeted), CryptoPanic |

## Models (Option 3: Opus orchestrates, cheaper sub-agents)

| Role | Model | Effort |
|---|---|---|
| Orchestrator | claude-opus-5-5 | high |
| Quant, Fundamental, Replication, Portfolio | claude-sonnet-5-5 | low |
| Risk, Post-Trade | claude-sonnet-5-5 | medium |
| Market Data*, News, Technical, Flow, Execution, Kill-Switch*, Backtest review | claude-haiku-4-5 | n/a |

\* per-cycle reviews are disabled in the $500 profile (fixed daily cost); deterministic checks still run.

Opus 5.5 and Sonnet 5.5 calls send `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`), so a
safety-classifier refusal is retried server-side on a fallback model instead of failing the call.
All calls use JSON-schema structured outputs and prompt caching on the system prompt.
