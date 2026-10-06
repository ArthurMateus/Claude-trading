# 2026 out-of-sample test

## Headline (pre-registered before testing)

`alpaca_spot` · validated strategies · 1% risk per trade · 1x · circuit breakers on:

**No result: no strategy passed validation for this venue, so the headline portfolio is empty (the bot would not have traded).**

Everything after this section is sensitivity analysis. Picking the best cell after the fact is selection on the test set.

## Integrity

- ⚠️ **CONTAMINATED:** this freeze was created after 2026 had already been viewed. The strategies were still selected only on 2022–2025, but the choice of families was made knowing earlier 2026 results, so 2026 is not a clean test. Forward paper trading is.
- ⚠️ 2026 was tested 1 time(s) before this run (research/test_ledger.jsonl).
- ⚠️ 70 2026 data file(s) existed on disk before the freeze.
- Test window **2026-01-01 → 2026-10-06** (278 days); assets AVAX/USD, BTC/USD, DOGE/USD, ETH/USD, LINK/USD, LTC/USD, SOL/USD; start equity $500.
- Frozen 2026-10-06T17:07:55.373823+00:00 (sha256 `6f4760b781ffd085…`, code `224ff0b4b628…`) after searching **1720 configurations** on 2022–2024 and selecting on 2025.
- Costs: `alpaca_spot` = 0.25% fee + slippage 0.05% (BTC/ETH) or 0.15% (others) per side, long-only, 1x. `perp` = 0.05% fee + 0.03% slippage per side + 0.01%/8h funding, long+short, ≤ 20x, liquidation modeled.
- Drawdowns are mark-to-market on 5-minute closes; one position per asset; max 8 positions.
- "Circuit breakers" = no new entries after a 5% daily loss, and close everything + stop at a 15% drawdown. The bot's other limits (calibrated sizing, 6% heat cap, 30% notional cap) are NOT applied here, which is why 10–20% risk is even possible.

## Strategy champions (one per family and side, per venue)

**4 champion(s) beat the random-entry null; about 2.6 would by pure chance** (5% of those with test trades).

`beats_null` = test expectancy above the 95th percentile of 200 random-entry runs with the same exits. `noise` = fewer than 40 test trades. Jan–Jun vs Jul–now: the families were designed by a model with training data to mid-2026, so Jul–now is the cleaner window.

### `alpaca_spot`

