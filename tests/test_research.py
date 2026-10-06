"""Research harness: data split guard, no lookahead, conservative fills, portfolio sizing, freeze integrity."""
import io
import json
import zipfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from tradebot.data.providers import SyntheticProvider
from tradebot.research import history, search
from tradebot.research.engine import ALPACA_SPOT, PERP, simulate
from tradebot.research.families import EXITS, FAMILIES, TIMEFRAMES, entry_edges
from tradebot.research.portfolio import simulate_portfolio


# ---------------------------------------------------------------- data + split guard

def _zip(rows: list[list], header: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        lines = ([",".join(history.COLUMNS)] if header else []) + [",".join(map(str, r)) for r in rows]
        z.writestr("k.csv", "\n".join(lines))
    return buf.getvalue()


def _row(ts_ms, px=100.0, unit=1):
    return [ts_ms * unit, px, px + 1, px - 1, px, 10, ts_ms * unit + 299999, 1000, 5, 6, 600, 0]


def test_parse_handles_milliseconds_microseconds_and_headers():
    t0 = int(datetime(2025, 3, 1, tzinfo=timezone.utc).timestamp() * 1000)
    ms = history.parse_klines_zip(_zip([_row(t0)]))
    us = history.parse_klines_zip(_zip([_row(t0, unit=1000)], header=True))
    assert ms.index[0] == us.index[0] == pd.Timestamp("2025-03-01", tz="UTC")
    assert list(ms.columns) == ["open", "high", "low", "close", "volume", "taker_buy_base"]


def test_download_load_and_refuse_test_period(tmp_path):
    def fetch(url):
        y, m = map(int, url.rsplit("-", 2)[-2:][0:1] + [url.rsplit("-", 1)[-1][:2]])
        t0 = int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
        return _zip([_row(t0 + i * 300000, 100 + i) for i in range(3)])
    start, end = datetime(2025, 10, 1, tzinfo=timezone.utc), datetime(2025, 12, 31, tzinfo=timezone.utc)
    assert history.download("BTC/USD", start, end, fetch=fetch, cache=tmp_path) == 3
    df = history.load("BTC/USD", start, history.TEST_START, cache=tmp_path)
    assert len(df) == 9 and df.index.min() >= start
    with pytest.raises(history.LookaheadError):
        history.load("BTC/USD", start, datetime(2026, 2, 1, tzinfo=timezone.utc), cache=tmp_path)
    assert len(history.load("BTC/USD", start, datetime(2026, 2, 1, tzinfo=timezone.utc), allow_test=True,
                            cache=tmp_path)) == 9


# ---------------------------------------------------------------- no lookahead

@pytest.fixture(scope="module")
def bars5():
    df = SyntheticProvider(seed=21).bars("ETH/USD", 5, limit=8000).copy()
    df["taker_buy_base"] = df["volume"] * 0.5
    return df


@pytest.mark.parametrize("tf", TIMEFRAMES)
def test_no_family_looks_ahead(bars5, tf):
    full = history.resample(bars5, tf)
    for cut_hours in (300, 433, 517):
        k5 = cut_hours * 12                                   # cut on an hour boundary (complete HTF bars)
        part = history.resample(bars5.iloc[:k5], tf)
        last = part.index[-1]
        for fam in FAMILIES.values():
            for params in fam.param_sets():
                for side in ("long", "short"):
                    a = fam.signal(full, side, tf, **params).fillna(False)
                    b = fam.signal(part, side, tf, **params).fillna(False)
                    assert bool(a.loc[last]) == bool(b.loc[last]), (fam.name, params, side, tf, cut_hours)


# ---------------------------------------------------------------- fills

def _df(o, h, l, c, freq="5min"):
    idx = pd.date_range("2025-01-01", periods=len(o), freq=freq, tz="UTC")
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1.0}, index=idx)


