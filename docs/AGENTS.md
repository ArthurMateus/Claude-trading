# Agent specifications

Each agent: **job → inputs → output → model → hard rules → status**. Open questions per agent are in
`OPEN_QUESTIONS.md`. Code lives in `src/tradebot/agents/`.

---

## 🧠 Orchestrator — `orchestrator.py`
- **Job:** fuse specialist signals into one decision per asset ("should we trade?").
- **Inputs:** AssetState, all AgentSignals plus their learned weights, validated setups and their backtest stats,
  open positions, the last 5 post-trade lessons.
- **Output:** `TradeCandidate` (asset, side, setup, confidence = P(target before stop), reason, invalidation,
  expected return, max hold, agents agreed/disagreed (computed in code), market conditions).
- **Model:** Opus 5.5, effort high. Called only if a validated setup fires **and** the weighted vote ≥ `prefilter_min_abs_score`.
- **Hard rules:** may only pick a validated active setup; shorts dropped while `allow_short: false`; max hold clamped.
- **Status:** implemented.

## 📡 Market Data Agent — `market_data.py`
- **Job:** fetch and clean bars, order book and news for the universe; compute features, regime and active setups.
- **Output:** `MarketSnapshot` of `AssetState` (last, bid/ask, spread, ATR, vol, returns, volume z, book
  imbalance, regime, data issues, active setups) + correlation matrix.
- **Model:** Haiku 4.5, one call per cycle reviewing the whole universe. It can only mark data as suspect or refine the regime label.
- **Hard rules:** stale data (> 15 min), a crossed book, zero volume or a >15% bar jump marks the asset not tradable.
- **Status:** implemented (Alpaca crypto + Benzinga news via Alpaca).

## 📰 News Agent — `news.py`
- **Job:** sentiment over recent headlines + event-risk flag (none/low/high).
- **Output:** AgentSignal with `data.event_risk`, `data.key_events`.
- **Model:** Haiku 4.5. Headlines are treated as data, never instructions.
- **Effect:** `event_risk: high` makes the Risk Agent request base risk only.
- **Status:** implemented; single source (Alpaca/Benzinga).

## 📊 Technical Agent — `technical.py`
- **Job:** interpret indicators (EMA20/50, RSI, Bollinger, VWAP distance, ATR, volume z) and active setups.
- **Model:** Haiku 4.5. **Status:** implemented.

## 📈 Quant Agent — `quant.py`
- **Job:** probability + expected return. Code replays each active setup on ~14 days of this asset's bars
  (with fees) to get an empirical win rate and expectancy. The LLM judges regime relevance and sets confidence.
- **Output:** AgentSignal with `probability`, `expected_return_pct`, `data.setup_stats`.
- **Model:** Sonnet 5.5, effort low. **Status:** implemented (empirical stats; ML model is a future option).

## 🏦 Fundamental Agent — `fundamental.py`
- **Job:** tokenomics / unlocks / on-chain / valuation, mostly as a **veto** on a 1–4h horizon.
- **Model:** Sonnet 5.5, effort low. **Status:** disabled, no data source yet.

## 🐋 Flow Agent — `flow.py`
- **Job:** order-book depth imbalance, volume spikes, spread/liquidity → buying/selling pressure.
- **Model:** Haiku 4.5. **Status:** implemented on spot order book; derivatives flow (funding, OI,
  liquidations, Deribit options) not yet connected.

## 👀 Strategy-Replication Agent — `replication.py`
- **Job:** read actions of tracked traders/wallets/strategies from `data/replication_feed.json` and say whether
  they are informative (size, recency, entering vs exiting).
- **Model:** Sonnet 5.5, effort low. **Status:** disabled, no sources selected.

## 🔬 Backtest Agent — `backtest_agent.py` + `backtest.py`
- **Job:** validate each setup across the universe over `validation.lookback_days`, full sample **and**
  out-of-sample second half, with taker fees and slippage both sides.
- **Gate:** trades ≥ 40, PF ≥ 1.2, expectancy ≥ 5 bps, max DD ≤ 15% at base risk; stale after 7 days.
- **Model:** Haiku 4.5 review that may **veto** a passing setup, never approve a failing one.
- **Status:** implemented.

