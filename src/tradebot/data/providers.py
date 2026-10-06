"""Market data sources. `AlpacaProvider` for paper/live, `SyntheticProvider` for offline runs and tests."""
from __future__ import annotations

import os
import zlib
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from ..contracts import NewsItem, OrderBookTop


class MarketDataProvider(ABC):
    @abstractmethod
    def bars(self, asset: str, timeframe_minutes: int, limit: int | None = None,
             start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        """OHLCV frame indexed by UTC timestamp, columns open/high/low/close/volume, oldest first."""

    @abstractmethod
    def orderbook(self, asset: str) -> OrderBookTop: ...

    @abstractmethod
    def news(self, asset: str, since: datetime) -> list[NewsItem]: ...


class SyntheticProvider(MarketDataProvider):
    """Deterministic random-walk markets with volatility clustering. For tests and dry runs only."""

    def __init__(self, seed: int = 7, base_prices: dict[str, float] | None = None):
        self.seed = seed
        self.base_prices = base_prices or {}
        self._cache: dict[tuple[str, int], pd.DataFrame] = {}
        self.now = datetime.now(timezone.utc).replace(second=0, microsecond=0)

    def _series(self, asset: str, tf: int, n: int = 20000) -> pd.DataFrame:
        key = (asset, tf)
        if key not in self._cache:
            rng = np.random.default_rng(zlib.crc32(f"{asset}|{self.seed}".encode()))
            vol = np.empty(n)
            v = 0.002
            for i in range(n):
                v = 0.0005 + 0.9 * v + 0.1 * abs(rng.normal(0, 0.003))
                vol[i] = v
            rets = rng.normal(0.00002, 1, n) * vol
            close = self.base_prices.get(asset, 100.0) * np.exp(np.cumsum(rets))
            open_ = np.concatenate([[close[0]], close[:-1]])
            wick = np.abs(rng.normal(0, 1, n)) * vol * close * 0.5
            high = np.maximum(open_, close) + wick
            low = np.minimum(open_, close) - wick
            volume = rng.lognormal(10, 0.5, n) * (1 + 50 * np.abs(rets))
            end = self.now - timedelta(minutes=tf)
            idx = pd.date_range(end=end, periods=n, freq=f"{tf}min", tz="UTC")
            self._cache[key] = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                                             "volume": volume}, index=idx)
        return self._cache[key]

    def bars(self, asset, timeframe_minutes, limit=None, start=None, end=None):
        df = self._series(asset, timeframe_minutes)
        if start is not None:
            df = df[df.index >= start]
        if end is not None:
            df = df[df.index <= end]
        return df.tail(limit) if limit else df

    def orderbook(self, asset):
        last = float(self._series(asset, 5)["close"].iloc[-1])
        half = last * 0.0002
        return OrderBookTop(bid=last - half, ask=last + half, bid_size=10, ask_size=9, imbalance=0.05)

    def news(self, asset, since):
        return []


class AlpacaProvider(MarketDataProvider):
    """Alpaca crypto market data (bars, order book) and Benzinga news via Alpaca."""

    def __init__(self, api_key: str | None = None, secret: str | None = None):
        from alpaca.data.historical import CryptoHistoricalDataClient
        from alpaca.data.historical.news import NewsClient

        key = api_key or os.environ.get("ALPACA_API_KEY")
        sec = secret or os.environ.get("ALPACA_SECRET_KEY")
        self._data = CryptoHistoricalDataClient(key, sec)
        self._news = NewsClient(key, sec) if key and sec else None

    def bars(self, asset, timeframe_minutes, limit=None, start=None, end=None):
        from alpaca.data.requests import CryptoBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        end = end or datetime.now(timezone.utc)
        if start is None:
            start = end - timedelta(minutes=timeframe_minutes * (limit or 300) * 1.2)
        req = CryptoBarsRequest(symbol_or_symbols=asset, start=start, end=end,
                                timeframe=TimeFrame(timeframe_minutes, TimeFrameUnit.Minute))
        df = self._data.get_crypto_bars(req).df
        if df.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(asset, level="symbol")
        df = df[["open", "high", "low", "close", "volume"]].astype(float).sort_index()
        return df.tail(limit) if limit else df

    def orderbook(self, asset):
        from alpaca.data.requests import CryptoLatestOrderbookRequest

        ob = self._data.get_crypto_latest_orderbook(CryptoLatestOrderbookRequest(symbol_or_symbols=asset))[asset]
        bids, asks = ob.bids[:10], ob.asks[:10]
        bid_depth = sum(b.s for b in bids)
        ask_depth = sum(a.s for a in asks)
        total = bid_depth + ask_depth
        return OrderBookTop(bid=bids[0].p, ask=asks[0].p, bid_size=bids[0].s, ask_size=asks[0].s,
                            imbalance=(bid_depth - ask_depth) / total if total else 0.0)

    def news(self, asset, since):
        if self._news is None:
            return []
        from alpaca.data.requests import NewsRequest

        symbol = asset.replace("/", "")
        res = self._news.get_news(NewsRequest(symbols=symbol, start=since, limit=20))
        items = res.data.get("news", []) if hasattr(res, "data") else []
        return [NewsItem(ts=n.created_at, headline=n.headline, summary=n.summary or "",
                         symbols=list(n.symbols or []), source=n.source or "") for n in items]


def make_provider(name: str) -> MarketDataProvider:
    return AlpacaProvider() if name == "alpaca" else SyntheticProvider()
