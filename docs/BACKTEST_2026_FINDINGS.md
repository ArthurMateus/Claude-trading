# 2026 out-of-sample test: findings (run 2026-10-06)

Raw output: `research/results/2026/` (REPORT.md, strategies.csv). Freeze `54a2e513…` committed 13:24 UTC,
single ledgered test run 13:43 UTC, no 2026 data cached before the freeze, not contaminated.
1,152 configurations searched (12 families × params × long/short × 5m/15m/1h × 4 exits), 36 champions.

## Headline (pre-registered)

Alpaca spot, validated strategies, 1% risk, 1x, circuit breakers on: **no strategy qualified, so the bot would not
have traded in 2026.** Its $500 stays $500. Every alternative on Alpaca spot did worse (see below), so the gates
did their job.

## Alpaca spot (the bot's venue): every strategy loses

- In-sample profit factors were 0.24–0.57. In 2026, 10 of 12 lost **43–89%** of the account even at 1% risk.
- Random entries with the same exits lose about −75 bps per trade: that is the **fee + slippage drag of a round
  trip (0.6–0.8%)**, which exceeds the typical 1–4h move these rules capture.
- The one "profitable" spot strategy (climax_reversal long, +4.5%) has 32 trades (noise). Its gains were all in
  Jan–Jun (+70 bps/trade) and it was flat in Jul–now (−1 bps).

## Perp fees (8× cheaper): still no edge

Only 3 of 24 champions were positive at 1% risk, 1x:

| strategy | trades | PF | t-stat | Jan–Jun bps | Jul–now bps | 1%, 1x | 20%, 10x (max DD) |
|---|---|---|---|---|---|---|---|
| squeeze_breakout long 1h | 232 | 1.11 | 0.42 | −12 | **+36** | +8.4% | +138% (73% DD) |
| climax_reversal long 1h | 60 | 2.03 | 1.14 | **+69** | −33 | +5.2% | +39% (46% DD) |
| mtf_pullback long 1h | 10 | 1.17 | 0.13 | −8 | +35 | +0.5% | +2% (noise) |

- None is statistically significant (t < 2). Both real candidates failed the 2022–2024 and 2025 gates
  (squeeze: train PF 0.90, val 1.02; climax: 0.85 / 0.69).
- climax_reversal made all its money in Jan–Jun, the period Claude's training data overlaps, and lost in the
  clean Jul–now window. squeeze_breakout is the reverse (positive in Jul–now): the only result worth watching.
- "5 beat the random-entry null vs 1.8 by chance" mostly means *losing less than random*: 3 of the 5 still had
  negative expectancy.

## Risk and leverage

With negative expectancy, more risk and leverage only reach ruin faster. Typical perp champion: about −20 to −35%
at 1%/1x, **−95 to −99% at 5–20% risk with 10x**. The best case (squeeze long, +138% at 20%/10x) went through a
73% drawdown on a strategy with t = 0.42. That's a lottery ticket, not an edge, and the bot's 15% drawdown
halt would have stopped it early.

## Conclusions

1. These 12 intraday families have **no demonstrable edge after costs** on 1–4h crypto, on either venue.
2. On Alpaca spot, intraday trading of this kind cannot work. The fees alone exceed the moves.
3. Keep the bot on paper. Do not go live, and do not raise risk or leverage.
4. 2026 has now been viewed: any new search must run with `--contaminated`, and its real test is **forward paper
   trading from today**, not 2026.

## Options worth testing next

- **Longer holds (daily bars, multi-day swings).** Fees become a small fraction of the move. This is the most
  promising change, but it departs from the "couple of hours or less" goal.
- **Cut costs:** maker-only (limit) entries on Alpaca, or a low-fee futures venue (needs a new broker adapter).
- **squeeze_breakout long (1h)** as a forward paper-test candidate only, with small size, judged on new data.

---

# Research v2 (run 2026-10-06 18:55 UTC): holds ≤ 24h, long + short, cross-asset and funding families

Freeze `e033f5db…` (contaminated: 2026 had been viewed twice before; ledger entry 3). 1,720 configurations,
17 families, 15m/1h/4h, exits with 8h/24h holds, real Binance funding costs, 51 champions. Raw output replaced
v1's in `research/results/2026/` (v1 numbers above remain the record of run 1).

**Headline: still no trade.** None of the 51 champions passed 2025 validation. The best 2025 t-stat was 1.35,
so even v1's weaker bar (t ≥ 2) would have passed none. Strategies that looked good on 2022–2024 did not hold
up on 2025.

- **Timing null:** 2 of 51 beat it, 2.6 expected by pure chance. Both have fewer than 20 trades (btc_lead long
  on spot with 14, btc_lead short on perp with 18), so they are noise.
- **Alpaca spot:** 15 of 17 long strategies lost in 2026 (−44 to −187 bps per trade). Random entries with the
  same exits lose about −75 bps. Longer holds did not overcome the 0.6–0.8% round-trip cost.
- **Perp (reference portfolio of the 16 champions with an in-sample edge, all rejected by 2025):** −23% at 1%
  risk 1x; −64% at 2x; −92% to −99% at 5x and above. With circuit breakers every cell stops near −15% (the
  drawdown halt), mostly within the first weeks. Leverage magnified a negative edge.
- **Jan–Jun vs Jul–now:** many long strategies were negative in H1 and positive in H2, with shorts the reverse.
  The null shows the same split, so this is market direction, not skill.

## What this means

Across 2,872 configurations in two independent searches (v1 intraday, v2 ≤ 24h), nothing that is both
statistically defensible on 2025 and positive on 2026 exists among these rule families on these 7 coins.
Further searches on 2026 are now pure curve-fitting; forward paper trading is the only honest test left.
