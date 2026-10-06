"""The hard limits. If any of these fail, the bot is not safe to run."""
import pandas as pd

from tradebot.guardrails import PortfolioState, allowed_risk_pct, check_trade

from .conftest import make_trade


def state(equity=10_000.0, cash=10_000.0, open_trades=None, day_start=None, **kw):
    return PortfolioState(equity=equity, cash=cash, open_trades=open_trades or [],
                          day_start_equity=day_start or equity, **kw)


def check(settings, st, **kw):
    args = dict(asset="ETH/USD", side="long", confidence=0.7, entry=100.0, stop=98.0, take_profit=104.0,
                spread_bps=5, requested_risk_pct=5.0, state=st, closed_trades=[], risk=settings.risk,
                cal=settings.calibration, corr=None)
    args.update(kw)
    return check_trade(**args)


def test_unproven_confidence_gets_base_risk_only(settings):
    risk, why = allowed_risk_pct(0.9, [], settings.risk, settings.calibration)
    assert risk == settings.risk.base_risk_pct
    assert "unproven" in why


def test_calibrated_bucket_unlocks_more_risk(settings):
    closed = [make_trade(i, confidence=0.9, result="WIN", r_multiple=1.5, status="CLOSED") for i in range(30)]
    risk, _ = allowed_risk_pct(0.9, closed, settings.risk, settings.calibration)
    assert settings.risk.base_risk_pct < risk <= settings.risk.max_risk_pct


def test_overconfident_bucket_stays_at_base(settings):
    closed = [make_trade(i, confidence=0.9, result="WIN" if i % 2 else "LOSS", r_multiple=0.2, status="CLOSED")
              for i in range(40)]
    risk, why = allowed_risk_pct(0.9, closed, settings.risk, settings.calibration)
    assert risk == settings.risk.base_risk_pct and "not calibrated" in why


def test_requested_risk_never_exceeds_allowed(settings):
    g = check(settings, state(), requested_risk_pct=50.0, stop=99.5, take_profit=101.0)
    assert g.approved
    assert g.risk_pct <= settings.risk.base_risk_pct + 1e-9


def test_notional_cap_limits_size_without_leverage(settings):
    g = check(settings, state(), stop=99.9, take_profit=100.3)   # very tight stop would need >100% notional
    assert g.approved
    assert g.quantity * 100.0 <= 10_000 * settings.risk.max_position_notional_pct / 100 + 1e-6


def test_heat_cap_trims_and_then_rejects(settings):
    settings.risk.heat_respects_daily_limit = False                           # plain 6% heat cap
    opens = [make_trade(i, asset=f"A{i}/USD", risk_usd=270.0) for i in range(2)]   # 5.4% heat
    g = check(settings, state(open_trades=opens), stop=99.0, take_profit=102.0)
    assert g.approved and g.risk_pct <= 0.6 + 1e-6
    full = [make_trade(i, asset=f"A{i}/USD", risk_usd=300.0) for i in range(2)]    # 6% heat
    assert not check(settings, state(open_trades=full)).approved


def test_heat_never_exceeds_remaining_daily_loss_budget(settings):
    # default: if every open stop hit at once we must still be inside the 5% daily loss limit
    opens = [make_trade(i, asset=f"A{i}/USD", risk_usd=250.0) for i in range(2)]   # 5.0% heat
    g = check(settings, state(open_trades=opens))
    assert not g.approved and "cap 5.00%" in g.reasons[-1]


def test_daily_loss_limit_blocks_entries(settings):
    g = check(settings, state(equity=9_400, day_start=10_000))
    assert not g.approved and "daily loss" in g.reasons[-1]


def test_heat_cap_shrinks_with_daily_losses(settings):
    # down 4.5% today -> only 0.5% of total open risk remains allowed
    g = check(settings, state(equity=9_550, cash=9_550, day_start=10_000), stop=99.0, take_profit=102.0)
    assert g.approved and g.risk_pct <= 0.5 + 1e-6


def test_correlated_cluster_cap(settings):
    corr = pd.DataFrame([[1, 0.9], [0.9, 1]], index=["BTC/USD", "ETH/USD"], columns=["BTC/USD", "ETH/USD"])
    settings.risk.max_cluster_risk_pct = 3.0
    btc = make_trade(0, asset="BTC/USD", risk_usd=300.0)   # 3% already in the BTC/ETH cluster
    g = check(settings, state(open_trades=[btc]), corr=corr)
    assert not g.approved and "cluster" in g.reasons[-1]


def test_rejections(settings):
    assert not check(settings, state(halted=True)).approved
    assert not check(settings, state(entries_paused=True)).approved
    assert not check(settings, state(), side="short").approved
    assert not check(settings, state(), confidence=0.5).approved
    assert not check(settings, state(), spread_bps=50).approved
    assert not check(settings, state(), take_profit=101.0).approved          # R:R 0.5
    assert not check(settings, state(), stop=101.0).approved                 # stop above entry
    many = [make_trade(i, asset=f"A{i}/USD", risk_usd=1.0) for i in range(settings.risk.max_open_positions)]
    assert not check(settings, state(open_trades=many)).approved
    assert not check(settings, state(open_trades=[make_trade(0, asset="ETH/USD", risk_usd=1)])).approved
