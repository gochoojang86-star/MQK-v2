"""MILCache 테스트"""
from datetime import datetime, timedelta

from market_intelligence.cache import MILCache


def test_cache_miss_returns_none():
    cache = MILCache()
    assert cache.get("get_ohlcv", "SCAN", {"ticker": "005930"}) is None


def test_cache_hit_returns_value():
    cache = MILCache()
    cache.set("get_ohlcv", "SCAN", {"ticker": "005930"}, {"price": 70000})
    assert cache.get("get_ohlcv", "SCAN", {"ticker": "005930"}) == {"price": 70000}


def test_cache_different_args_are_different_keys():
    cache = MILCache()
    cache.set("get_ohlcv", "SCAN", {"ticker": "005930"}, {"price": 70000})
    assert cache.get("get_ohlcv", "SCAN", {"ticker": "000660"}) is None


def test_cache_expires_after_ttl():
    cache = MILCache()
    cache.set("get_realtime_price", "INTRADAY", {"ticker": "005930"}, {"price": 70000})
    # get_realtime_price/INTRADAY TTL = 15초. 직접 내부 timestamp를 과거로 조작.
    key = cache._key("get_realtime_price", "INTRADAY", {"ticker": "005930"})
    value, _ = cache._store[key]
    cache._store[key] = (value, datetime.now() - timedelta(seconds=20))
    assert cache.get("get_realtime_price", "INTRADAY", {"ticker": "005930"}) is None


def test_cache_unknown_tool_uses_default_ttl():
    cache = MILCache()
    cache.set("get_unknown_tool", "SCAN", {}, {"x": 1})
    assert cache.get("get_unknown_tool", "SCAN", {}) == {"x": 1}


def test_invalidate_tool_clears_all_entries_for_tool():
    cache = MILCache()
    cache.set("get_ohlcv", "SCAN", {"ticker": "005930"}, {"price": 1})
    cache.set("get_ohlcv", "SCAN", {"ticker": "000660"}, {"price": 2})
    cache.set("get_flow", "SCAN", {"ticker": "005930"}, {"flow": 1})
    cache.invalidate_tool("get_ohlcv")
    assert cache.get("get_ohlcv", "SCAN", {"ticker": "005930"}) is None
    assert cache.get("get_ohlcv", "SCAN", {"ticker": "000660"}) is None
    assert cache.get("get_flow", "SCAN", {"ticker": "005930"}) == {"flow": 1}