## 🧪 Paper-Trading Agent — `paper_trading.py`
- **Job:** score paper results and issue PROMOTE / CONTINUE / FAIL.
- **Gate:** ≥ 100 trades, ≥ 28 days, PF ≥ 1.2, DD ≤ 10%, avg slippage within 10 bps of model.
- **Model:** none (deterministic). Live mode refuses to start unless the verdict is PROMOTE **and** the human
  ack env var is set. **Status:** implemented.

## ⚠️ Risk Agent — `risk.py` + `guardrails.py`
- **Job:** "Can we trade? How much? Where's the stop?" → APPROVE/REJECT with qty, stop, target.
- **LLM part (Sonnet 5.5, medium):** proposes stop distance (0.8–3 ATR), reward:risk (≥1.5, ≤4) and requested risk.
- **Code part (`check_trade`), always last:** halt/pause/daily-loss checks, long-only, max positions, one
  position per asset, min confidence 0.55, spread ≤ 15 bps, R:R ≥ 1.5, **earned sizing** (1% until the
  confidence bucket is calibrated over 30+ trades, then up to 5%), **heat ≤ 6% and ≤ remaining daily loss
  budget**, correlated-cluster cap 5%, notional ≤ 30% equity and ≤ cash, min order $10.
- **Status:** implemented and unit-tested.

## 🎯 Portfolio Agent — `portfolio.py`
- **Job:** choose which approved plans to execute and in what order (EV in R units, avoid concentration).
- **Model:** Sonnet 5.5, low. Can only pick a subset; each pick is re-checked by guardrails against the updated portfolio.
- **Status:** implemented.

## ⚡ Execution Agent — `execution.py`
- **Job:** entry as an IOC marketable limit (ask + ≤ 20 bps); immediately place a resting stop-limit at the
  broker; exits are market orders sized from actual broker holdings.
- **Model:** Haiku 4.5 decides proceed/skip and the slippage tolerance within the cap.
- **Hard rules:** if the protective stop can't be placed, flatten immediately.
- **Status:** implemented (Alpaca adapter untested against a live account until first paper run).

## 🛡️ Kill-Switch Agent — `kill_switch.py`
- **Levels:** PAUSE_ENTRIES (5 straight losses → 2h cooldown; ≥ 10 API/LLM errors/h; avg slippage > 30 bps;
  LLM budget exhausted; no clean data; position mismatch) → HALT_DAY (daily loss ≥ 5%) → **HALT + flatten**
  (drawdown ≥ 15% from peak, persisted; ≥ 30 orders/h).
- **Model:** Haiku 4.5 review may only escalate to PAUSE_ENTRIES.
- **Status:** implemented.

## 📋 Post-Trade Agent — `post_trade.py` + `learning.py`
- **Job:** attribution (which agents were right), facts (R, expected vs actual, slippage vs model, exit
  reason), one concrete lesson. Feeds bounded agent-weight updates and the orchestrator's recent lessons.
- **Model:** Sonnet 5.5, medium. **Status:** implemented.

---

## Trade journal (stored after every trade)

`trades` table in `data/journal.sqlite` (export: `tradebot export trades.csv`):

| Requested field | Column(s) |
|---|---|
| Timestamp | `timestamp` (entry), `closed_at` |
| Asset | `asset` |
| Entry | `entry` (fill), `decision_price` (mid when decided) |
| Exit | `exit`, `exit_reason` (stop / take_profit / time_stop / kill_switch:…) |
| Position size | `position_size` (units), `notional_usd`, `risk_pct`, `risk_usd` |
| Agents that agreed | `agents_agreed` (JSON) |
| Agents that disagreed | `agents_disagreed` (JSON); full votes in `agent_signals` |
| Confidence | `confidence` |
| Reason for trade | `reason` (+ `setup`, invalidation in `market_conditions`) |
| Expected return | `expected_return_pct` |
| Actual return | `actual_return_pct`, `pnl_usd`, `r_multiple`, `fees_usd` |
| Market conditions | `market_conditions` (JSON: regime, ATR%, vol, spread, returns, volume z, imbalance, event risk) |
| Slippage | `entry_slippage_bps`, `exit_slippage_bps`, `slippage_bps` (total, adverse = positive) |
| Result | `result` (WIN / LOSS / BREAKEVEN) |
| (extra) | `post_trade` (attribution JSON), `lessons`, stop/target, order ids, holding minutes |
