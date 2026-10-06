# Running tradebot on your own machine

## 1. Prerequisites
- Python 3.11+ and git
- An **Alpaca** account → Paper Trading → generate API key + secret
- **Claude Code** logged in with your **Claude Pro** subscription. The bot's AI agents run through `claude -p`, so
  no Anthropic API key and no per-token bill:
  1. Install with the native installer (https://code.claude.com/docs; on Windows prefer the native `claude.exe`
     over the npm version).
  2. Run `claude` once and log in with your Claude account.
  3. In claude.ai → **Settings → Usage**, keep **usage credits OFF**. Pro includes a $20/month credit for
     `claude -p`; with usage credits off, calls just stop when it's used up, and you can never be billed extra.
     The bot also stops itself at $18/month and $0.60/day (`llm:` in `config/settings.yaml`).
  4. Don't put an `ANTHROPIC_API_KEY` in your shell profile. The bot strips it from the CLI's environment
     anyway, so it can't switch to API billing.
- Optional: a **Discord webhook** for alerts (section 5)
- Optional, paid: `X_BEARER_TOKEN` (X API pay-per-use, ~$0.005 per post read, capped at 100 posts/day ≈ $0.50/day),
  `CRYPTOPANIC_TOKEN`. Leave them empty to stay free.

## 2. Install
```bash
git clone https://github.com/ArthurMateus/Claude-trading && cd Claude-trading
bash scripts/setup_local.sh                 # macOS / Linux
# Windows: powershell -ExecutionPolicy Bypass -File scripts\setup_local.ps1
```
This creates `.venv`, installs the bot, runs the offline tests and a dry-run cycle, and creates `.env`.
Fill in `.env` (never commit it).

## 3. Match the paper account to your $500
`config/settings.yaml` has `allocated_capital_usd: 500`: the bot sizes, tracks P&L and drawdown on $500 even if
the Alpaca paper account holds $100k. For the cleanest numbers, also reset the paper account to $500 in the
Alpaca dashboard (Paper account → Reset → set the starting balance).

## 4. First run, supervised
```bash
source .venv/bin/activate                 # Windows: .venv\Scripts\activate
tradebot llm-check                         # one tiny AI call through your subscription (~$0.005 of credit)
tradebot validate                          # backtest gate on 60 days of real Alpaca data
tradebot cycle                             # one full cycle; read the JSON report
tradebot report                            # P&L, validated setups, LLM spend, halt state
```
If no setup passes the gate, the bot will not trade. That is the gate working; see `docs/OPEN_QUESTIONS.md`.

## 5. Discord alerts (optional, 2 minutes)
1. In your Discord server: **Server Settings → Integrations → Webhooks → New Webhook**, pick a channel, **Copy Webhook URL**.
2. Put it in `.env` as `DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...`
3. `tradebot notify-test` should post a test message.

You'll get: bot started, every trade opened/closed (P&L, R, reason, agents), kill-switch changes (pause / halt /
cleared), the paper-gate verdict changing, AI credit budget reached, repeated errors, and a daily summary
(trades, win rate, P&L, equity, AI credit used). Treat the webhook URL like a password: anyone with it can post.

## 6. Run continuously
```bash
bash scripts/run_local.sh                  # Windows: scripts\run_local.ps1
```
Logs go to `data/tradebot.log`. To survive reboots and crashes, install it as a service:
- **Linux:** `deploy/tradebot.service` (systemd user unit; instructions inside)
- **macOS:** `deploy/com.tradebot.plist` (launchd; instructions inside)
- **Windows:** Task Scheduler → Create Task → Trigger "At log on" (or "At startup") → Action: start
  `powershell.exe` with arguments `-ExecutionPolicy Bypass -File C:\path\to\Claude-trading\scripts\run_local.ps1`
  → Settings: "If the task fails, restart every 1 minute", untick "Stop the task if it runs longer than".

## 7. What happens when your computer sleeps, reboots or loses internet
- Every open position has a **protective stop order resting at Alpaca**, so it is protected even while the bot is off.
- Take-profit and the 4h time-stop are checked by the bot, so they run late if the machine is asleep. Overdue
  positions are closed on the first cycle after waking.
- Stale market data makes the Kill-Switch pause new entries until fresh data arrives.
- State lives in `data/journal.sqlite` and at the broker, so restarts are safe. Back up `data/` occasionally.
- Disable sleep while trading (Windows: Power settings; macOS: `caffeinate -s`; Linux: your desktop's power settings).

## 8. Daily habits (2 minutes)
The Discord daily summary covers most of it. `tradebot report` shows P&L, AI credit used vs the budget, the promotion verdict and any halt.
After a HALT, read `data/tradebot.log` before running `tradebot reset-halt`.

## 9. Optional: external traders for the Replication Agent
Copy `data/replication_feed.example.json` to `data/replication_feed.json` and append actions you observe
(source, asset, buy/sell, UTC time). Each source is scored automatically on what the price did over the next
2 hours. It only influences trades after ≥ 20 scored actions with ≥ 55% hit rate and ≥ 0.5% average move.
