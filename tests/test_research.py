"""Research harness: data split guard, no lookahead, conservative fills, portfolio sizing, freeze integrity."""
import io
import json
import zipfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest
from pathlib import Path

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

UNIVERSE = ["BTC/USD", "ETH/USD", "SOL/USD"]


@pytest.fixture(scope="module")
def universe():
    prov = SyntheticProvider(seed=21)
    data = {}
    for a in UNIVERSE:
        df = prov.bars(a, 5, limit=9000).copy()
        df["taker_buy_base"] = df["volume"] * 0.5
        data[a] = df
    idx = data["BTC/USD"].index
    settle = pd.date_range(idx[0].ceil("8h"), idx[-1], freq="8h")
    rng = np.random.default_rng(3)
    funding = {a: pd.Series(1e-4 + rng.normal(0, 3e-4, len(settle)), index=settle) for a in UNIVERSE}
    return data, funding


@pytest.mark.parametrize("tf", TIMEFRAMES)
def test_no_family_looks_ahead(universe, tf):
    """Signals at the last complete bar must be identical with and without all later data (prices of every
    coin and funding), for every family including the cross-asset and funding ones."""
    from tradebot.research.families import build_context, signal_for
    data, funding = universe
    idx = data["BTC/USD"].index
    minute_of_day = idx.hour * 60 + idx.minute
    starts = np.flatnonzero(minute_of_day % tf < 5)            # first 5m bar of each tf bin
    full_ctx = build_context(data, tf, funding)
    full = {a: history.resample(df, tf) for a, df in data.items()}
    for frac in (0.55, 0.7, 0.85):
        k5 = int(starts[int(len(starts) * frac)])
        cut = idx[k5]
        part_data = {a: df.iloc[:k5] for a, df in data.items()}
        part_funding = {a: s[s.index <= cut] for a, s in funding.items()}
        part_ctx = build_context(part_data, tf, part_funding)
        for asset in UNIVERSE:
            part = history.resample(part_data[asset], tf)
            last = part.index[-1]
            for fam in FAMILIES.values():
                for params in fam.param_sets():
                    for side in ("long", "short"):
                        a = signal_for(fam, full[asset], side, tf, params, full_ctx, asset).fillna(False)
                        b = signal_for(fam, part, side, tf, params, part_ctx, asset).fillna(False)
                        assert bool(a.loc[last]) == bool(b.loc[last]), (fam.name, params, side, tf, asset, frac)


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


# ---------------------------------------------------------------- portfolio (mark-to-market)

T0 = pd.Timestamp("2026-01-02", tz="UTC")


def _prices(assets=("BTC/USD",), periods=2000, path=None):
    idx = pd.date_range(T0, periods=periods, freq="5min")
    return pd.DataFrame({a: (path if path is not None else np.full(periods, 100.0)) for a in assets}, index=idx)


def _trade(asset="BTC/USD", start_bar=0, bars=12, gross=0.01, stop=0.01, mae=0.005, side="long", strategy="s"):
    entry_ts = T0 + pd.Timedelta(minutes=5 * start_bar)
    return {"strategy": strategy, "asset": asset, "entry_ts": entry_ts,
            "exit_ts": entry_ts + pd.Timedelta(minutes=5 * bars),
            "exit_bar_ts": entry_ts + pd.Timedelta(minutes=5 * (bars - 1)), "side": side, "entry": 100.0,
            "stop_frac": stop, "gross": gross, "hours": bars * 5 / 60, "mae": mae}


def _trades(rows):
    return pd.DataFrame(rows)


def test_spot_caps_notional_at_equity():
    r = simulate_portfolio(_trades([_trade()]), PERP, risk_pct=20, leverage=1, prices=_prices())
    expected = 500 * (1 + 0.01 - 2 * 8 / 1e4 - 1 / 1e4 / 8)
    assert r.final_equity == pytest.approx(expected, abs=0.01)     # 20x wanted, 1x allowed


def test_high_leverage_gets_liquidated_and_can_ruin():
    r = simulate_portfolio(_trades([_trade(start_bar=i * 30, gross=-0.01, mae=0.06) for i in range(3)]),
                           PERP, risk_pct=20, leverage=20, prices=_prices())
    assert r.liquidations >= 1 and r.final_equity < 500
    big = simulate_portfolio(_trades([_trade(start_bar=i * 30, gross=-0.012, mae=0.012) for i in range(40)]),
                             PERP, risk_pct=20, leverage=20, prices=_prices())
    assert big.ruined or big.final_equity < 50


