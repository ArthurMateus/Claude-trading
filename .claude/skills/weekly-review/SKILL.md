---
name: weekly-review
description: Weekly performance review of the tradebot journal — P&L, calibration per confidence bucket, agent accuracy, setup health, slippage, LLM spend, promotion status — ending with concrete proposals. Use when asked for a review, report or "how is the bot doing".
---
Data source: `data/journal.sqlite` (or `tradebot export data/trades.csv`). Use read-only SQL via
`python3 -c "import sqlite3; ..."` or pandas. Never modify the journal.

Produce, for the last 7 days and since inception:
1. Trades, win rate, net P&L (USD and % of equity), profit factor, average R, max drawdown (from `equity`).
2. Calibration: for each confidence bucket (config `calibration.bucket_edges`) predicted mean confidence vs
   realized win rate and trade count. Flag over-confidence.
3. Agent accuracy from `trades.post_trade -> attribution` and current `state.agent_weights`.
4. Per setup: trades, win rate, expectancy vs its backtest expectancy (`state.setup_validation`).
5. Slippage: average entry/exit slippage vs modelled (`costs.sim_slippage_bps` × 2).
6. Kill-switch events and errors (`events`), LLM spend per role (`llm_usage`) vs `llm.daily_budget_usd`.
7. Promotion verdict (`state.promotion`) and what's missing.
8. Top recurring lessons (`trades.lessons`).

End with at most 5 proposals, each tagged [config], [setup] or [agent], with the evidence for it. Proposals
that touch limits must say "needs user approval". Do not apply proposals; offer to open a PR.
