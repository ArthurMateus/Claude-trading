---
name: add-setup
description: Add or change a rule-based trade setup in src/tradebot/strategies.py and take it through the Backtest gate. Use when the user describes a trading idea/rule they want the bot to trade.
---
1. Restate the user's rule precisely: entry condition on CLOSED bars, side, stop (ATR multiple), target
   (reward:risk), max hold (bars of `bar_timeframe_minutes`). Ask about anything ambiguous.
2. Implement it as a `Setup` in `SETUPS` using only `indicators.py` helpers (add a helper there if needed).
   Any "prior high/low" must be `.shift(1)`ed. Write a one-line `description` (the LLM agents read it).
3. Add a unit test proving no lookahead (signal at bar i must not change when bars after i are altered).
4. Run `python3 -m pytest -q`, then the `backtest-gate` skill.
5. Ask the `quant-validator` sub-agent to review the diff and the gate results.
6. Report honestly: if it fails the gate, it does not trade. Do not tune parameters on the same data until it passes.