def test_spot_model_drops_shorts():
    r = simulate_portfolio(_trades([_trade(side="short")]), ALPACA_SPOT, risk_pct=1, leverage=1, prices=_prices())
    assert r.trades == 0


def test_drawdown_is_mark_to_market_with_concurrent_losers():
    """8 positions dip together, then all close in profit: realized DD would be 0, true DD is large."""
    assets = [f"A{i}/USD" for i in range(8)]
    path = np.full(2000, 100.0)
    path[5:10] = 99.1                                   # all eight sit 0.9% under water together
    rows = [_trade(asset=a, start_bar=0, bars=24, gross=0.005, stop=0.01, mae=0.009) for a in assets]
    r = simulate_portfolio(_trades(rows), PERP, risk_pct=5, leverage=20, prices=_prices(assets, path=path))
    # 20x caps total notional at $10k (4 positions); a 0.9% dip on $10k is 18% plus the entry-side half of the
    # 16 bps round trip (1.6%): a 19.6% drawdown that realized-only accounting would have reported as 0%
    assert r.final_equity > 500 and r.max_drawdown_pct == pytest.approx(19.6, abs=0.3)


def test_exit_pnl_is_not_available_before_the_exit_bar_closes():
    """A exits inside the bar B enters on: B must be sized without A's P&L (A is still open)."""
    a = _trade(asset="BTC/USD", start_bar=0, bars=12, gross=0.10, stop=0.01)          # exits at bar-12 close
    b = _trade(asset="ETH/USD", start_bar=11, bars=6, gross=0.0, stop=0.01, strategy="b")  # enters at bar 11 open
    prices = _prices(("BTC/USD", "ETH/USD"))
    r = simulate_portfolio(_trades([a, b]), PERP, risk_pct=1, leverage=20, prices=prices)
    a_only = simulate_portfolio(_trades([a]), PERP, risk_pct=1, leverage=20, prices=prices)
    b_pnl = r.final_equity - a_only.final_equity
    # B sized on ~$500 (A unrealized at a flat price), not on $500 + A's ~$50 profit
    assert b_pnl == pytest.approx(-(0.01 * 500 / 0.01) * (2 * 8 / 1e4 + 0.5 / 8 / 1e4), rel=0.02)


def test_daily_stop_counts_losses_before_the_first_entry_of_the_day():
    late = _trade(asset="ETH/USD", start_bar=348, bars=6, gross=0.01, strategy="b")     # day 2, 05:00
    prices = _prices(("BTC/USD", "ETH/USD"))
    # -7% realized on day 1 (exit closes 23:00): a new day starts clean, the 05:00 entry is allowed
    day1 = _trade(asset="BTC/USD", start_bar=270, bars=6, gross=-0.07, stop=0.01, mae=0.07)
    g1 = simulate_portfolio(_trades([day1, late]), PERP, risk_pct=100, leverage=1, prices=prices, guardrails=True)
    assert g1.trades == 2 and g1.skipped == 0
    # -7% realized at 00:20 on day 2, BEFORE that day's first entry: the 05:00 entry must be blocked
    day2 = _trade(asset="BTC/USD", start_bar=280, bars=12, gross=-0.07, stop=0.01, mae=0.07)
    g2 = simulate_portfolio(_trades([day2, late]), PERP, risk_pct=100, leverage=1, prices=prices, guardrails=True)
    raw = simulate_portfolio(_trades([day2, late]), PERP, risk_pct=100, leverage=1, prices=prices)
    assert g2.trades == 1 and g2.skipped == 1 and raw.trades == 2


def test_guardrails_flatten_and_halt_on_drawdown():
    path = np.full(2000, 100.0)
    path[20:] = 90.0                                        # 10% drop while long at 3x on ~100% notional
    rows = [_trade(start_bar=0, bars=40, gross=-0.10, stop=0.12, mae=0.10)] + \
           [_trade(start_bar=100 + i * 20, bars=12, gross=0.01, strategy=f"x{i}") for i in range(5)]
    raw = simulate_portfolio(_trades(rows), PERP, risk_pct=40, leverage=3, prices=_prices(path=path))
    guarded = simulate_portfolio(_trades(rows), PERP, risk_pct=40, leverage=3, prices=_prices(path=path),
                                 guardrails=True)
    assert guarded.halted_at and guarded.trades == 1 and raw.trades == 6