| family | side | tf | validated | train_pf | val_pf | val_t | test_trades | test_pf | test_exp_bps | test_t | exp_bps_jan_jun | exp_bps_jul_now | null_exp_bps | beats_null | noise | ret@1%,1x | ret@5%,1x | ret@10%,1x | ret@20%,1x |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bb_reversion | long | 240 | no | 0.33 | 0.58 | -1.02 | 86 | 0.43 | -82.64 | -1.92 | -118.39 | -0.13 | -76.21 | no | no | -14.4% | -30.6% | -33.6% | -33.6% |
| btc_lead | long | 240 | no | 1.19 | 0.37 | -0.80 | 14 | 2.64 | 141.76 | 1.10 | -178.84 | 270.00 | -80.56 | yes | yes | +3.9% | +5.1% | +5.1% | +5.0% |
| climax_reversal | long | 240 | no | 2.38 | 1.35 | 0.37 | 12 | 1.05 | 13.51 | 0.03 | 13.51 | - | -83.69 | no | yes | -3.0% | +0.0% | -0.6% | -0.6% |
| daily_reversal | long | 15 | no | 1.51 | 0.82 | -0.54 | 77 | 0.31 | -186.63 | -1.75 | -199.66 | 0.92 | -71.39 | no | no | -26.3% | -28.1% | -28.3% | -28.3% |
| donchian_breakout | long | 240 | no | 0.95 | 0.77 | -1.18 | 274 | 0.69 | -53.49 | -1.46 | -112.55 | 33.24 | -76.66 | no | no | -27.0% | -33.1% | -32.0% | -32.0% |
| ema_cross | long | 240 | no | 0.90 | 0.82 | -0.61 | 82 | 0.66 | -55.39 | -1.15 | -134.75 | 68.61 | -72.93 | no | no | -5.0% | -36.6% | -38.5% | -38.5% |
| funding_contrarian | long | 240 | no | 0.92 | 0.65 | -1.76 | 81 | 0.63 | -67.96 | -1.38 | -98.60 | 47.41 | -67.72 | no | no | -9.9% | -38.4% | -45.3% | -45.3% |
| momentum_burst | long | 240 | no | 0.99 | 0.48 | -1.65 | 86 | 0.60 | -81.61 | -1.26 | -216.61 | 30.40 | -72.49 | no | no | -13.5% | -18.5% | -15.2% | -15.2% |
| mtf_pullback | long | 15 | no | 0.70 | 0.58 | -1.13 | 25 | 0.41 | -78.20 | -1.55 | -155.75 | 38.13 | -67.47 | no | yes | -9.9% | -12.7% | -12.7% | -12.7% |
| rsi2_reversion | long | 60 | no | 0.82 | 0.53 | -3.13 | 363 | 0.45 | -80.79 | -3.79 | -95.46 | -61.98 | -75.78 | no | no | -52.6% | -67.4% | -67.6% | -67.6% |
| session_breakout | long | 60 | no | 0.75 | 0.52 | -3.56 | 555 | 0.50 | -84.13 | -2.91 | -117.96 | -24.56 | -76.43 | no | no | -58.8% | -64.1% | -64.1% | -64.1% |
| squeeze_breakout | long | 240 | no | 0.96 | 0.48 | -2.19 | 111 | 0.53 | -88.23 | -1.66 | -144.10 | 23.52 | -72.12 | no | no | -12.7% | -20.5% | -17.2% | -17.2% |
| taker_flow | long | 240 | no | 0.64 | 0.57 | -2.14 | 118 | 0.48 | -83.70 | -2.57 | -116.02 | -31.27 | -77.78 | no | no | -21.8% | -45.3% | -46.4% | -46.4% |
| tsmom | long | 240 | no | 0.87 | 0.58 | -1.91 | 62 | 0.70 | -57.99 | -0.83 | -165.17 | -29.56 | -74.26 | no | no | -2.9% | -27.7% | -32.1% | -32.2% |
| vol_breakout | long | 60 | no | 0.73 | 0.56 | -2.96 | 364 | 0.70 | -44.20 | -1.41 | -69.62 | -5.37 | -75.87 | yes | no | -27.0% | -29.9% | -29.0% | -29.0% |
| vwap_reclaim | long | 240 | no | 1.04 | 0.92 | -0.26 | 134 | 0.57 | -80.53 | -1.30 | -86.06 | -67.53 | -75.14 | no | no | -21.3% | -43.6% | -44.9% | -45.0% |
| xs_momentum | long | 240 | no | 0.79 | 0.54 | -4.45 | 278 | 0.42 | -100.47 | -5.63 | -111.45 | -79.99 | -76.05 | no | no | -49.4% | -93.4% | -94.7% | -94.7% |


### `perp`

