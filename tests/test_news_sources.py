"""News/social source parsing, asset matching, dedupe and the X spend cap (fixtures only, no network)."""
import json
from datetime import datetime, timedelta, timezone

from tradebot.config import NewsConfig
from tradebot.data.news_sources import NewsHub, asset_matcher, parse_reddit, parse_rss
from tradebot.journal import Journal

NOW = datetime.now(timezone.utc)
RSS = f"""<?xml version="1.0"?><rss><channel>
<item><title>Bitcoin ETF sees record inflows</title><description>&lt;p&gt;Big day&lt;/p&gt;</description>
<pubDate>{NOW.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate></item>
<item><title>Solana validator outage resolved</title><description>Network back</description>
<pubDate>{NOW.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate></item>
<item><title>Old news about ETH</title><description>stale</description>
<pubDate>{(NOW - timedelta(days=2)).strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate></item>
</channel></rss>""".encode()
REDDIT = json.dumps({"data": {"children": [
    {"data": {"title": "Is $BTC going to 200k?", "selftext": "", "created_utc": NOW.timestamp(), "score": 40, "num_comments": 10}},
    {"data": {"title": "I link my wallet to everything", "selftext": "", "created_utc": NOW.timestamp(), "score": 1, "num_comments": 0}},
]}}).encode()
X = json.dumps({"data": [{"text": "BTC breaking out", "created_at": NOW.isoformat().replace("+00:00", "Z"),
                          "public_metrics": {"like_count": 5, "retweet_count": 1, "reply_count": 0}}] * 10}).encode()


def test_parsers():
    items = parse_rss(RSS, "coindesk")
    assert [i.headline for i in items][:2] == ["Bitcoin ETF sees record inflows", "Solana validator outage resolved"]
    assert items[0].summary == "Big day" and items[0].source == "rss:coindesk" and items[0].kind == "news"
    posts = parse_reddit(REDDIT, "CryptoCurrency")
    assert posts[0].kind == "social" and posts[0].engagement == 60


def test_asset_matching_avoids_common_words():
    kw = NewsConfig().asset_keywords
    link = asset_matcher("LINK/USD", kw)
    assert link("Chainlink partners with bank") and link("$LINK pumps") and link("LINK breaks out")
    assert not link("I link my wallet to everything")
    btc = asset_matcher("BTC/USD", kw)
    assert btc("bitcoin hits high") and btc("Is $BTC going to 200k?") and not btc("Solana validator outage")


def fake_fetch(calls):
    def fetch(url, headers):
        calls.append(url)
        if "reddit" in url:
            return REDDIT
        if "api.x.com" in url:
            assert headers["Authorization"] == "Bearer tok"
            return X
        return RSS
    return fetch


def test_hub_maps_dedupes_filters_and_caches(monkeypatch):
    cfg = NewsConfig(rss_feeds=["coindesk", "decrypt"], reddit_subreddits=["CryptoCurrency"], x_enabled=False)
    calls = []
    hub = NewsHub(cfg, None, None, fetch=fake_fetch(calls))
    btc = hub.for_asset("BTC/USD")
    assert {i.headline for i in btc} == {"Bitcoin ETF sees record inflows", "Is $BTC going to 200k?"}  # deduped
    assert all(i.ts >= NOW - timedelta(hours=cfg.lookback_hours) for i in btc)
    n = len(calls)
    hub.for_asset("SOL/USD")
    assert len(calls) == n                       # general feeds cached across assets


def test_x_is_capped_and_charged_to_the_daily_budget(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "tok")
    cfg = NewsConfig(rss_feeds=[], reddit_subreddits=[], x_max_posts_per_day=25, x_posts_per_query=10)
    j = Journal(":memory:")
    calls = []
    hub = NewsHub(cfg, None, j, fetch=fake_fetch(calls), daily_budget_usd=2.0)
    for _ in range(5):
        hub.for_asset("BTC/USD")
    x_calls = [c for c in calls if "api.x.com" in c]
    assert len(x_calls) == 2                      # 10 + 10 posts; the third would exceed the 25/day cap
    assert abs(j.llm_spend_today() - 20 * 0.005) < 1e-9


def test_x_stops_when_budget_is_spent(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "tok")
    j = Journal(":memory:")
    j.record_llm_usage("orchestrator", "claude-opus-5-5", {}, 5.0)
    calls = []
    NewsHub(NewsConfig(rss_feeds=[], reddit_subreddits=[]), None, j, fetch=fake_fetch(calls),
            daily_budget_usd=2.0).for_asset("BTC/USD")
    assert not [c for c in calls if "api.x.com" in c]