def test_one_position_per_asset():
    rows = [_trade(start_bar=0, bars=24), _trade(start_bar=2, bars=6, strategy="other")]
    r = simulate_portfolio(_trades(rows), PERP, risk_pct=1, leverage=3, prices=_prices())
    assert r.trades == 1 and r.skipped == 1


# ---------------------------------------------------------------- engine timing + overlap

def test_exit_timestamp_is_bar_close_and_trades_do_not_overlap():
    n = 120
    base = np.full(n, 100.0)
    df = _df(base.copy(), base + 0.01, base - 0.01, base.copy(), freq="60min")
    trades = simulate(df, np.arange(60, 80), "long", [(5.0, 2.0)], 4, "BTC/USD", 60)[(5.0, 2.0)]
    assert (trades["exit_ts"] - trades["entry_ts"] == pd.Timedelta(hours=4)).all()   # 4 bars, closes at bar end
    assert (trades["entry_ts"].iloc[1:].to_numpy() >= trades["exit_ts"].iloc[:-1].to_numpy()).all()


def test_every_family_fires_on_every_timeframe():
    from tradebot.research.families import build_context, signal_for
    prov = SyntheticProvider(seed=4)
    data = {}
    for a in ("BTC/USD", "ETH/USD", "SOL/USD", "LTC/USD", "LINK/USD"):
        df = prov.bars(a, 5, limit=40000).copy()
        df["taker_buy_base"] = df["volume"] * np.random.default_rng(0).uniform(0.3, 0.7, len(df))
        data[a] = df
    idx = data["BTC/USD"].index
    settle = pd.date_range(idx[0].ceil("8h"), idx[-1], freq="8h")
    funding = {a: pd.Series(np.random.default_rng(1).normal(1e-4, 3e-4, len(settle)), index=settle) for a in data}
    for tf in TIMEFRAMES:
        ctx = build_context(data, tf, funding)
        for fam in FAMILIES.values():
            if fam.name == "climax_reversal":
                continue   # random walks have no volume climaxes with rejection wicks
            if fam.name == "session_breakout" and tf == 240:
                continue   # 4h bars can't resolve a one-hour opening range
            fired = sum(len(entry_edges(signal_for(fam, history.resample(data[a], tf), side, tf, p, ctx, a)))
                        for a in ("ETH/USD", "SOL/USD") for p in fam.param_sets() for side in ("long", "short"))
            assert fired > 0, (fam.name, tf)


# ---------------------------------------------------------------- selection + freeze + ledger

def _select_doc():
    ts = pd.date_range("2022-01-01", periods=1400, freq="1D", tz="UTC")
    rng = np.random.default_rng(0)
    good = pd.DataFrame({"entry_ts": ts, "gross": 0.004 + rng.normal(0, 0.01, len(ts)), "hours": 1.0,
                         "asset": "BTC/USD"})
    bad = good.assign(gross=-0.004 + rng.normal(0, 0.01, len(ts)))
    fam = "ema_cross"
    g_id = f"{fam}|long|60m|fast=9,slow=50,trend_filter=True|stop1.5xATR,rr2.0,hold8h"
    b_id = f"{fam}|long|60m|fast=20,slow=100,trend_filter=False|stop1.0xATR,rr2.0,hold24h"
    return search.select({g_id: good, b_id: bad}), g_id


def test_select_freeze_and_tamper_detection(tmp_path):
    doc, g_id = _select_doc()
    perp = [c for c in doc["champions"] if c["cost_model"] == "perp"][0]
    assert perp["id"] == g_id and perp["validated"] and perp["params"]["trend_filter"] is True
    assert perp["exit"] == [1.5, 2.0, 8]
    path = tmp_path / "frozen.json"
    body = search.freeze(doc, ["BTC/USD"], path, ledger=tmp_path / "ledger.jsonl")
    assert body["headline"] == search.HEADLINE and body["contaminated"] is False
    assert search.load_frozen(path)["frozen_sha256"] == body["frozen_sha256"]
    tampered = json.loads(path.read_text())
    tampered["champions"][0]["validated"] = not tampered["champions"][0]["validated"]
    path.write_text(json.dumps(tampered))
    with pytest.raises(RuntimeError):
        search.load_frozen(path)


