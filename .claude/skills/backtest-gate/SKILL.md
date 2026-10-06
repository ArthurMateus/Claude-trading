---
name: backtest-gate
description: Run tradebot's Backtest gate (validate every setup with fees, slippage and an out-of-sample check) and explain which setups may trade and why. Use when asked to validate, re-validate or check setups.
---
1. Run `python3 -m pytest -q` first; stop and report if anything fails.
2. Run the gate:
   - real data (needs Alpaca keys in .env): `tradebot validate`
   - offline: `tradebot --mode simulated --offline validate` (synthetic random walk; setups are EXPECTED to fail here)
3. For each setup report: PASS/FAIL, trades, win rate, profit factor, expectancy (bps), max drawdown,
   full vs out-of-sample, and the failing thresholds.
4. Interpret: a setup that only passes in-sample is overfit; expectancy under ~10 bps is fragile against
   fee/slippage changes; under 40 trades is noise.
5. Never lower `validation:` thresholds to make a setup pass. Propose setup changes instead, and have the
   `quant-validator` sub-agent review them.