| family | side | tf | validated | train_pf | val_pf | val_t | test_trades | test_pf | test_exp_bps | test_t | exp_bps_jan_jun | exp_bps_jul_now | null_exp_bps | beats_null | noise | ret@1%,3x | ret@5%,3x | ret@10%,3x | ret@20%,3x |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bb_reversion | long | 15 | no | 0.93 | 0.86 | -1.34 | 1462 | 0.75 | -22.50 | -2.40 | -28.92 | -8.69 | -14.01 | no | no | -87.4% | -96.3% | -96.0% | -96.0% |
| bb_reversion | short | 240 | no | 0.67 | 0.79 | -0.63 | 70 | 0.91 | -11.39 | -0.24 | -34.53 | 19.45 | -15.80 | no | no | -4.1% | -2.3% | -8.2% | -13.1% |
| btc_lead | long | 15 | no | 1.19 | 1.00 | -0.01 | 112 | 1.25 | 22.52 | 0.46 | -85.84 | 90.05 | -18.90 | yes | no | +5.3% | -21.2% | -18.8% | -10.9% |
| btc_lead | short | 240 | no | 0.99 | 2.39 | 0.88 | 18 | 3.14 | 155.04 | 0.87 | 187.78 | -8.67 | -13.78 | yes | yes | +9.8% | -2.8% | -6.0% | -6.0% |
| climax_reversal | long | 240 | no | 2.88 | 1.84 | 0.79 | 12 | 1.34 | 73.01 | 0.19 | 73.01 | - | -24.10 | no | yes | -1.8% | -0.7% | +11.5% | +4.0% |
| climax_reversal | short | 15 | no | 1.09 | 1.30 | 1.21 | 113 | 0.84 | -11.74 | -0.73 | -18.90 | 0.35 | -10.49 | no | no | -10.7% | -28.2% | -31.7% | -31.7% |
| daily_reversal | long | 15 | no | 1.81 | 1.10 | 0.28 | 86 | 0.56 | -73.86 | -0.93 | -74.45 | -64.32 | -16.75 | no | no | -33.5% | -42.3% | -42.9% | -43.0% |
| daily_reversal | short | 60 | no | 0.93 | 1.17 | 0.33 | 80 | 0.50 | -67.36 | -1.78 | -11.51 | -102.70 | -15.02 | no | no | -20.2% | -48.1% | -58.8% | -64.2% |
| donchian_breakout | long | 240 | no | 1.28 | 1.06 | 0.24 | 274 | 1.02 | 2.57 | 0.07 | -56.20 | 88.87 | -20.67 | no | no | +6.9% | -5.3% | +20.8% | +16.3% |
| donchian_breakout | short | 240 | no | 1.21 | 0.80 | -0.80 | 219 | 0.85 | -13.38 | -0.41 | -2.93 | -51.64 | -14.97 | no | no | -31.2% | -64.1% | -64.4% | -65.8% |
| ema_cross | long | 240 | no | 1.37 | 1.25 | 0.78 | 84 | 0.89 | -7.55 | -0.34 | -23.26 | 17.99 | -17.63 | no | no | -3.0% | -31.4% | -41.7% | -41.7% |
| ema_cross | short | 240 | no | 1.27 | 1.34 | 0.90 | 77 | 0.99 | -1.24 | -0.03 | 42.87 | -87.75 | -18.61 | no | no | -1.5% | -17.3% | -18.8% | -21.3% |
| funding_contrarian | long | 240 | no | 1.24 | 0.91 | -0.40 | 81 | 0.91 | -14.34 | -0.29 | -45.12 | 101.52 | -14.13 | no | no | +0.4% | -6.6% | -27.7% | -65.1% |
| funding_contrarian | short | 60 | no | 1.17 | 2.56 | 1.32 | 15 | 1.22 | 16.97 | 0.32 | 46.28 | -16.52 | -8.43 | no | yes | -1.4% | +0.7% | +5.1% | +5.1% |
| momentum_burst | long | 60 | no | 1.12 | 1.05 | 0.29 | 538 | 0.97 | -3.87 | -0.15 | -34.85 | 61.50 | -18.63 | no | no | -4.3% | -13.4% | +51.5% | +54.3% |
| momentum_burst | short | 240 | no | 0.93 | 0.42 | -1.89 | 62 | 0.77 | -29.30 | -0.48 | 8.27 | -121.16 | -14.78 | no | no | -11.9% | -41.2% | -54.5% | -59.4% |
| mtf_pullback | long | 15 | no | 1.08 | 0.91 | -0.18 | 25 | 0.73 | -24.26 | -0.50 | -100.63 | 90.31 | -13.95 | no | yes | -3.2% | -7.7% | -8.7% | -8.7% |
| mtf_pullback | short | 240 | no | 2.19 | 1.09 | 0.07 | 9 | 0.64 | -22.57 | -0.77 | -5.61 | -158.26 | -15.09 | no | yes | -1.8% | -6.7% | -6.2% | -6.2% |
| rsi2_reversion | long | 60 | no | 1.18 | 0.78 | -1.21 | 363 | 0.77 | -25.31 | -1.26 | -39.64 | -6.94 | -20.20 | no | no | -23.6% | -67.9% | -76.0% | -79.6% |
| rsi2_reversion | short | 240 | no | 1.13 | 1.06 | 0.26 | 272 | 0.76 | -27.97 | -0.92 | -26.15 | -36.25 | -17.45 | no | no | -15.1% | -36.3% | -21.7% | +15.2% |
| session_breakout | long | 60 | no | 1.07 | 0.78 | -1.37 | 555 | 0.79 | -28.05 | -0.98 | -61.79 | 31.36 | -20.20 | no | no | -43.5% | -70.9% | -74.6% | -74.0% |
| session_breakout | short | 15 | no | 1.00 | 0.88 | -0.84 | 661 | 0.81 | -17.86 | -1.19 | -4.88 | -45.36 | -10.36 | no | no | -54.5% | -27.0% | -22.9% | -22.8% |
| squeeze_breakout | long | 240 | no | 1.30 | 0.67 | -1.20 | 111 | 0.78 | -33.28 | -0.63 | -89.51 | 79.18 | -17.17 | no | no | -2.7% | -13.4% | -20.2% | -14.6% |
| squeeze_breakout | short | 240 | no | 1.19 | 1.06 | 0.17 | 66 | 0.47 | -50.25 | -1.57 | -43.69 | -70.73 | -13.74 | no | no | -15.2% | -35.7% | -39.0% | -39.3% |
| taker_flow | long | 240 | no | 1.04 | 0.88 | -0.47 | 118 | 0.78 | -26.63 | -0.83 | -59.61 | 26.86 | -20.77 | no | no | -5.9% | -21.1% | -48.4% | -54.8% |
| taker_flow | short | 240 | no | 1.17 | 1.04 | 0.18 | 233 | 1.06 | 6.10 | 0.28 | 36.80 | -52.61 | -9.58 | no | no | -1.0% | -27.8% | -1.3% | +35.9% |
| tsmom | long | 240 | no | 1.18 | 0.82 | -0.70 | 62 | 0.99 | -1.09 | -0.02 | -107.06 | 27.02 | -17.38 | no | no | +3.2% | +0.2% | -24.8% | -44.6% |
| tsmom | short | 240 | no | 1.20 | 0.58 | -2.21 | 88 | 0.72 | -29.29 | -0.76 | -30.18 | -17.16 | -14.69 | no | no | -10.6% | -18.6% | -13.2% | -14.8% |
| vol_breakout | long | 15 | no | 1.06 | 0.97 | -0.17 | 447 | 1.06 | 5.44 | 0.23 | -17.81 | 39.60 | -13.99 | no | no | +6.0% | +44.5% | +47.8% | +51.0% |
| vol_breakout | short | 240 | no | 0.97 | 1.03 | 0.12 | 330 | 0.93 | -5.20 | -0.22 | 23.94 | -63.50 | -14.62 | no | no | -32.3% | -55.5% | -62.7% | -63.0% |
| vwap_reclaim | long | 240 | no | 1.38 | 1.22 | 0.60 | 134 | 0.83 | -26.06 | -0.43 | -31.65 | -12.95 | -20.61 | no | no | -6.9% | -30.7% | -46.6% | -62.9% |
| vwap_reclaim | short | 60 | no | 0.94 | 0.87 | -0.88 | 381 | 0.92 | -7.45 | -0.39 | 12.64 | -43.65 | -10.87 | no | no | -25.1% | -49.2% | -49.5% | -53.1% |
| xs_momentum | long | 240 | no | 1.08 | 0.76 | -1.90 | 278 | 0.67 | -45.70 | -2.57 | -57.36 | -23.94 | -21.27 | no | no | -22.5% | -77.3% | -95.8% | -99.0% |
| xs_momentum | short | 240 | no | 0.98 | 0.97 | -0.21 | 278 | 0.87 | -14.85 | -0.82 | 7.04 | -55.70 | -13.93 | no | no | -17.4% | -66.7% | -88.1% | -89.1% |