def test_zero_edge_rarely_validates():
    ts = pd.date_range("2022-01-01", periods=1400, freq="1D", tz="UTC")
    passed = 0
    for seed in range(40):
        rng = np.random.default_rng(seed)
        cost = (2 * (5 + 3) + 1 / 8) / 1e4                # perp round trip + 1h of funding
        noise = pd.DataFrame({"entry_ts": ts, "gross": cost + rng.normal(0, 0.01, len(ts)), "hours": 1.0,
                              "asset": "ETH/USD"})        # exactly zero edge after perp costs
        sid = "ema_cross|long|60m|fast=9,slow=50,trend_filter=True|stop1.5xATR,rr2.0,hold8h"
        champ = [c for c in search.select({sid: noise})["champions"] if c["cost_model"] == "perp"][0]
        passed += champ["validated"]
    assert passed <= 4                                 # ~2.5% expected at t >= 2


def test_ledger_blocks_refreeze_unless_contaminated(tmp_path):
    doc, _ = _select_doc()
    ledger = tmp_path / "ledger.jsonl"
    search.record_test_run({"run_at": "x"}, ledger)
    with pytest.raises(RuntimeError):
        search.freeze(doc, ["BTC/USD"], tmp_path / "f.json", ledger=ledger)
    body = search.freeze(doc, ["BTC/USD"], tmp_path / "f.json", contaminated=True, ledger=ledger)
    assert body["contaminated"] and body["prior_2026_views"] == 1


def test_source_hash_covers_indicators_and_portfolio():
    names = {Path(n).name for n in search.SOURCE_FILES}
    assert {"indicators.py", "portfolio.py", "test2026.py"} <= names


def test_liquidated_position_stays_liquidated_through_a_drawdown_halt():
    """An intrabar wick liquidates BTC (close stays flat); ETH/SOL then fall: the halt must not undo BTC's loss."""
    assets = ["BTC/USD", "ETH/USD", "SOL/USD"]
    prices = _prices(assets)
    prices.loc[prices.index[20]:, ["ETH/USD", "SOL/USD"]] = 97.0          # -3% on closes
    rows = [_trade(asset="BTC/USD", start_bar=0, bars=200, gross=0.0, stop=0.01, mae=0.06),  # wick past 20x liq
            _trade(asset="ETH/USD", start_bar=0, bars=200, gross=-0.03, stop=0.01, mae=0.03, strategy="e"),
            _trade(asset="SOL/USD", start_bar=0, bars=200, gross=-0.03, stop=0.01, mae=0.03, strategy="f")]
    raw = simulate_portfolio(_trades(rows), PERP, risk_pct=2, leverage=20, prices=prices)
    guarded = simulate_portfolio(_trades(rows), PERP, risk_pct=2, leverage=20, prices=prices, guardrails=True)
    assert guarded.halted_at
    assert guarded.final_equity <= raw.final_equity + 1e-6     # breakers can't turn a liquidation into a scratch


def test_liquidation_on_closes_realizes_margin_plus_fee():
    path = np.full(2000, 100.0)
    path[10:] = 94.0                                           # 6% down on closes at 20x (liq distance 4.5%)
    r = simulate_portfolio(_trades([_trade(start_bar=0, bars=100, gross=-0.06, stop=0.01, mae=0.06)]), PERP,
                           risk_pct=2, leverage=20, prices=_prices(path=path))
    notional = 0.02 * 500 / 0.01
    assert r.liquidations == 1
    assert r.final_equity == pytest.approx(500 - (notional / 20 + 0.005 * notional), abs=0.01)


def test_signal_to_entry_gap_drops_the_trade():
    n = 60
    base = np.full(n, 100.0)
    idx = pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC")
    idx = idx.where(np.arange(n) <= 30, idx + pd.Timedelta(hours=2))   # 2h outage right after bar 30
    df = pd.DataFrame({"open": base, "high": base + 0.5, "low": base - 0.5, "close": base, "volume": 1.0},
                      index=pd.DatetimeIndex(idx))
    trades = simulate(df, np.array([30, 40]), "long", [(1.0, 2.0)], 6, "BTC/USD", 5)[(1.0, 2.0)]
    assert len(trades) == 1 and trades["entry_ts"].iloc[0] == df.index[41]


