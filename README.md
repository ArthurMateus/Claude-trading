# Tradebot: a multi-agent Claude crypto swing-trading bot

Fifteen cooperating agents (an Opus 5.5 orchestrator plus Haiku/Sonnet specialists) trade crypto swings
lasting minutes to 4 hours on Alpaca, **paper first**. Hard risk limits are enforced in code. Every trade is
journaled with its full reasoning, and every result feeds back into the system.

> ⚠️ Experimental software. Nothing here is financial advice and no profitability is promised. Run it in paper
> mode until the built-in promotion gate passes, then risk only money you can afford to lose.

## Quick start
```bash
pip install -e ".[dev]"
python3 -m pytest -q                                   # 24 offline tests
tradebot --mode simulated --offline validate           # backtest gate on synthetic data
tradebot --mode simulated --offline cycle              # one dry-run cycle, no API calls

cp .env.example .env    # add ANTHROPIC_API_KEY, ALPACA_API_KEY, ALPACA_SECRET_KEY (paper keys)
tradebot validate       # backtest gate on real Alpaca history
tradebot run            # paper trading loop, one cycle every 5 minutes
tradebot report         # P&L, validated setups, promotion verdict, LLM spend
tradebot export data/trades.csv
```

## Docs
- [docs/ASSESSMENT.md](docs/ASSESSMENT.md): does this design make sense, and can it be profitable?
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): pipeline, principles, modules, models
- [docs/AGENTS.md](docs/AGENTS.md): each agent's spec and the trade-journal fields
- [docs/SDLC.md](docs/SDLC.md): agentic development and strategy-promotion lifecycle
- [docs/OPEN_QUESTIONS.md](docs/OPEN_QUESTIONS.md): decisions still needed from you
- [CLAUDE.md](CLAUDE.md) and [.claude/](.claude): context and sub-agents for Claude Code
- [HANDOFF_PROMPT.md](HANDOFF_PROMPT.md): paste into a new Claude session or account to continue
