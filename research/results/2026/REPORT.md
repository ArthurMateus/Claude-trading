# 2026 out-of-sample test

## Headline (pre-registered before testing)

`alpaca_spot` · validated strategies · 1% risk per trade · 1x · circuit breakers on:

**No result: no strategy passed validation for this venue, so the headline portfolio is empty (the bot would not have traded).**

Everything after this section is sensitivity analysis. Picking the best cell after the fact is selection on the test set.

## Integrity

- ✅ First run on 2026 for this freeze; no 2026 data was cached before the freeze; not contaminated (checked from the ledger and file timestamps).
- Test window **2026-01-01 → 2026-10-06** (278 days); assets AVAX/USD, BTC/USD, DOGE/USD, ETH/USD, LINK/USD, LTC/USD, SOL/USD; start equity $500.
- Frozen 2026-10-06T13:24:31.561952+00:00 (sha256 `54a2e513f881ea91…`, code `1c288a7ed1ae…`) after searching **1152 configurations** on 2022–2024 and selecting on 2025.
- Costs: `alpaca_spot` = 0.25% fee + slippage 0.05% (BTC/ETH) or 0.15% (others) per side, long-only, 1x. `perp` = 0.05% fee + 0.03% slippage per side + 0.01%/8h funding, long+short, ≤ 20x, liquidation modeled.
- Drawdowns are mark-to-market on 5-minute closes; one position per asset; max 8 positions.
- "Circuit breakers" = no new entries after a 5% daily loss, and close everything + stop at a 15% drawdown. The bot's other limits (calibrated sizing, 6% heat cap, 30% notional cap) are NOT applied here, which is why 10–20% risk is even possible.

## Strategy champions (one per family and side, per venue)

**5 champion(s) beat the random-entry null; about 1.8 would by pure chance** (5% of those with test trades).

`beats_null` = test expectancy above the 95th percentile of 200 random-entry runs with the same exits. `noise` = fewer than 40 test trades. Jan–Jun vs Jul–now: the families were designed by a model with training data to mid-2026, so Jul–now is the cleaner window.

### `alpaca_spot`

| family | side | tf | validated | train_pf | val_pf | val_t | test_trades | test_pf | test_exp_bps | test_t | exp_bps_jan_jun | exp_bps_jul_now | null_exp_bps | beats_null | noise | ret@1%,1x | ret@5%,1x | ret@10%,1x | ret@20%,1x |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bb_reversion | long | 60 | no | 0.24 | 0.31 | -5.75 | 524 | 0.26 | -68.20 | -5.90 | -70.78 | -63.05 | -73.81 | no | no | -73.1% | -79.8% | -79.8% | -79.8% |
| climax_reversal | long | 60 | no | 0.37 | 0.75 | -0.35 | 32 | 3.34 | 56.59 | 1.43 | 69.93 | -1.19 | -76.26 | yes | yes | +4.5% | +4.9% | +4.7% | +4.7% |
| donchian_breakout | long | 60 | no | 0.41 | 0.43 | -6.34 | 624 | 0.31 | -71.88 | -8.46 | -83.13 | -54.48 | -75.17 | no | no | -77.2% | -78.8% | -78.8% | -78.8% |
| ema_cross | long | 60 | no | 0.32 | 0.30 | -7.58 | 331 | 0.19 | -76.09 | -8.00 | -74.19 | -79.27 | -75.37 | no | no | -71.9% | -81.0% | -81.0% | -81.0% |
| momentum_burst | long | 60 | no | 0.57 | 0.73 | -1.46 | 333 | 0.34 | -73.49 | -4.73 | -88.09 | -46.18 | -75.83 | no | no | -55.5% | -68.2% | -68.2% | -68.2% |
| mtf_pullback | long | 60 | no | 0.27 | 0.38 | -1.10 | 10 | 0.08 | -52.74 | -1.51 | -63.16 | -28.40 | -73.58 | no | yes | -2.1% | -2.4% | -2.4% | -2.4% |
| rsi2_reversion | long | 60 | no | 0.37 | 0.23 | -7.71 | 479 | 0.20 | -66.58 | -7.87 | -72.63 | -58.42 | -74.64 | no | no | -71.2% | -79.1% | -79.0% | -79.0% |
| session_breakout | long | 60 | no | 0.39 | 0.47 | -4.00 | 603 | 0.16 | -84.24 | -9.12 | -89.29 | -74.86 | -75.77 | no | no | -67.3% | -70.5% | -70.5% | -70.5% |
| squeeze_breakout | long | 60 | no | 0.42 | 0.46 | -3.14 | 232 | 0.40 | -52.43 | -3.92 | -70.15 | -21.78 | -74.74 | yes | no | -43.6% | -37.1% | -37.1% | -37.1% |
| taker_flow | long | 60 | no | 0.25 | 0.23 | -9.17 | 365 | 0.16 | -79.11 | -9.85 | -80.42 | -77.20 | -75.59 | no | no | -85.0% | -88.9% | -88.9% | -88.9% |
| vol_breakout | long | 60 | no | 0.39 | 0.39 | -6.14 | 483 | 0.27 | -71.63 | -8.13 | -74.74 | -66.71 | -75.13 | no | no | -70.6% | -77.4% | -77.4% | -77.4% |
| vwap_reclaim | long | 60 | no | 0.39 | 0.64 | -2.00 | 391 | 0.24 | -78.92 | -6.11 | -88.96 | -58.76 | -74.86 | no | no | -66.1% | -72.3% | -72.3% | -72.3% |


