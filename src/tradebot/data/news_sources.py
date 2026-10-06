"""News and social sources for the News Agent.

Free by default:
- Alpaca news (Benzinga newswire, per asset)
- RSS: CoinDesk, Cointelegraph, Decrypt (configurable)
- Reddit public JSON: r/CryptoCurrency, r/Bitcoin, r/ethereum, r/solana (configurable)
Opt-in, paid or keyed:
- X / Twitter API v2 recent search. Pay-per-use (~$0.005 per post read): only queried for assets that
  already have a validated setup firing, capped by `news.x_max_posts_per_day`, and the spend is recorded
  in `llm_usage` so it counts against the same daily budget as the LLM calls.
- CryptoPanic (aggregated news + community votes), if CRYPTOPANIC_TOKEN is set.

Everything fetched here is untrusted third-party text: it is passed to the LLM as data only.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Callable, Optional

from ..config import NewsConfig
from ..contracts import NewsItem

log = logging.getLogger(__name__)

Fetch = Callable[[str, dict], bytes]

RSS_FEEDS = {
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph": "https://cointelegraph.com/rss",
    "decrypt": "https://decrypt.co/feed",
}
X_COST_PER_POST_USD = 0.005
_TAG = re.compile(r"<[^>]+>")


def http_get(url: str, headers: dict) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "tradebot/0.1 (research bot)", **headers})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read()


def _clean(text: str, limit: int = 400) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG.sub(" ", text or ""))).strip()[:limit]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- asset matching

def asset_matcher(asset: str, keywords: dict[str, list[str]]) -> Callable[[str], bool]:
    """Names match case-insensitively; the ticker matches only as $TICKER or an upper-case word."""
    base = asset.split("/")[0]
    names = [k for k in keywords.get(asset, []) if k.lower() != base.lower()]
    name_re = re.compile(r"\b(" + "|".join(map(re.escape, names)) + r")\b", re.I) if names else None
    ticker_re = re.compile(r"(\$" + re.escape(base) + r"\b|\b" + re.escape(base.upper()) + r"\b)")
    return lambda text: bool((name_re and name_re.search(text)) or ticker_re.search(text))


def dedupe(items: list[NewsItem]) -> list[NewsItem]:
    seen, out = set(), []
    for it in sorted(items, key=lambda i: i.ts, reverse=True):
        key = re.sub(r"[^a-z0-9]", "", it.headline.lower())[:80]
        if key and key not in seen:
            seen.add(key)
            out.append(it)
    return out


# ---------------------------------------------------------------- general feeds (fetched once, mapped to assets)

def parse_rss(raw: bytes, source: str) -> list[NewsItem]:
    root = ET.fromstring(raw)
    items = []
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        fields = {c.tag.split("}")[-1]: (c.text or "") for c in node}

        def first(*names: str) -> str:
            return next((fields[n] for n in names if fields.get(n)), "")

        title = _clean(first("title"), 300)
        summary = _clean(first("description", "summary", "content"))
        raw_ts = first("pubDate", "published", "updated")
        try:
            ts = parsedate_to_datetime(raw_ts) if raw_ts and "," in raw_ts else datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except Exception:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if title:
            items.append(NewsItem(ts=ts, headline=title, summary=summary, source=f"rss:{source}", kind="news"))
    return items


def parse_reddit(raw: bytes, subreddit: str) -> list[NewsItem]:
    data = json.loads(raw)
    out = []
    for child in data.get("data", {}).get("children", []):
        d = child.get("data", {})
        if not d.get("title"):
            continue
        out.append(NewsItem(ts=datetime.fromtimestamp(d.get("created_utc", 0), timezone.utc),
                            headline=_clean(d["title"], 300), summary=_clean(d.get("selftext", "")),
                            source=f"reddit:r/{subreddit}", kind="social",
                            engagement=float(d.get("score", 0)) + 2 * float(d.get("num_comments", 0))))
    return out


# ---------------------------------------------------------------- asset-specific paid/keyed sources

def parse_x(raw: bytes) -> list[NewsItem]:
    data = json.loads(raw)
    out = []
    for t in data.get("data", []) or []:
        m = t.get("public_metrics", {})
        out.append(NewsItem(ts=datetime.fromisoformat(t["created_at"].replace("Z", "+00:00")),
                            headline=_clean(t.get("text", ""), 300), source="x", kind="social",
                            engagement=float(m.get("like_count", 0) + 2 * m.get("retweet_count", 0) + m.get("reply_count", 0))))
    return out


def parse_cryptopanic(raw: bytes) -> list[NewsItem]:
    data = json.loads(raw)
    out = []
    for p in data.get("results", []) or []:
        votes = p.get("votes", {}) or {}
        try:
            ts = datetime.fromisoformat(p["published_at"].replace("Z", "+00:00"))
        except Exception:
            continue
        out.append(NewsItem(ts=ts, headline=_clean(p.get("title", ""), 300), source="cryptopanic", kind="news",
                            summary=f"votes +{votes.get('positive', 0)}/-{votes.get('negative', 0)}",
                            engagement=float(votes.get("positive", 0) + votes.get("negative", 0))))
    return out


class NewsHub:
    """Collects news + social posts for an asset from every enabled source."""

    def __init__(self, cfg: NewsConfig, provider=None, journal=None, fetch: Fetch = http_get,
                 daily_budget_usd: float = float("inf")):
        self.cfg, self.provider, self.journal, self.fetch = cfg, provider, journal, fetch
        self.daily_budget_usd = daily_budget_usd
        self._general: list[NewsItem] = []
        self._general_at: Optional[datetime] = None
        self.x_token = os.environ.get("X_BEARER_TOKEN") if cfg.x_enabled else None
        self.cp_token = os.environ.get("CRYPTOPANIC_TOKEN") if cfg.cryptopanic_enabled else None

    def sources_enabled(self) -> list[str]:
        out = ["alpaca"] if self.provider else []
        out += [f"rss:{n}" for n in self.cfg.rss_feeds] + [f"reddit:r/{s}" for s in self.cfg.reddit_subreddits]
        if self.x_token:
            out.append(f"x (max {self.cfg.x_max_posts_per_day}/day)")
        if self.cp_token:
            out.append("cryptopanic")
        return out

    def _refresh_general(self) -> None:
        if self._general_at and _now() - self._general_at < timedelta(minutes=self.cfg.refresh_minutes):
            return
        items: list[NewsItem] = []
        for name in self.cfg.rss_feeds:
            url = RSS_FEEDS.get(name, name)
            try:
                items += parse_rss(self.fetch(url, {}), name)
            except Exception as e:
                log.warning("rss %s failed: %s", name, e)
        for sub in self.cfg.reddit_subreddits:
            try:
                items += parse_reddit(self.fetch(f"https://www.reddit.com/r/{sub}/new.json?limit=50", {}), sub)
            except Exception as e:
                log.warning("reddit r/%s failed: %s", sub, e)
        self._general, self._general_at = items, _now()

    def _x_budget_left(self) -> int:
        if not self.journal:
            return self.cfg.x_max_posts_per_day
        key = f"x_posts_read:{_now():%Y-%m-%d}"
        return max(0, self.cfg.x_max_posts_per_day - int(self.journal.get_state(key, 0) or 0))

    def _x_record(self, n: int) -> None:
        if not self.journal or n <= 0:
            return
        key = f"x_posts_read:{_now():%Y-%m-%d}"
        self.journal.set_state(key, int(self.journal.get_state(key, 0) or 0) + n)
        self.journal.record_llm_usage("news_x", "x-api-pay-per-use", {"input": n}, n * X_COST_PER_POST_USD)

    def _x(self, asset: str) -> list[NewsItem]:
        left = self._x_budget_left()
        if not self.x_token or left < 10:      # the API returns at least 10 posts per request
            return []
        if self.journal and self.journal.llm_spend_today() >= self.daily_budget_usd:
            return []
        base = asset.split("/")[0]
        names = [k for k in self.cfg.asset_keywords.get(asset, []) if k.lower() != base.lower()]
        query = "(" + " OR ".join([f"${base}"] + [f'"{n}"' if " " in n else n for n in names]) + ") lang:en -is:retweet"
        n = min(self.cfg.x_posts_per_query, left, 100)
        params = urllib.parse.urlencode({"query": query, "max_results": max(10, n), "sort_order": "relevancy",
                                         "tweet.fields": "created_at,public_metrics"})
        try:
            raw = self.fetch(f"https://api.x.com/2/tweets/search/recent?{params}",
                             {"Authorization": f"Bearer {self.x_token}"})
        except Exception as e:
            log.warning("x search failed for %s: %s", asset, e)
            return []
        items = parse_x(raw)
        self._x_record(len(items))
        return items

    def _cryptopanic(self, asset: str) -> list[NewsItem]:
        if not self.cp_token:
            return []
        params = urllib.parse.urlencode({"auth_token": self.cp_token, "currencies": asset.split("/")[0],
                                         "public": "true"})
        try:
            return parse_cryptopanic(self.fetch(f"{self.cfg.cryptopanic_url}?{params}", {}))
        except Exception as e:
            log.warning("cryptopanic failed for %s: %s", asset, e)
            return []

    def for_asset(self, asset: str) -> list[NewsItem]:
        since = _now() - timedelta(hours=self.cfg.lookback_hours)
        items: list[NewsItem] = []
        if self.provider is not None:
            try:
                items += [i.model_copy(update={"source": i.source or "alpaca", "kind": "news"})
                          for i in self.provider.news(asset, since)]
            except Exception as e:
                log.warning("alpaca news failed for %s: %s", asset, e)
        self._refresh_general()
        match = asset_matcher(asset, self.cfg.asset_keywords)
        items += [i for i in self._general if match(i.headline + " " + i.summary)]
        items += self._cryptopanic(asset)
        items += self._x(asset)
        return [i for i in dedupe(items) if i.ts >= since]
