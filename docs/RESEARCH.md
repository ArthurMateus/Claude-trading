# Strategy research: design on ≤ 2025, test once on 2026

> **v2 (after the first 2026 run):** holds up to 24h (8h/24h time exits), timeframes 15m/1h/4h (5m dropped),
> 5 new families (tsmom, xs_momentum, btc_lead, funding_contrarian, daily_reversal; the last two use Binance
> USD-M funding rates), long and short. Run it with `tradebot research search --refreeze --contaminated`: the
> report will say 2026 was already viewed. Results of run 1 are in `docs/BACKTEST_2026_FINDINGS.md`.

The question this answers: *which strategies would have worked in 2026 if they had been designed using only data
up to the end of 2025, and what would different risk levels (1–20% per trade) and leverage (1–20x) have done to a
$500 account?*

## The protocol (fixed before any 2026 data is seen)

| Period | Used for | Enforcement |
|---|---|---|
| 2022-01-01 → 2024-12-31 | **design**: grid search over 12 strategy families × parameters × long/short × 5m/15m/1h × 4 exit rules (~1,000 configurations per asset universe) | `history.load` |
| 2025-01-01 → 2025-12-31 | **selection**: each family's in-sample champion must hold up on a year it never saw | `search.select` |
| freeze | champions, rules, cost models and the **pre-registered headline** written to `research/frozen.json` with a sha256, plus a hash of all code that affects results (incl. indicators, portfolio, test runner) | `search.freeze` |
| 2026-01-01 → today | **one-shot test** of the frozen champions, every risk level and leverage | `history.load` raises `LookaheadError` for 2026 unless called by `test2026.run`, which refuses to run if `frozen.json` or that code changed. Every run is appended to `research/test_ledger.jsonl`. Once 2026 has been viewed, a new search needs `--contaminated` and the report says so. 2026 cache files older than the freeze are flagged. |

**Pre-registered headline:** `alpaca_spot` (the venue the bot trades), validated strategies only, 1% risk, 1x,
circuit breakers on. The report prints this first. Every other cell is sensitivity analysis.

**Selection rule** (per venue cost model, per family and side):
1. In-sample 2022–2024: ≥ 150 non-overlapping trades pooled over the 7 coins, profit factor ≥ 1.1 after costs,
   and positive expectancy in at least 2 of the 3 years. Among those, the highest **day-clustered** t-stat wins,
   so simultaneous BTC/ETH/SOL trades on one move don't count as independent evidence.
2. Validation 2025: ≥ 100 trades, profit factor ≥ 1.1, positive expectancy **and** a day-clustered t-stat ≥ 3
   (v2; v1 used 2). With ~50 champions (family × side × venue), t ≥ 2 would pass one or two zero-edge
   strategies by chance; t ≥ 3 passes about 0.07 in expectation. Champions that fail are still frozen and
   reported, flagged *not validated*.

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

Exits (v2): (stop ATR × reward:risk × max hold) = 1.0×2/8h, 1.5×2/8h, 2.0×3/24h, 3.0×10/24h (the last is
in practice a 24h time exit with a wide stop: a 30-ATR target almost never fills in a day). Each family has long and short forms. Shorts only exist on the
perp venue.

New in v2:

| Family | Idea | Parameters searched |
|---|---|---|
| tsmom | vol-scaled 1–7 day return beyond a threshold (trend) | lookback 24/72/168h, z 0.5/1.5 |
| xs_momentum | at each rebalance, long the k strongest / short the k weakest of the 7 coins | lookback 24/72h, k 1/2, rebalance 8/24h |
| btc_lead | after an outsized BTC move, trade alts that haven't followed yet | window 1/4h, z 1.5/2.5 |
| funding_contrarian | fade unusually high (short) / low (long) perpetual funding | z 1.5/2.5, window 7/30d |
| daily_reversal | fade an extreme 24h move | z 2/3 |

Funding rates come from Binance's monthly archives, which publish after each month ends. The current month has
no funding data, so funding signals stop at the last published settlement plus 16h (the report lists the last
settlement per coin). Rates are converted to a per-8h basis for signals, since Binance moved many perps to 4h
or 1h settlements.

## Costs and venues

- `alpaca_spot`: 0.25% fee per side plus slippage of 0.05% (BTC, ETH) or 0.15% (thinner Alpaca books: SOL,
  LTC, LINK, AVAX, DOGE) per side, long-only, no leverage. This is what the bot trades today.
- `perp`: 0.05% fee + 0.03% slippage per side, plus the funding Binance actually settled while each trade was
  open (longs pay positive rates, shorts receive them; 0.01% per 8h where no rate is published yet), long and
  short, leverage up to 20x.
  Isolated-margin liquidation is modeled: margin = notional / leverage, lost if the move against the position
  reaches 1/leverage − 0.5% before the exit. **The bot cannot trade this venue yet.** These rows show what a
  futures account would have done.

Fills are conservative: next-bar-open entries, the stop wins when stop and target touch in one bar, gaps through
the stop fill at the worse open, and the take-profit never fills better than the target. Exits are timestamped
at the close of their bar, so their P&L can't be reused early. Trades of one configuration on one asset never
overlap, windows that span exchange data gaps are dropped, and resampled bars missing more than 5% of their
5-minute bars are discarded.

**Every strategy is compared with a timing null:** its own 2026 entries, all shifted by one random whole number
of days (wrapping within the window), re-priced with the same exits and costs, 200 times. That keeps trade
counts, time of day and cross-asset clustering (xs_momentum and btc_lead enter many coins at once), and removes
only the timing skill. `beats_null` means test expectancy above the null's 95th percentile; the report also says how many champions would beat it by pure chance (5%). Fewer than 40 test trades is flagged `noise`. Results are also split into **Jan–Jun** and
**Jul–now 2026**; the second half is the cleaner window (see caveats).

## Risk × leverage

Each combined portfolio (all validated strategies of a venue) is replayed at 1, 2, 3, 5, 7.5, 10, 15 and 20%
risk per trade and 1, 2, 3, 5, 10 and 20x leverage, from $500. Position size = risk × mark-to-market equity ÷
stop distance, capped by leverage × equity across open positions (max 8, one per asset). **Equity is marked to
market on 5-minute closes**, so drawdowns include open losses across correlated positions. Each run is shown
raw and with the bot's **circuit breakers only**: no new entries once equity is down 5% since UTC midnight;
at a 15% drawdown everything is closed and trading stops. The bot's other limits (calibrated sizing, heat cap,
notional cap) are deliberately not applied, since they would make 10–20% risk impossible.

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
  model that has some knowledge of Jan–Jun 2026 markets. The parameter selection is mechanical and frozen, which
  limits but doesn't eliminate this. Judge mainly by the **Jul–now** columns.
- **Survivorship.** The universe is today's Alpaca coins, so coins that collapsed (LUNA, FTT) are missing.
  That mostly biases cross-sectional strategies like xs_momentum.
- **Binance as proxy.** Binance USDT spot prices stand in for Alpaca's USD pairs. Price paths match closely, but
  Alpaca's own books are thinner and real slippage may be higher than modeled.
- **Intra-bar extremes** between 5-minute closes aren't in the drawdown (they are in each trade's liquidation
  check via its maximum adverse excursion).
- **Live guardrails differ.** The bot also enforces calibrated sizing (1% until earned), a 6% heat cap and a 30%
  notional cap. With those, 10–20% risk per trade can't happen live without changing limits, which needs your
  explicit approval.
