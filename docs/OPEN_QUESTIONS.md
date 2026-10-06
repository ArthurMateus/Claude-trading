# Open questions

Answer any of these by editing this file or telling Claude. The *default* is what the code does today.

## Still open

1. **Credit stretch:** the $20/month Pro credit buys very roughly 6–15 fully analyzed decisions a day, paced
   24/7. When it's paced out or used up, should the bot fall back to heuristic-only decisions, or pause entries
   (current)? *Default:* pause. Also: what day does your Claude subscription renew (`llm.credit_reset_day`)?
2. **Upgrade path:** if paper results justify it, move to Max ($100/month credit) or the paid API for more
   headroom? *Default:* no.
3. **Max open positions "5–8"**: with $500 and a 30% per-position cap, at most ~3 positions fit at once anyway.
   *Default:* cap 8, cash limits it naturally.
4. **Heat cap coupled to the daily loss limit** (effective cap 5% while the daily limit is 5%)? *Default:* yes.
5. **Leverage / shorts:** stay spot long-only on Alpaca? *Default:* yes.
6. **Fees:** try maker (passive limit) entries to cut fees, at the cost of missed fills? *Default:* IOC taker.
7. **Trading calendar:** 24/7, or pause around CPI/FOMC releases? *Default:* 24/7 (the News agent flags event risk).
8. **Paper gate:** 28 days / 100 trades / PF ≥ 1.2 / DD ≤ 10% before live? At a few trades per day, 100
   trades may take longer than 28 days. *Default:* both must be met.
9. **Event risk:** should `event_risk: high` block trades outright instead of sizing down? *Default:* size down.
10. **Daily loss limit:** should it also flatten open positions? *Default:* it only blocks new entries.
11. **Derivatives flow data** (funding, open interest, liquidations) for the Flow agent: worth a provider later?
12. **External traders to follow:** any specific accounts, wallets or strategies you want scored? *Default:*
    none. Replication follows the system's own agents and setups; external sources only count once earned.

## Decisions made

| Date | Topic | Decision |
|---|---|---|
| 2026-10-06 | Markets | Crypto first |
| 2026-10-06 | Broker | Alpaca, paper first |
| 2026-10-06 | Risk per trade | 1–5% by confidence, **earned** via calibration (starts at 1%) |
| 2026-10-06 | Loss limits | 5% daily → no new entries that day; 15% drawdown → HALT + flatten |
| 2026-10-06 | Positions | max 5–8 (cap 8) |
| 2026-10-06 | Portfolio heat | 6% total open risk; correlated assets = one cluster |
| 2026-10-06 | LLM architecture | Option 3: Opus 5.5 orchestrator, Haiku/Sonnet sub-agents; hard limits in code |
| 2026-10-06 | Stack | Python + SQLite |
| 2026-10-06 | Account size | **$500** for paper (`allocated_capital_usd: 500`); per-cycle LLM reviews off |
| 2026-10-06 | Setups | No personal setups; use a diverse library (8 setups); each must pass the backtest gate |
| 2026-10-06 | News | Free: Alpaca/Benzinga, RSS (CoinDesk, Cointelegraph, Decrypt), Reddit. Opt-in X (capped 100 posts/day) and CryptoPanic |
| 2026-10-06 | Replication | Follow the system's own hot agents + setup momentum; external traders only after they earn trust |
| 2026-10-06 | Fundamental | Stays disabled for now |
| 2026-10-06 | Hosting | User's local machine (scripts + systemd/launchd/Task Scheduler templates) |
| 2026-10-06 | AI cost | **Free beyond the Pro subscription:** `claude -p` backend on the user's Claude login, $19.50/month paced 24/7 (carry-over), free pre-screen + 30-min re-analysis cooldown, no API key, X/CryptoPanic off |
| 2026-10-06 | Alerts | Discord webhook (`DISCORD_WEBHOOK_URL`) |