def _periodic_5m(days_before=130, days=270, seed=1, periodic=True):
    from datetime import timedelta
    idx = pd.date_range(history.TEST_START - timedelta(days=days_before), periods=(days_before + days) * 288,
                        freq="5min")
    rng = np.random.default_rng(seed)
    if periodic:      # the same intraday path every day: +1% during 10:00-11:00 UTC, flat otherwise
        step = np.where(idx.hour == 10, 0.01 / 12, 0.0)
    else:
        step = rng.normal(0, 0.002, len(idx))
    c = 100 * np.exp(np.cumsum(step))
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.0005, "low": np.minimum(o, c) * 0.9995,
                         "close": c, "volume": 1.0}, index=idx)


def test_timing_null_keeps_time_of_day():
    from tradebot.research import test2026
    df5 = _periodic_5m()
    d = history.resample(df5, 60)
    sig = np.flatnonzero((d.index >= history.TEST_START) & (d.index.hour == 9))[5:60:2]   # enter at 10:00
    t = simulate(d, sig, "long", [(1.5, 2.0)], 1, "BTC/USD", 60)[(1.5, 2.0)]
    strat = PERP.net_trades(t).mean() * 1e4
    assert strat > 10
    null_mean, _ = test2026.null_expectancy({"id": "x", "tf": 60, "side": "long", "exit": [1.5, 2.0, 1]},
                                            {"BTC/USD": df5}, t, PERP)
    assert null_mean == pytest.approx(strat, abs=1.0)     # same hour of day, so no timing skill to find


def test_timing_null_rejects_luck_and_flags_skill():
    from tradebot.research import test2026
    df5 = _periodic_5m(periodic=False, seed=3)
    d = history.resample(df5, 60)
    test = np.flatnonzero(d.index >= history.TEST_START)[:-2]
    nxt = d["close"].to_numpy()[test + 1] / d["open"].to_numpy()[test + 1] - 1
    best = np.sort(test[np.argsort(nxt)[-40:]])                            # perfect foresight entries
    champion = {"id": "x", "tf": 60, "side": "long", "exit": [3.0, 10.0, 1]}
    t = simulate(d, best, "long", [(3.0, 10.0)], 1, "BTC/USD", 60)[(3.0, 10.0)]
    strat = PERP.net_trades(t).mean() * 1e4
    null_mean, null_p95 = test2026.null_expectancy(champion, {"BTC/USD": df5}, t, PERP)
    assert strat > null_p95 > null_mean
    assert abs(null_mean) < 20


def test_funding_paid_uses_settlements_while_open():
    from tradebot.research.engine import funding_paid
    rates = pd.Series(0.001, index=pd.date_range("2025-03-01", periods=12, freq="8h", tz="UTC"))
    ts = lambda h: pd.Timestamp("2025-03-01", tz="UTC") + pd.Timedelta(hours=h)
    trades = pd.DataFrame({"entry_ts": [ts(7), ts(7), ts(8), ts(90)], "exit_ts": [ts(17), ts(17), ts(16), ts(99)],
                           "side": ["long", "short", "long", "long"]})
    paid = funding_paid(trades, rates)
    assert paid[:3].tolist() == pytest.approx([0.002, -0.002, 0.001])   # 08:00+16:00; received; 16:00 only
    assert np.isnan(paid[3])                                              # beyond the published rates
    t = trades.assign(gross=0.0, hours=10.0, asset="BTC/USD", funding=paid)
    net = PERP.net_trades(t)
    rt = PERP.round_trip_bps(["BTC/USD"])[0] / 1e4
    assert net[0] == pytest.approx(-rt - 0.002) and net[1] == pytest.approx(-rt + 0.002)
    assert net[3] == pytest.approx(-rt - PERP.funding_bps_8h / 1e4 * 10 / 8)   # flat fallback
    assert ALPACA_SPOT.net_trades(t)[0] == pytest.approx(-ALPACA_SPOT.round_trip_bps(["BTC/USD"])[0] / 1e4)


