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


def test_no_setup_looks_ahead():
    """A setup's value at bar k must be identical whether or not later bars exist."""
    from tradebot.data.providers import SyntheticProvider
    from tradebot.strategies import setup_signals

    df = SyntheticProvider(seed=11).bars("BTC/USD", 5, limit=2500)
    full = setup_signals(df)
    for k in (400, 777, 1203, 1650, 2001, 2499):
        part = setup_signals(df.iloc[:k])
        assert (part.iloc[-1] == full.iloc[k - 1]).all(), (k, part.iloc[-1], full.iloc[k - 1])


def test_every_setup_fires_somewhere_and_respects_max_hold():
    from tradebot.config import load_settings
    from tradebot.data.providers import SyntheticProvider
    from tradebot.strategies import setup_signals

    df = SyntheticProvider(seed=5).bars("ETH/USD", 5, limit=15000)
    counts = setup_signals(df).sum()
    assert (counts.drop("capitulation_reversal") > 0).all(), counts   # random walks have no sell climaxes
    assert all(s.max_hold_bars > 0 and s.reward_risk >= 1.5 for s in SETUPS.values())
    max_hold = load_settings().max_hold_minutes
    assert all(s.max_hold_bars * 5 <= max_hold for s in SETUPS.values())


def test_capitulation_reversal_fires_on_a_climax_bar():
    n = 120
    closes = np.linspace(110, 100, n)                      # steady decline -> low RSI
    df = _bars(closes)
    df["volume"] = 1.0 + np.random.default_rng(0).random(n) * 0.1
    i = n - 1
    df.iloc[i, df.columns.get_loc("open")] = 100.2
    df.iloc[i, df.columns.get_loc("high")] = 100.4
    df.iloc[i, df.columns.get_loc("low")] = 96.0            # long lower wick
    df.iloc[i, df.columns.get_loc("close")] = 100.0         # closes in the upper half
    df.iloc[i, df.columns.get_loc("volume")] = 50.0         # volume climax
    assert bool(SETUPS["capitulation_reversal"].rule(df).iloc[-1])
