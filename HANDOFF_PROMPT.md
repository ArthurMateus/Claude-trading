# Handoff prompt

Copy everything inside the block below into a new Claude Code session (any account) that has access to
`ArthurMateus/Claude-trading`. It is self-contained; the repo's `CLAUDE.md` and `.claude/` folder carry the rest.

```text
You are continuing work on "tradebot", my multi-agent Claude crypto swing-trading bot.

Repo: https://github.com/ArthurMateus/Claude-trading
Branch with the v0.1 build: claude/trusting-noether-rb2slp (merge it to main first or keep working on it).

Start by reading, in this order: CLAUDE.md, docs/ASSESSMENT.md, docs/ARCHITECTURE.md, docs/AGENTS.md,
docs/SDLC.md, docs/OPEN_QUESTIONS.md, docs/LOCAL_SETUP.md. Then run:
  bash scripts/setup_local.sh                               (all tests must pass, offline)
  tradebot --mode simulated --offline cycle                 (offline smoke run)

WHAT IT IS
- 15 agents: Orchestrator (Opus 5.5), Market Data, News, Technical, Quant, Fundamental, Flow,
  Strategy-Replication, Backtest, Paper-Trading, Risk, Portfolio, Execution, Kill-Switch, Post-Trade.
- Pipeline every 5 min: Market Data -> manage open positions -> Kill-Switch -> intelligence layer (only for assets
  where a backtest-validated setup fires) -> Orchestrator fusion -> Risk (LLM + hard guardrails in code) ->
  Portfolio -> guardrail re-check -> Execution (IOC limit + resting broker stop) -> SQLite journal ->
  Post-Trade attribution/lessons -> bounded learning loop.
- Python 3.11, SQLite journal (data/journal.sqlite), Alpaca crypto (paper), Anthropic SDK with structured
  outputs, refusal fallback ("fallbacks": "default") on Opus/Sonnet 5.5, prompt caching, daily LLM budget.

DECISIONS I ALREADY MADE (don't re-ask)
- Crypto first; Alpaca; paper first, live only after the paper gate + my explicit env-var acknowledgement.
- Risk per trade 1-5% depending on confidence, but EARNED: 1% until a confidence bucket proves calibration
  over 30+ trades. Daily loss limit 5% (stop entries for the day). Max drawdown 15% -> HALT + flatten.
- Max 5-8 open positions (cap 8). Portfolio heat cap 6% of equity, also capped by today's remaining
  loss budget; correlated assets count as one cluster (5% cap).
- LLM "Option 3": Opus 5.5 orchestrator; cheaper models (Haiku 4.5 / Sonnet 5.5) for every sub-agent. Hard
  limits stay in code; LLMs can only make safety decisions stricter.
- Holding period: minutes to max 4 hours. Spot, long-only (Alpaca crypto can't short).
- Account: $500 for paper (allocated_capital_usd: 500). Per-cycle LLM reviews off.
- NO PAID API: the AI agents run through my Claude Pro subscription via `claude -p` (llm.backend: claude_cli).
  Pro includes a $20/month credit for that; the bot caps itself at $18/month and $0.60/day and strips
  ANTHROPIC_API_KEY from the CLI env. I keep "usage credits" off in claude.ai, so running out just pauses calls.
  Do not switch me to the paid API or enable X/CryptoPanic without asking.
- Alerts go to Discord via DISCORD_WEBHOOK_URL (notify.py).
- No personal setups: use the diverse 8-setup library; every setup must pass the backtest gate on real data.
- News: free sources (Alpaca/Benzinga, CoinDesk/Cointelegraph/Decrypt RSS, Reddit) + X/Twitter opt-in via
  X_BEARER_TOKEN (capped 100 posts/day, charged to the daily budget) + CryptoPanic opt-in.
- Replication agent follows the system's own best-performing agents and setups; external traders/strategies
  only count after their past calls are scored and clear the trust thresholds.
- Fundamental agent stays disabled. Hosting: my local machine.

AFTER EVERY TRADE THE JOURNAL STORES
Timestamp, Asset, Entry, Exit, Position size, Agents that agreed, Agents that disagreed, Confidence, Reason for
trade, Expected return, Actual return, Market conditions, Slippage, Result (plus stops, fees, R-multiple, exit
reason, attribution, lessons). Export with: tradebot export data/trades.csv

RULES FOR YOU
- Follow CLAUDE.md invariants. Never enable live trading, never raise risk limits, never read .env.
- Every behavior change needs tests; keep pytest green. Use the .claude sub-agents: risk-reviewer for anything
  touching guardrails/risk/kill-switch/execution/brokers/limits; quant-validator for setups/backtests;
  agent-builder to add or modify agents. Skills: backtest-gate, weekly-review, add-setup.
- Ask me before deciding anything listed in docs/OPEN_QUESTIONS.md.

CURRENT STATUS
- v0.3 complete and tested offline (45 tests); the claude -p backend was verified with real calls. NOT yet
  verified against real services: the Alpaca adapter, live news feeds and the Discord webhook (the build sandbox
  blocked those hosts). Not built: derivatives flow data.
- On random-walk data every setup fails the backtest gate once fees are included (as it should). Real
  profitability depends on setups that pass on real Alpaca history.

NEXT STEPS
1. Help me through docs/LOCAL_SETUP.md on my machine (venv, claude login, .env, Alpaca paper reset to $500).
2. Run `tradebot llm-check` and `tradebot notify-test`, then `tradebot validate` on real history and report
   with the backtest-gate skill.
3. Do one supervised `tradebot cycle`; fix anything the real Alpaca/news APIs reveal; then scripts/run_local.sh.
4. Ask me the remaining items in docs/OPEN_QUESTIONS.md.
5. Weekly: run the weekly-review skill and propose changes as PRs.
```
