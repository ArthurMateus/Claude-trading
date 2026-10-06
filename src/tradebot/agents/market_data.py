"""📡 Market Data Agent — fetches, cleans and summarizes the market into `AssetState`s.

Data fetching and cleaning are code; one cheap LLM call per cycle reviews the whole universe for
data anomalies and refines the regime label.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from ..contracts import AssetState, MarketSnapshot, utcnow
from ..data.providers import MarketDataProvider
from ..indicators import classify_regime, feature_row
from ..strategies import active_setups
from .base import Agent

log = logging.getLogger(__name__)


class RegimeItem(BaseModel):
    asset: str
    regime: Literal["trend_up", "trend_down", "range", "volatile", "unknown"]
    data_suspect: bool
    note: str


class RegimeOut(BaseModel):
    assets: list[RegimeItem]


class MarketDataAgent(Agent):
    name = "market_data"
    job = ("review per-asset market summaries, flag suspicious data (stale prices, zero volume, absurd moves, "
           "crossed books) and label each asset's regime.")

    def __init__(self, ctx, provider: MarketDataProvider):
        super().__init__(ctx)
        self.provider = provider

    def snapshot(self, with_news: bool = True) -> MarketSnapshot:
        s = self.settings
        now = utcnow()
        assets: dict[str, AssetState] = {}
        bars: dict[str, pd.DataFrame] = {}
        news = {}
        for asset in s.universe:
            try:
                df = self.provider.bars(asset, s.bar_timeframe_minutes, limit=s.bars_lookback)
                ob = self.provider.orderbook(asset)
            except Exception as e:
                log.error("market data failed for %s: %s", asset, e)
                self.journal.log_event("api_error", f"market data {asset}: {e}", "WARN")
                continue
            issues = self._clean(df, ob, now)
            if df.empty or len(df) < 60:
                issues.append("insufficient history")
                continue
            f = feature_row(df, s.bar_timeframe_minutes)
            mid = (ob.bid + ob.ask) / 2
            assets[asset] = AssetState(
                asset=asset, ts=df.index[-1].to_pydatetime(), last=f["close"], bid=ob.bid, ask=ob.ask,
                spread_bps=(ob.ask - ob.bid) / mid * 1e4 if mid else 1e9, atr=f["atr"], atr_pct=f["atr_pct"],
                realized_vol_pct=f["realized_vol_pct"], ret_1h_pct=f["ret_1h_pct"], ret_24h_pct=f["ret_24h_pct"],
                volume_z=f["volume_z"], book_imbalance=ob.imbalance, regime=classify_regime(df),
                data_ok=not issues, data_issues=issues, active_setups=active_setups(df),
            )
            bars[asset] = df
            if with_news:
                try:
                    news[asset] = self.provider.news(asset, now - timedelta(hours=6))
                except Exception as e:
                    log.warning("news failed for %s: %s", asset, e)
                    news[asset] = []
        corr = None
        if len(bars) >= 2:
            rets = pd.DataFrame({a: df["close"].pct_change().tail(288) for a, df in bars.items()})
            corr = rets.corr()
        snap = MarketSnapshot(ts=now, assets=assets, bars=bars, news=news, correlations=corr)
        self._review(snap)
        return snap

    def _clean(self, df: pd.DataFrame, ob, now) -> list[str]:
        issues = []
        if df.empty:
            return ["no bars"]
        age = (now - df.index[-1].to_pydatetime()).total_seconds()
        if age > self.settings.kill_switch.max_data_staleness_seconds:
            issues.append(f"stale bars ({age:.0f}s old)")
        if ob.bid <= 0 or ob.ask <= 0 or ob.bid >= ob.ask:
            issues.append("crossed or empty order book")
        if (df["volume"].tail(12) <= 0).all():
            issues.append("zero volume last hour")
        if df["close"].pct_change().abs().tail(12).max() > 0.15:
            issues.append("bar move >15% (possible bad tick)")
        return issues

    def _review(self, snap: MarketSnapshot) -> None:
        if not snap.assets or not self.llm.online:
            return
        payload = {"assets": [a.model_dump(include={"asset", "last", "spread_bps", "atr_pct", "realized_vol_pct",
                                                     "ret_1h_pct", "ret_24h_pct", "volume_z", "regime",
                                                     "data_issues"}) for a in snap.assets.values()]}
        out = self.llm.structured(self.name, self.system_prompt, payload, RegimeOut)
        if out is None:
            return
        for item in out.assets:
            st = snap.assets.get(item.asset)
            if st is None:
                continue
            st.regime = item.regime
            if item.data_suspect:   # the LLM may only make data checks stricter
                st.data_ok = False
                st.data_issues.append(f"llm: {item.note}")