## Combined portfolios: return by risk per trade × leverage (sensitivity)

### `alpaca_spot` · train_edge_reference · raw (1 strategies) — reference only, includes strategies 2025 rejected

Return % (max mark-to-market drawdown %):

| risk | 1x |
|---|---|
| 1.0% risk | -26.3% (28%) |
| 2.0% risk | -27.4% (29%) |
| 3.0% risk | -28.7% (30%) |
| 5.0% risk | -28.1% (30%) |
| 7.5% risk | -28.3% (30%) |
| 10.0% risk | -28.3% (30%) |
| 15.0% risk | -28.3% (30%) |
| 20.0% risk | -28.3% (30%) |


![heatmap](heatmap_alpaca_spot_train_edge_reference.png)

![equity](equity_alpaca_spot_train_edge_reference.png)

### `alpaca_spot` · train_edge_reference · with circuit breakers (5% daily stop, 15% DD close-all + halt) (1 strategies) — reference only, includes strategies 2025 rejected

Return % (max mark-to-market drawdown %):

| risk | 1x |
|---|---|
| 1.0% risk | -15.9% (16%) |
| 2.0% risk | -15.0% (15%) |
| 3.0% risk | -14.5% (15%) |
| 5.0% risk | -14.4% (15%) |
| 7.5% risk | -14.4% (15%) |
| 10.0% risk | -14.4% (15%) |
| 15.0% risk | -14.4% (15%) |
| 20.0% risk | -14.4% (15%) |


