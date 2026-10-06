# Open questions

Decisions made so far are recorded at the bottom. Answer any question by editing this file (or telling
Claude); the current default is what the code does today.

## A. General (most important first)

1. **Account size** for paper and later live? With LLM costs of ~$5–12/day, a $2k account can't cover them;
   $10k needs ~1.5–3.5%/month just to break even on the LLM bill. *Default:* $10k simulated, Alpaca paper default.
2. **"Max 5–8 open positions"**: is the cap 8, or 5 normally and 8 only once sizing is calibrated? *Default:* 8.
3. **Heat cap vs daily loss**: total open risk is capped at 6% **and** at today's remaining loss budget
   (≤ 5%), so all stops hitting at once can't breach the daily limit. Keep that coupling? *Default:* yes
   (`heat_respects_daily_limit: true`).
4. **Leverage**: spot-only plus a 30% notional cap means real risk per trade is often 0.3–1%, not 1–5%. Stay
   spot-only, raise the notional cap, or add a perps venue later (enables shorts too)? *Default:* spot only.
5. **Fees/venue**: Alpaca taker ~0.25%. Should entries be passive maker limits (cheaper, may not fill),
   or should we evaluate a lower-fee venue? *Default:* IOC marketable limit on Alpaca.
6. **Profit target**: what monthly return and maximum drawdown would you call success? This sets the promotion gates.
7. **Trading calendar**: trade 24/7, or skip weekends / low-liquidity hours / CPI-FOMC windows? *Default:* 24/7.
8. **Alerts**: where should HALTs, daily summaries and promotion verdicts go (Telegram, Slack, email, push)?
   *Default:* journal + logs only.
9. **Hosting**: where will `tradebot run` live 24/7 (VPS, home server, cloud VM)? Cloud Claude sessions are ephemeral.
10. **Paper gate**: are 28 days / 100 trades / PF 1.2 / DD 10% the right bar to go live?

## B. Per agent

**📡 Market Data**: Which coins (current: BTC, ETH, SOL, LTC, LINK, AVAX, DOGE)? Add a minimum 24h-volume
filter? Is the 5-minute bar right, or do you want 1m / 15m?

**📰 News**: Is Alpaca/Benzinga enough, or add CryptoPanic, X/Twitter, LunarCrush or CoinMarketCap news (these
need their own API keys for the bot)? Should `event_risk: high` **block** trades or only size them down
(current)? Should scheduled macro events (CPI, FOMC) be a blackout window?

**📊 Technical**: Which setups do *you* trust or trade by hand? The three starters are generic and fail on
random data after fees. Your own rules are the best candidates for the backtest gate.

**📈 Quant**: Keep empirical per-setup statistics, or add an ML model (e.g. gradient boosting on features,
walk-forward trained)? How much history (now ~14 days for live stats, 60 days for validation)?

**🏦 Fundamental**: Which source (Token Unlocks, Messari, CoinMarketCap, Glassnode)? Veto-only (e.g. block
longs on unlock day) or a real score? Or keep it disabled?

**🐋 Flow**: Add derivatives data (funding rates, open interest, liquidations, Deribit options skew)? For
intraday crypto these are often the strongest flow signals. Which provider?

**👀 Replication**: Whom exactly? On-chain whale wallets (addresses?), public traders (which accounts?),
exchange copy-trading leaders, or published strategies (which?). Mirror entries only, or exits too? Check
that the source's terms allow automated use.

**🔬 Backtest**: Are the thresholds right (40 trades, PF 1.2, 5 bps expectancy, 15% DD, OOS half)? Want
rolling walk-forward re-optimization, or fixed rules only (current, safer)?

**🧪 Paper Trading**: Paper on Alpaca's paper account (current), and keep the simulated broker only for tests?

**⚠️ Risk**: Should `event_risk: high` reject outright? Any per-asset caps (e.g. DOGE max 10% of equity)? Should
risk scale down automatically after a drawdown (e.g. half size below −5%)?

**🎯 Portfolio**: Keep a minimum cash buffer? Cap total exposure below 100% of equity?

**⚡ Execution**: Market-like IOC (current) vs passive maker? Is a 20 bps max slippage acceptable?

**🛡️ Kill-Switch**: Should the daily loss limit also **flatten** open positions (currently it only blocks new
entries)? Who gets notified and how?

**📋 Post-Trade**: Should lessons stay advisory (current: they go into prompts and bounded weight updates)?
Want an auto-generated weekly report as a PR?

**🧠 Orchestrator**: Opus effort `high` (current) or `xhigh` (slower, costlier, maybe better)? Should it see
all recent lessons or only those for the same setup?

---

## Decisions so far (2026-10-06)

| Topic | Decision |
|---|---|
| Markets | Crypto first |
| Broker | Alpaca (paper first) |
| Risk per trade | 1–5% by confidence; **earned** via calibration (starts at 1%) |
| Daily loss / drawdown | 5% → stop entries for the day; 15% → HALT + flatten |
| Open positions | max 5–8 (cap 8 for now) |
| Portfolio heat | 6% total open risk, correlated assets count as one cluster |
| LLM architecture | Option 3: Opus 5.5 orchestrator, cheaper (Haiku/Sonnet) sub-agents for everything else; hard limits stay in code |
| Stack | Python + SQLite |