def test_funding_rates_are_normalized_per_8h():
    from tradebot.research.families import build_context
    eight = pd.date_range("2025-03-01", periods=30, freq="8h", tz="UTC")
    four = pd.date_range(eight[-1] + pd.Timedelta(hours=4), periods=60, freq="4h", tz="UTC")
    rates = pd.concat([pd.Series(1e-4, index=eight), pd.Series(5e-5, index=four)])
    idx = pd.date_range("2025-03-01", "2025-03-30", freq="5min", tz="UTC", inclusive="left")
    df5 = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)
    f = build_context({"BTC/USD": df5}, 60, {"BTC/USD": rates})["funding"]["BTC/USD"].dropna()
    assert f.iloc[1:].to_numpy() == pytest.approx(1e-4)


def test_report_handles_a_run_where_no_portfolio_qualified(tmp_path):
    from tradebot.research import report
    meta = {"frozen_sha256": "a" * 64, "frozen_created_at": "2026-10-06T00:00:00+00:00", "source_sha256": "b" * 64,
            "contaminated": False, "test_start": "2026-01-01", "test_end": "2026-10-06", "test_days": 278,
            "assets": ["BTC/USD"], "start_equity": 500.0, "configs_searched": 1152, "prior_2026_runs": 0,
            "test_files_cached_before_freeze": [], "headline_spec": search.HEADLINE, "beats_null": 0,
            "beats_null_expected_by_chance": 0.1, "headline": None}
    (tmp_path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    pd.DataFrame([{"cost_model": "perp", "family": "ema_cross", "side": "long", "tf": 60, "validated": False,
                   "test_trades": 50, "test_pf": 0.8, "ret_r1_x3": -4.0}]).to_csv(tmp_path / "strategies.csv", index=False)
    pd.DataFrame().to_csv(tmp_path / "portfolio_grid.csv", index=False)
    pd.DataFrame().to_csv(tmp_path / "equity_curves.csv")
    text = report.write(tmp_path).read_text(encoding="utf-8")
    assert "No result" in text and "no champion passed the in-sample gate" in text


def test_funding_parse_and_test_period_guard(tmp_path):
    t0 = int(datetime(2025, 11, 1, tzinfo=timezone.utc).timestamp() * 1000)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        rows = ["calc_time,funding_interval_hours,last_funding_rate"] + \
               [f"{t0 + i * 8 * 3600 * 1000},8,{0.0001 * (i % 3)}" for i in range(6)]
        z.writestr("f.csv", "\n".join(rows))
    s = history.parse_funding_zip(buf.getvalue())
    assert len(s) == 6 and s.index[0] == pd.Timestamp("2025-11-01", tz="UTC") and s.iloc[1] == pytest.approx(1e-4)
    assert history.download_funding("BTC/USD", datetime(2025, 11, 1, tzinfo=timezone.utc),
                                    datetime(2025, 11, 30, tzinfo=timezone.utc), fetch=lambda url: buf.getvalue(),
                                    cache=tmp_path) == 1
    got = history.load_funding("BTC/USD", datetime(2025, 11, 1, tzinfo=timezone.utc), history.TEST_START,
                               cache=tmp_path)
    assert len(got) == 6
    with pytest.raises(history.LookaheadError):
        history.load_funding("BTC/USD", datetime(2025, 11, 1, tzinfo=timezone.utc),
                             datetime(2026, 3, 1, tzinfo=timezone.utc), cache=tmp_path)


def test_funding_is_only_visible_from_the_bar_it_settles_in():
    from tradebot.research.families import build_context
    idx = pd.date_range("2025-06-01", periods=48 * 12, freq="5min", tz="UTC")
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)
    settle = pd.Timestamp("2025-06-01 08:00", tz="UTC")
    ctx = build_context({"BTC/USD": df}, 60, {"BTC/USD": pd.Series([0.001], index=[settle])})
    f = ctx["funding"]["BTC/USD"]
    assert np.isnan(f.loc[pd.Timestamp("2025-06-01 06:00", tz="UTC")])     # bar closing 07:00: not yet known
    assert f.loc[pd.Timestamp("2025-06-01 07:00", tz="UTC")] == 0.001      # bar closing 08:00: known at its close
    assert np.isnan(f.loc[pd.Timestamp("2025-06-02 00:00", tz="UTC")])     # >16h stale: dropped
