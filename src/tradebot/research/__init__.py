"""Strategy research: design on 2022-2025, freeze, then a single out-of-sample test on 2026.

Workflow (see docs/RESEARCH.md):
  tradebot research download        # Binance spot 5m history through 2025 (no 2026 data)
  tradebot research search          # grid search on 2022-2024, selection on 2025, writes research/frozen.json
  tradebot research test2026        # downloads 2026, runs the frozen strategies once at 1-20% risk / 1-20x leverage
"""