### `perp`

| family | side | tf | validated | train_pf | val_pf | val_t | test_trades | test_pf | test_exp_bps | test_t | exp_bps_jan_jun | exp_bps_jul_now | null_exp_bps | beats_null | noise | ret@1%,3x | ret@5%,3x | ret@10%,3x | ret@20%,3x |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bb_reversion | long | 60 | no | 0.68 | 0.71 | -1.80 | 524 | 0.80 | -10.98 | -1.09 | -13.98 | -5.00 | -16.62 | no | no | -38.0% | -66.5% | -70.2% | -70.7% |
| bb_reversion | short | 60 | no | 0.61 | 0.47 | -3.35 | 428 | 0.56 | -23.19 | -3.25 | -22.33 | -24.54 | -16.03 | no | no | -53.2% | -59.3% | -63.7% | -65.5% |
| climax_reversal | long | 60 | no | 0.85 | 0.69 | -0.52 | 60 | 2.03 | 40.30 | 1.14 | 69.15 | -32.68 | -16.74 | yes | no | +6.5% | +14.4% | +15.4% | +15.0% |
| climax_reversal | short | 15 | no | 1.09 | 1.25 | 0.78 | 113 | 0.71 | -15.13 | -1.38 | -16.60 | -12.63 | -15.00 | no | no | -24.1% | -26.8% | -27.3% | -27.3% |
| donchian_breakout | long | 60 | no | 0.84 | 0.95 | -0.33 | 624 | 0.78 | -14.11 | -1.64 | -25.69 | 3.79 | -17.43 | no | no | -41.3% | -47.6% | -43.8% | -42.8% |
| donchian_breakout | short | 60 | no | 0.72 | 0.94 | -0.33 | 569 | 0.68 | -22.86 | -1.58 | -15.58 | -40.53 | -16.53 | no | no | -52.8% | -68.0% | -72.4% | -73.1% |
| ema_cross | long | 60 | no | 0.88 | 0.76 | -1.66 | 331 | 0.66 | -17.44 | -1.88 | -16.30 | -19.35 | -17.87 | no | no | -38.9% | -79.3% | -78.5% | -78.5% |
| ema_cross | short | 60 | no | 0.73 | 0.97 | -0.22 | 482 | 0.65 | -16.39 | -2.59 | -13.09 | -22.39 | -15.13 | no | no | -40.5% | -74.2% | -75.5% | -75.5% |
| momentum_burst | long | 60 | no | 1.02 | 1.27 | 1.13 | 337 | 0.80 | -14.15 | -1.11 | -18.69 | -5.72 | -18.80 | no | no | -34.5% | -68.5% | -70.7% | -70.7% |
| momentum_burst | short | 60 | no | 0.81 | 0.83 | -0.60 | 144 | 0.90 | -8.90 | -0.19 | -6.58 | -21.73 | -15.47 | no | no | -10.8% | -10.1% | -1.7% | +0.5% |
| mtf_pullback | long | 60 | no | 0.52 | 0.71 | -0.36 | 10 | 1.17 | 4.81 | 0.13 | -8.16 | 35.10 | -16.04 | no | yes | +0.1% | +1.0% | +0.9% | +0.9% |
| mtf_pullback | short | 60 | no | 0.88 | 1.08 | 0.12 | 14 | 0.17 | -42.75 | -1.68 | -36.51 | -80.19 | -16.24 | no | yes | -7.4% | -11.3% | -11.3% | -11.3% |
| rsi2_reversion | long | 60 | no | 0.96 | 0.71 | -2.13 | 480 | 0.80 | -8.85 | -1.29 | -13.02 | -3.26 | -17.05 | no | no | -35.1% | -56.2% | -58.4% | -58.1% |
| rsi2_reversion | short | 60 | no | 0.86 | 0.75 | -1.98 | 515 | 0.54 | -23.60 | -2.72 | -21.97 | -29.34 | -15.43 | no | no | -50.9% | -75.8% | -77.9% | -78.0% |
| session_breakout | long | 60 | no | 0.88 | 1.06 | 0.27 | 609 | 0.53 | -27.50 | -3.30 | -32.60 | -17.96 | -17.44 | no | no | -59.0% | -75.3% | -74.7% | -74.8% |
| session_breakout | short | 15 | no | 0.98 | 1.06 | 0.36 | 685 | 0.59 | -27.35 | -2.83 | -21.52 | -39.27 | -14.63 | no | no | -66.9% | -76.5% | -74.4% | -74.5% |
| squeeze_breakout | long | 60 | no | 0.90 | 1.02 | 0.09 | 232 | 1.11 | 5.53 | 0.42 | -12.03 | 35.90 | -16.80 | yes | no | +1.9% | +53.3% | +95.7% | +103.2% |
| squeeze_breakout | short | 60 | no | 1.04 | 0.87 | -0.53 | 213 | 0.96 | -2.31 | -0.15 | 6.61 | -23.53 | -15.76 | yes | no | -7.5% | -7.3% | -1.5% | -1.5% |
| taker_flow | long | 60 | no | 0.82 | 0.75 | -1.81 | 365 | 0.58 | -20.83 | -2.91 | -22.68 | -18.12 | -17.30 | no | no | -51.7% | -84.9% | -86.4% | -86.1% |
| taker_flow | short | 60 | no | 0.75 | 0.75 | -3.71 | 2096 | 0.72 | -14.51 | -3.63 | -11.73 | -19.92 | -15.98 | no | no | -88.2% | -99.0% | -99.0% | -99.0% |
| vol_breakout | long | 60 | no | 0.84 | 0.95 | -0.34 | 483 | 0.76 | -13.88 | -1.75 | -17.35 | -8.38 | -17.40 | no | no | -36.7% | -60.2% | -59.7% | -55.1% |
| vol_breakout | short | 60 | no | 0.79 | 0.86 | -0.88 | 490 | 0.63 | -23.42 | -1.90 | -15.95 | -38.56 | -15.98 | no | no | -49.8% | -66.0% | -68.3% | -68.4% |
| vwap_reclaim | long | 60 | no | 0.88 | 0.97 | -0.21 | 627 | 0.63 | -21.76 | -3.11 | -22.95 | -19.29 | -18.39 | no | no | -64.0% | -86.8% | -87.6% | -87.6% |
| vwap_reclaim | short | 60 | no | 0.84 | 0.91 | -0.53 | 407 | 0.64 | -19.56 | -2.11 | -13.96 | -29.37 | -15.46 | no | no | -47.0% | -70.6% | -69.6% | -68.9% |


## Combined portfolios

_None: no champion passed the in-sample gate (2022–2024), so neither the validated nor the reference portfolio has members. Per-strategy returns at each risk level are in the tables above._
