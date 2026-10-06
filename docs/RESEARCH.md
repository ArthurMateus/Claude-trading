# Strategy research: design on ≤ 2025, test once on 2026

The question this answers: *which strategies would have worked in 2026 if they had been designed using only data
up to the end of 2025, and what would different risk levels (1–20% per trade) and leverage (1–20x) have done to a
$500 account?*

## The protocol (fixed before any 2026 data is seen)

| Period | Used for | Enforcement |
|---|---|---|
| 2022-01-01 → 2024-12-31 | **design**: grid search over 12 strategy families × parameters × long/short × 5m/15m/1h × 4 exit rules (~1,000 configurations per asset universe) | `history.load` |
| 2025-01-01 → 2025-12-31 | **selection**: each family's in-sample champion must hold up on a year it never saw | `search.select` |
| freeze | champions + rules + cost models written to `research/frozen.json` with a sha256, plus a hash of the research code | `search.freeze` |
| 2026-01-01 → today | **one-shot test** of the frozen champions, every risk level and leverage | `history.load` raises `LookaheadError` for 2026 unless called by `test2026.run`, which refuses to run if `frozen.json` or the research code changed |

**Selection rule** (per venue cost model, per family and side):
1. In-sample 2022–2024: ≥ 150 trades pooled over the 7 coins, profit factor ≥ 1.1 after costs, and positive
   expectancy in at least 2 of the 3 years. Among those, the highest t-stat wins.
2. Validation 2025: ≥ 40 trades, profit factor ≥ 1.1, positive expectancy. Otherwise the champion is still
   frozen and reported, flagged *not validated*.

Every champion is reported on 2026, including the failures, so the results can't be cherry-picked.

## Strategy families (`src/tradebot/research/families.py`)

| Family | Idea | Parameters searched |
|---|---|---|
| donchian_breakout | close beyond the prior N-hour high/low | lookback 4/12/24h, volume filter |
| squeeze_breakout | Bollinger squeeze, then a close outside the band | squeeze quantile, window |
| vol_breakout | today's UTC open ± k × yesterday's range | k 0.3/0.5/0.8 |
| momentum_burst | return z-score spike with volume | z 2/3, window 1/4h |
| session_breakout | break of the first hour of the US equity open or the UTC day | session |
| ema_cross | fast vs slow EMA, optional 1-day trend filter | 9/20 × 50/100, filter |
| mtf_pullback | 4×-timeframe EMA50/200 trend, pullback resuming through EMA20 | RSI level |
| rsi2_reversion | Connors RSI(2) extreme with the EMA200 trend | 5/10/20 |
| bb_reversion | close outside a Bollinger band in a flat regime | k, flatness |
| vwap_reclaim | close back through rolling VWAP on volume | window, volume |
| climax_reversal | capitulation / blow-off bar with a rejection wick | volume z, wick |
| taker_flow | aggressor (taker-buy) share of volume with the trend | threshold, window (Binance data only, not usable live on Alpaca) |

Exits: stop at 1.0/1.5/2.0 × ATR and reward:risk 1.5/2/3, 4-hour time stop. Each family has long and short forms.
Shorts only exist on the perp venue.

## Costs and venues

- `alpaca_spot`: 0.25% fee + 0.05% slippage per side, long-only, no leverage. This is what the bot trades today.
- `perp`: 0.05% fee + 0.03% slippage per side, 0.01% funding per 8h held, long and short, leverage up to 20x.
  Isolated-margin liquidation is modeled: margin = notional / leverage, lost if the move against the position
  reaches 1/leverage − 0.5% before the exit. **The bot cannot trade this venue yet.** These rows show what a
  futures account would have done.

Fills are conservative: next-bar-open entries, the stop wins when stop and target touch in one bar, gaps through
the stop fill at the worse open, and the take-profit never fills better than the target.

## Risk × leverage

Each combined portfolio (all validated strategies of a venue) is replayed at 1, 2, 3, 5, 7.5, 10, 15 and 20%
risk per trade and 1, 2, 3, 5, 10 and 20x leverage, from $500. Position size = risk × equity ÷ stop distance,
capped by leverage × equity across open positions (max 8). Each run is shown raw and with the bot's circuit
breakers: no new entries after a 5% daily loss, and a permanent halt at a 15% drawdown.

## Running it

```bash
pip install -e ".[research]"
tradebot research download     # Binance 5m candles 2022-2025 for the 7 coins (~100 MB, cached in data/history/)
tradebot research search       # ~3-5 min; writes research/frozen.json (commit it BEFORE testing)
tradebot research test2026     # downloads 2026, runs the frozen strategies once, writes research/results/2026/REPORT.md
tradebot research status
```
`search` refuses to overwrite an existing freeze unless you pass `--refreeze`. Re-searching after seeing the
2026 results makes 2026 in-sample, so the numbers stop meaning anything.

## Honest caveats

- **Multiple testing.** ~1,000 configurations were tried, and the best of many always looks good in-sample. The
  2025 validation and the 2026 test are what protect against that.
- **Model knowledge.** Claude's training data runs to mid-2026, so the strategy *families* were chosen by a
  model that has some knowledge of 2026 markets. The parameter selection is mechanical and frozen, which limits
  but doesn't eliminate this.
- **Binance as proxy.** Binance USDT spot prices stand in for Alpaca's USD pairs. Price paths match closely, but
  Alpaca's own books are thinner and real slippage may be higher than modeled.
- **Realized-equity drawdowns** are slightly understated versus mark-to-market. The worst intra-trade loss is
  reported separately.
- **Live guardrails differ.** The bot also enforces calibrated sizing (1% until earned), a 6% heat cap and a 30%
  notional cap. With those, 10–20% risk per trade can't happen live without changing limits, which needs your
  explicit approval.
