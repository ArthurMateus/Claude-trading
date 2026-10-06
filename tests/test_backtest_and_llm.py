import numpy as np
import pandas as pd

from tradebot.backtest import run_setup, stats
from tradebot.contracts import TradeCandidate
from tradebot.llm import strict_schema
from tradebot.strategies import SETUPS, Setup


def _bars(closes):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="5min", tz="UTC")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": c, "high": c * 1.001, "low": c * 0.999, "close": c, "volume": 1.0}, index=idx)


def test_entry_is_next_bar_open_and_fees_are_charged():
    closes = np.linspace(100, 130, 200)
    always = Setup("always", "long", 1.0, 1.5, 10, "test", lambda df: pd.Series(True, index=df.index))
    trades = run_setup(_bars(closes), always, fee_bps=25, slippage_bps=5)
    assert trades
    t = trades[0]
    df = _bars(closes)
    assert abs(t.entry - df["open"].iloc[61] * 1.0005) < 1e-9          # signal at bar 60 -> fill at 61 open
    assert t.ret < t.exit / t.entry - 1                                # fees reduce the return


def test_stop_wins_when_both_levels_touch_in_one_bar():
    closes = [100.0] * 100
    df = _bars(closes)
    df.iloc[61:, df.columns.get_loc("high")] = 200.0
    df.iloc[61:, df.columns.get_loc("low")] = 1.0
    once = Setup("once", "long", 1.0, 1.5, 10, "test", lambda d: pd.Series(d.index == d.index[60], index=d.index))
    t = run_setup(df, once, 0, 0)[0]
    assert t.exit_reason == "stop"


def test_stats_on_empty_and_known_trades():
    assert stats([]).trades == 0
    closes = np.linspace(100, 130, 300)
    trades = run_setup(_bars(closes), SETUPS["trend_pullback"], 25, 5)
    st = stats(trades)
    assert st.trades == len(trades)


def test_strict_schema_is_structured_output_compatible():
    sch = strict_schema(TradeCandidate)

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            assert "default" not in node and "minimum" not in node
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(sch)