def test_stop_first_gap_fill_and_short_side():
    n = 40
    base = np.full(n, 100.0)
    df = _df(base.copy(), base + 0.5, base - 0.5, base.copy())
    df.iloc[31, :4] = [95.0, 200.0, 1.0, 100.0]       # gap down through the stop, also touches the target
    t = simulate(df, np.array([29]), "long", [(1.0, 2.0)], 6)[(1.0, 2.0)].iloc[0]
    assert t.exit_reason == "stop" and t.exit == 95.0  # stop first, filled at the gapped open
    df2 = _df(base.copy(), base + 0.5, base - 0.5, base.copy())
    df2.iloc[32, :4] = [100.0, 100.2, 90.0, 92.0]
    s = simulate(df2, np.array([29]), "short", [(1.0, 2.0)], 6)[(1.0, 2.0)].iloc[0]
    assert s.exit_reason == "take_profit" and s.gross > 0
    flat = _df(base.copy(), base + 0.01, base - 0.01, base.copy())
    f = simulate(flat, np.array([29]), "long", [(5.0, 2.0)], 6)[(5.0, 2.0)].iloc[0]
    assert f.exit_reason == "time_stop" and f.hours == pytest.approx(0.5)


# ---------------------------------------------------------------- portfolio

def _trades(n=10, gross=0.01, stop=0.01, mae=0.005, side="long", start="2026-01-02"):
    ts = pd.date_range(start, periods=n, freq="6h", tz="UTC")
    return pd.DataFrame({"strategy": "s", "asset": "BTC/USD", "entry_ts": ts, "exit_ts": ts + pd.Timedelta("1h"),
                         "side": side, "stop_frac": stop, "gross": gross, "hours": 1.0, "mae": mae})


def test_spot_caps_notional_at_equity():
    r = simulate_portfolio(_trades(1, gross=0.01, stop=0.01), PERP, risk_pct=20, leverage=1)
    # 20% risk with a 1% stop wants 20x notional; 1x caps it at equity
    assert r.final_equity == pytest.approx(500 * (1 + 0.01 - 2 * 8 / 1e4 - 1 / 1e4 / 8), abs=0.01)


def test_high_leverage_gets_liquidated_and_can_ruin():
    r = simulate_portfolio(_trades(3, gross=-0.01, stop=0.01, mae=0.06), PERP, risk_pct=20, leverage=20)
    assert r.liquidations >= 1 and r.final_equity < 500
    big = simulate_portfolio(_trades(20, gross=-0.012, stop=0.01, mae=0.012), PERP, risk_pct=20, leverage=20)
    assert big.ruined or big.final_equity < 50


def test_spot_model_drops_shorts():
    r = simulate_portfolio(_trades(5, side="short"), ALPACA_SPOT, risk_pct=1, leverage=1)
    assert r.trades == 0


def test_guardrails_halt_on_drawdown():
    losers = _trades(30, gross=-0.02, stop=0.01, mae=0.02)
    raw = simulate_portfolio(losers, PERP, risk_pct=5, leverage=10)
    guarded = simulate_portfolio(losers, PERP, risk_pct=5, leverage=10, guardrails=True)
    assert guarded.halted_at and guarded.final_equity > raw.final_equity
    assert guarded.max_drawdown_pct < 30


# ---------------------------------------------------------------- selection + freeze

def test_select_freeze_and_tamper_detection(tmp_path):
    ts = pd.date_range("2022-01-01", periods=1200, freq="1D", tz="UTC")
    rng = np.random.default_rng(0)
    good = pd.DataFrame({"entry_ts": ts, "gross": 0.004 + rng.normal(0, 0.01, len(ts)), "hours": 1.0})
    bad = good.assign(gross=-0.004 + rng.normal(0, 0.01, len(ts)))
    fam = "ema_cross"
    g_id = f"{fam}|long|60m|fast=9,slow=50,trend_filter=True|stop1.5xATR,rr2.0"
    b_id = f"{fam}|long|60m|fast=20,slow=100,trend_filter=False|stop1.0xATR,rr2.0"
    doc = search.select({g_id: good, b_id: bad})
    perp = [c for c in doc["champions"] if c["cost_model"] == "perp"][0]
    assert perp["id"] == g_id and perp["validated"] and perp["params"]["trend_filter"] is True
    path = tmp_path / "frozen.json"
    body = search.freeze(doc, ["BTC/USD"], path)
    assert search.load_frozen(path)["frozen_sha256"] == body["frozen_sha256"]
    tampered = json.loads(path.read_text())
    tampered["champions"][0]["validated"] = not tampered["champions"][0]["validated"]
    path.write_text(json.dumps(tampered))
    with pytest.raises(RuntimeError):
        search.load_frozen(path)
