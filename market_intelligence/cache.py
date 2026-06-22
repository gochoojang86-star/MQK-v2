"""Phase-aware TTL 캐시.

스펙 섹션 3.5 TTL 표를 (tool, phase) → seconds 로 인코딩한다.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

# (tool_name, phase) -> TTL seconds. 스펙 섹션 3.5 기준.
TTL_TABLE: dict[tuple[str, str], int] = {
    ("get_ohlcv", "PREMARKET"): 300,
    ("get_ohlcv", "SCAN"): 120,
    ("get_ohlcv", "INTRADAY"): 120,
    ("get_ohlcv", "CLOSE"): 86400,

    ("get_realtime_price", "INTRADAY"): 15,

    ("get_intraday_candles", "INTRADAY"): 60,

    ("get_flow", "PREMARKET"): 600,
    ("get_flow", "SCAN"): 300,
    ("get_flow", "INTRADAY"): 300,
    ("get_flow", "CLOSE"): 900,

    ("get_news_stock", "PREMARKET"): 1800,
    ("get_news_stock", "SCAN"): 300,
    ("get_news_stock", "INTRADAY"): 600,
    ("get_news_stock", "CLOSE"): 900,

    ("get_news_market", "PREMARKET"): 1800,
    ("get_news_market", "SCAN"): 300,
    ("get_news_market", "INTRADAY"): 600,
    ("get_news_market", "CLOSE"): 900,

    ("get_stock_status", "PREMARKET"): 3600,
    ("get_stock_status", "SCAN"): 600,
    ("get_stock_status", "INTRADAY"): 600,
    ("get_stock_status", "CLOSE"): 3600,

    ("get_event_schedule", "PREMARKET"): 86400,
    ("get_event_schedule", "SCAN"): 86400,
    ("get_event_schedule", "INTRADAY"): 86400,
    ("get_event_schedule", "CLOSE"): 86400,

    ("get_market_context", "PREMARKET"): 300,
    ("get_market_context", "SCAN"): 120,
    ("get_market_context", "INTRADAY"): 120,
    ("get_market_context", "CLOSE"): 300,

    ("get_sector_breadth", "PREMARKET"): 300,
    ("get_sector_breadth", "SCAN"): 180,
    ("get_sector_breadth", "INTRADAY"): 180,
    ("get_sector_breadth", "CLOSE"): 300,

    ("get_sector_investor_flow", "PREMARKET"): 300,
    ("get_sector_investor_flow", "SCAN"): 180,
    ("get_sector_investor_flow", "INTRADAY"): 180,
    ("get_sector_investor_flow", "LATE_INTRADAY"): 180,

    ("get_foreign_institution_rank", "SCAN"): 180,
    ("get_foreign_institution_rank", "INTRADAY"): 180,
    ("get_foreign_institution_rank", "LATE_INTRADAY"): 180,

    ("get_foreign_continuous_rank", "SCAN"): 180,
    ("get_foreign_continuous_rank", "INTRADAY"): 180,
    ("get_foreign_continuous_rank", "LATE_INTRADAY"): 180,

    ("get_intraday_investor_rank", "SCAN"): 180,
    ("get_intraday_investor_rank", "INTRADAY"): 180,
    ("get_intraday_investor_rank", "LATE_INTRADAY"): 180,

    ("get_bid_queue_surge", "SCAN"): 180,
    ("get_bid_queue_surge", "INTRADAY"): 180,
    ("get_bid_queue_surge", "LATE_INTRADAY"): 180,

    ("get_volume_surge", "SCAN"): 180,
    ("get_volume_surge", "INTRADAY"): 180,
    ("get_volume_surge", "LATE_INTRADAY"): 180,

    ("get_attention_rank", "SCAN"): 180,
    ("get_attention_rank", "INTRADAY"): 180,
    ("get_attention_rank", "LATE_INTRADAY"): 180,

    ("kw_psearch_title", "SCAN"): 300,
    ("kw_psearch_result", "SCAN"): 180,
}

DEFAULT_TTL_SECONDS = 60


class MILCache:
    """도구 호출 결과를 (tool, phase, args) 키로 캐싱한다."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, datetime]] = {}

    def _key(self, tool: str, phase: str, args: dict) -> str:
        return f"{tool}:{phase}:{json.dumps(args, sort_keys=True, default=str)}"

    def _ttl(self, tool: str, phase: str) -> int:
        return TTL_TABLE.get((tool, phase), DEFAULT_TTL_SECONDS)

    def get(self, tool: str, phase: str, args: dict) -> Any | None:
        key = self._key(tool, phase, args)
        entry = self._store.get(key)
        if entry is None:
            return None
        value, ts = entry
        if (datetime.now() - ts).total_seconds() > self._ttl(tool, phase):
            del self._store[key]
            return None
        return value

    def set(self, tool: str, phase: str, args: dict, value: Any) -> None:
        key = self._key(tool, phase, args)
        self._store[key] = (value, datetime.now())

    def invalidate_tool(self, tool: str) -> None:
        prefix = f"{tool}:"
        for key in [k for k in self._store if k.startswith(prefix)]:
            del self._store[key]
