#!/usr/bin/env bash
# One-time local setup for macOS / Linux. Run from the repo root: bash scripts/setup_local.sh
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -e ".[dev]"
[ -f .env ] || { cp .env.example .env; echo "Created .env - fill in your keys before running."; }
python -m pytest -q
tradebot --mode simulated --offline cycle >/dev/null && echo "Offline smoke cycle OK."
echo
echo "Next: edit .env, then:"
echo "  source .venv/bin/activate"
echo "  tradebot validate                 # backtest gate on real Alpaca history"
echo "  tradebot cycle                    # one supervised paper cycle"
echo "  bash scripts/run_local.sh         # start the paper-trading loop"
