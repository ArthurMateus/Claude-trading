"""Historical candles from Binance's public archive (data.binance.vision; free, no key) with a hard
train/test split: nothing on or after TEST_START can be loaded unless the caller is the frozen final test.

Binance spot USDT pairs are used as the price proxy for the Alpaca USD pairs the bot trades.
"""
from __future__ import annotations

import io
import logging
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from ..config import ROOT

log = logging.getLogger(__name__)

TEST_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
TRAIN_START = datetime(2022, 1, 1, tzinfo=timezone.utc)
VALIDATION_START = datetime(2025, 1, 1, tzinfo=timezone.utc)
CACHE = ROOT / "data" / "history" / "binance"
BASE_URL = "https://data.binance.vision/data/spot"
COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades",
           "taker_buy_base", "taker_buy_quote", "ignore"]

Fetch = Callable[[str], bytes]


class LookaheadError(RuntimeError):
    """Raised when research code tries to read test-period (2026+) data before the strategies are frozen."""


def binance_symbol(asset: str) -> str:
    base, quote = asset.split("/")
    return f"{base}{'USDT' if quote == 'USD' else quote}"


def http_fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "tradebot-research/0.1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def parse_klines_zip(raw: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        with z.open(z.namelist()[0]) as f:
            text = f.read().decode()
    first = text.split("\n", 1)[0].split(",")[0]
    df = pd.read_csv(io.StringIO(text), header=0 if not first.strip().isdigit() else None, names=COLUMNS)
    ts = df["open_time"].astype("int64")
    ts = ts.where(ts < 10**14, ts // 1000)          # 2025+ spot files use microseconds
    out = df[["open", "high", "low", "close", "volume", "taker_buy_base"]].astype(float)
    out.index = pd.to_datetime(ts, unit="ms", utc=True)
    out.index.name = "ts"
    return out


def _months(start: datetime, end: datetime) -> list[tuple[int, int]]:
    y, m, out = start.year, start.month, []
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def download(asset: str, start: datetime, end: datetime, interval: str = "5m", fetch: Fetch = http_fetch,
             cache: Path = CACHE) -> int:
    """Cache monthly files (daily files for the current, incomplete month). Returns number of files fetched."""
    sym = binance_symbol(asset)
    folder = cache / sym / interval
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    fetched = 0
    for y, m in _months(start, end):
        path = folder / f"{y:04d}-{m:02d}.parquet"
        complete_month = (y, m) < (now.year, now.month)
        if path.exists() and complete_month:
            continue
        try:
            if complete_month:
                df = parse_klines_zip(fetch(f"{BASE_URL}/monthly/klines/{sym}/{interval}/{sym}-{interval}-{y:04d}-{m:02d}.zip"))
            else:   # current month: stitch daily files
                parts = []
                for d in pd.date_range(datetime(y, m, 1, tzinfo=timezone.utc), now, freq="D"):
                    try:
                        parts.append(parse_klines_zip(fetch(
                            f"{BASE_URL}/daily/klines/{sym}/{interval}/{sym}-{interval}-{d:%Y-%m-%d}.zip")))
                    except Exception:
                        continue    # today's file isn't published yet
                if not parts:
                    continue
                df = pd.concat(parts)
        except Exception as e:
            log.warning("no data for %s %04d-%02d: %s", sym, y, m, e)
            continue
        df = df[~df.index.duplicated()].sort_index()
        df.to_parquet(path)
        fetched += 1
    return fetched


def load(asset: str, start: datetime, end: datetime, interval: str = "5m", *, allow_test: bool = False,
         cache: Path = CACHE) -> pd.DataFrame:
    """Cached candles in [start, end). Refuses any test-period data unless allow_test (frozen final test only)."""
    if end > TEST_START and not allow_test:
        raise LookaheadError(f"refusing to load data after {TEST_START:%Y-%m-%d} before strategies are frozen")
    folder = cache / binance_symbol(asset) / interval
    frames = []
    for y, m in _months(start, end - pd.Timedelta(microseconds=1)):
        path = folder / f"{y:04d}-{m:02d}.parquet"
        if path.exists():
            frames.append(pd.read_parquet(path))
    if not frames:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "taker_buy_base"])
    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated()]
    return df[(df.index >= start) & (df.index < end)]


def resample(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """OHLCV bars of `minutes`, indexed by bar OPEN time like the source. Incomplete trailing bar dropped."""
    if minutes == 5:
        return df
    rule = f"{minutes}min"
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    if "taker_buy_base" in df:
        agg["taker_buy_base"] = "sum"
    out = df.resample(rule, label="left", closed="left").agg(agg).dropna(subset=["open"])
    counts = df["close"].resample(rule, label="left", closed="left").count()
    full = counts.reindex(out.index) >= minutes // 5 * 0.8
    return out[full]


def coverage(asset: str, interval: str = "5m", cache: Path = CACHE) -> Optional[tuple[str, str]]:
    folder = cache / binance_symbol(asset) / interval
    files = sorted(folder.glob("*.parquet")) if folder.exists() else []
    return (files[0].stem, files[-1].stem) if files else None
