#!/usr/bin/env bash
# Start the paper-trading loop with logs in data/tradebot.log. Ctrl+C to stop
# (open positions keep their protective stop orders at Alpaca while the bot is stopped).
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
exec tradebot --log-file data/tradebot.log run