### `perp` · train_edge_reference · raw (15 strategies) — reference only, includes strategies 2025 rejected

Return % (max mark-to-market drawdown %):

| risk | 1x | 2x | 3x | 5x | 10x | 20x |
|---|---|---|---|---|---|---|
| 1.0% risk | -27.1% (44%) | -62.1% (71%) | -69.6% (78%) | -69.6% (79%) | -71.3% (81%) | -77.2% (84%) |
| 2.0% risk | -28.8% (54%) | -56.3% (71%) | -80.1% (87%) | -93.0% (96%) | -95.3% (98%) | -97.4% (98%) |
| 3.0% risk | -35.1% (59%) | -45.9% (71%) | -77.8% (88%) | -95.1% (98%) | -99.0% (99%) | -99.0% (99%) |
| 5.0% risk | -30.6% (62%) | -57.8% (80%) | -72.9% (90%) | -95.4% (98%) | -99.0% (99%) | -99.1% (99%) |
| 7.5% risk | -21.1% (63%) | -56.8% (82%) | -77.8% (93%) | -93.1% (98%) | -99.0% (99%) | -99.2% (99%) |
| 10.0% risk | -20.9% (63%) | -54.9% (86%) | -80.8% (94%) | -97.1% (99%) | -99.0% (99%) | -99.5% (99%) |
| 15.0% risk | -20.6% (63%) | -61.9% (87%) | -80.9% (96%) | -99.0% (99%) | -99.0% (100%) | -99.6% (100%) |
| 20.0% risk | -20.6% (63%) | -58.3% (86%) | -83.8% (96%) | -99.0% (99%) | -99.0% (100%) | -99.2% (100%) |


![heatmap](heatmap_perp_train_edge_reference.png)

![equity](equity_perp_train_edge_reference.png)

Ruined (equity < 1% of start): 3.0% @ 10x, 3.0% @ 20x, 5.0% @ 10x, 5.0% @ 20x, 7.5% @ 10x, 7.5% @ 20x, 10.0% @ 10x, 10.0% @ 20x, 15.0% @ 5x, 15.0% @ 10x, 15.0% @ 20x, 20.0% @ 5x, 20.0% @ 10x, 20.0% @ 20x

### `perp` · train_edge_reference · with circuit breakers (5% daily stop, 15% DD close-all + halt) (15 strategies) — reference only, includes strategies 2025 rejected

Return % (max mark-to-market drawdown %):

| risk | 1x | 2x | 3x | 5x | 10x | 20x |
|---|---|---|---|---|---|---|
| 1.0% risk | -10.0% (15%) | -11.6% (15%) | -11.4% (15%) | -11.4% (15%) | -11.4% (15%) | -12.2% (15%) |
| 2.0% risk | -7.2% (15%) | -4.7% (15%) | -4.7% (15%) | -7.6% (16%) | -8.6% (16%) | -8.9% (15%) |
| 3.0% risk | -11.7% (17%) | +1.1% (15%) | +0.4% (16%) | +0.4% (17%) | -16.3% (16%) | -16.1% (16%) |
| 5.0% risk | -8.6% (16%) | +0.2% (15%) | +10.6% (16%) | +4.2% (17%) | -14.3% (16%) | -12.3% (17%) |
| 7.5% risk | -8.0% (16%) | -13.9% (19%) | +7.1% (16%) | +19.8% (19%) | -15.5% (16%) | -28.0% (28%) |
| 10.0% risk | -8.0% (16%) | -14.2% (19%) | +0.2% (16%) | +23.5% (21%) | -15.3% (16%) | -16.4% (17%) |
| 15.0% risk | -8.0% (16%) | -14.2% (19%) | -8.1% (15%) | -15.1% (16%) | -16.3% (17%) | -41.0% (42%) |
| 20.0% risk | -8.0% (16%) | -14.2% (19%) | -8.1% (15%) | -14.5% (16%) | -14.8% (16%) | -14.8% (16%) |

