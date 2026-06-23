"""MQK v4 TradingAgent — 국장 세력주 스나이퍼.

v3 TradingAgent를 재사용하되 v4 전용 Phase/도구/프롬프트를 등록한다.
"""
from __future__ import annotations
from enum import Enum
from agents.trading_agent import TradingAgent, ModelTier


class TradingPhaseV4(str, Enum):
    PREMARKET_SEJUK = "PREMARKET_SEJUK"  # 08:45 장전 상한가 세력 검증
    PREMARKET       = "PREMARKET"        # 09:03 레짐 판단
    SCAN            = "SCAN"             # 09:17/11:17/13:17/15:00 종목 스캔
    INTRADAY        = "INTRADAY"         # 09:20~14:50 진입 + 세력 이탈 감시
    CLOSE           = "CLOSE"            # 15:18 마감 청산
    MARKET_CLOSE    = "MARKET_CLOSE"     # 17:00 복기


_PHASE_PROMPT_NAMES_V4: dict[TradingPhaseV4, str] = {
    TradingPhaseV4.PREMARKET_SEJUK: "trading_agent_v4/premarket_sejuk",
    TradingPhaseV4.PREMARKET:       "trading_agent_v4/premarket",
    TradingPhaseV4.SCAN:            "trading_agent_v4/scan",
    TradingPhaseV4.INTRADAY:        "trading_agent_v4/intraday",
    TradingPhaseV4.CLOSE:           "trading_agent_v4/close",
    TradingPhaseV4.MARKET_CLOSE:    "trading_agent_v4/market_close",
}

PHASE_TOOLS_V4: dict[TradingPhaseV4, list[str]] = {
    TradingPhaseV4.PREMARKET_SEJUK: [
        "get_limit_up_stocks", "get_premarket_movers",
        "get_news_stock", "get_news_market", "get_ohlcv",
    ],
    TradingPhaseV4.PREMARKET: [
        "get_market_context", "get_sector_breadth", "get_news_market",
        "get_sector_investor_flow", "get_foreign_institution_rank",
    ],
    TradingPhaseV4.SCAN: [
        "get_market_context", "get_theme_candidates", "get_news_market",
        "get_volume_surge", "get_foreign_institution_rank",
        "get_news_stock", "get_ohlcv", "get_stock_status",
        "get_sector_investor_flow", "get_top_movers",
        "psearch_title", "psearch_result",
        "kw_psearch_title", "kw_psearch_result",
    ],
    TradingPhaseV4.INTRADAY: [
        "get_watchlist_intraday_snapshot", "get_intraday_candles",
        "get_intraday_volume_trend", "get_realtime_price",
        "get_sector_investor_flow", "get_foreign_institution_rank",
        "get_intraday_investor_rank", "get_volume_surge",
        "get_news_stock", "get_flow", "get_stock_status",
        "get_orderbook",
    ],
    TradingPhaseV4.CLOSE: [
        "get_open_positions", "get_realtime_price",
        "get_intraday_volume_trend", "get_news_stock",
        "get_sector_investor_flow",
    ],
    TradingPhaseV4.MARKET_CLOSE: [
        "get_market_context", "get_sector_breadth", "get_news_market",
        "get_open_positions", "get_daily_pnl",
        "get_ohlcv", "get_sector_investor_flow",
    ],
}

_TIER_MAP: dict[TradingPhaseV4, ModelTier] = {
    TradingPhaseV4.PREMARKET_SEJUK: ModelTier.REASONING,
    TradingPhaseV4.PREMARKET:       ModelTier.FAST,
    TradingPhaseV4.SCAN:            ModelTier.REASONING,
    TradingPhaseV4.INTRADAY:        ModelTier.FAST,
    TradingPhaseV4.CLOSE:           ModelTier.FAST,
    TradingPhaseV4.MARKET_CLOSE:    ModelTier.FAST,
}


class TradingAgentV4:
    """v4 전용 TradingAgent 래퍼. v3 TradingAgent 내부 로직을 재사용."""

    def __init__(self, max_steps: int = 15):
        self._agent = TradingAgent(
            phase_prompt_names=_PHASE_PROMPT_NAMES_V4,  # type: ignore[arg-type]
            phase_tools=PHASE_TOOLS_V4,                  # type: ignore[arg-type]
            tier_map=_TIER_MAP,                          # type: ignore[arg-type]
            max_steps=max_steps,
        )

    def run(self, phase: TradingPhaseV4, context: dict) -> dict:
        return self._agent.run(phase, context)  # type: ignore[arg-type]
