"""TradingAgent - 단일 LLM이 Phase별 ReAct 루프로 MIL 16개 도구를 사용해
PREMARKET/SCAN/INTRADAY/CLOSE/MARKET_CLOSE 단계를 수행한다.

최종 출력은 proposal일 뿐이며, v2 Safety Layer(RiskOfficer/PositionSizer/
Telegram approval/OrderManager)가 코드로 강제한다.
"""
from __future__ import annotations

import json
import os
from enum import Enum
from typing import Any, Callable

from config.settings import ModelTier
from llm.client import LLMClient
from llm.soul import inject_agent
from market_intelligence import market, portfolio, risk_filter, screening, stock
from market_intelligence.base import MILContext, ToolFailure


class TradingPhase(str, Enum):
    PREMARKET = "PREMARKET"
    SCAN = "SCAN"
    INTRADAY = "INTRADAY"
    CLOSE = "CLOSE"
    MARKET_CLOSE = "MARKET_CLOSE"


_PHASE_PROMPT_NAMES = {
    TradingPhase.PREMARKET: "trading_agent/premarket",
    TradingPhase.SCAN: "trading_agent/scan",
    TradingPhase.INTRADAY: "trading_agent/intraday",
    TradingPhase.CLOSE: "trading_agent/close",
    TradingPhase.MARKET_CLOSE: "trading_agent/market_close",
}

TOOL_REGISTRY: dict[str, Callable] = {
    "get_market_context": market.get_market_context,
    "get_sector_breadth": market.get_sector_breadth,
    "get_intraday_index_candles": market.get_intraday_index_candles,
    "get_news_market": market.get_news_market,
    "psearch_title": screening.psearch_title,
    "psearch_result": screening.psearch_result,
    "get_top_movers": screening.get_top_movers,
    "get_ohlcv": stock.get_ohlcv,
    "get_realtime_price": stock.get_realtime_price,
    "get_intraday_candles": stock.get_intraday_candles,
    "get_flow": stock.get_flow,
    "get_news_stock": stock.get_news_stock,
    "get_fundamentals": stock.get_fundamentals,
    "get_stock_status": risk_filter.get_stock_status,
    "get_event_schedule": risk_filter.get_event_schedule,
    "get_open_positions": portfolio.get_open_positions,
    "get_daily_pnl": portfolio.get_daily_pnl,
}

PHASE_TOOLS: dict[TradingPhase, list[str]] = {
    TradingPhase.PREMARKET: [
        "get_market_context", "get_sector_breadth", "get_intraday_index_candles",
        "get_news_market", "get_event_schedule", "get_ohlcv", "get_flow",
    ],
    TradingPhase.SCAN: [
        "get_market_context", "get_sector_breadth", "get_intraday_index_candles", "get_news_market",
        "psearch_title", "psearch_result", "get_top_movers",
        "get_ohlcv", "get_flow", "get_stock_status", "get_news_stock", "get_fundamentals",
    ],
    TradingPhase.INTRADAY: [
        "get_ohlcv", "get_intraday_candles", "get_flow", "get_news_stock", "get_stock_status",
    ],
    TradingPhase.CLOSE: [
        "get_market_context", "get_sector_breadth", "get_news_market",
        "get_ohlcv", "get_open_positions", "get_daily_pnl", "get_news_stock",
    ],
    TradingPhase.MARKET_CLOSE: [
        "get_market_context", "get_sector_breadth", "get_news_market", "get_ohlcv",
    ],
}

_TOOLS_REQUIRING_USER_ID = {"psearch_title", "psearch_result"}


def build_context(
    phase: TradingPhase,
    trading_date: str,
    regime: dict,
    drift_status: str,
    risk_guidance: dict,
    portfolio_snapshot: dict,
    daily_pnl: dict,
    risk_budget_remaining: dict,
    watchlist: list[str] | None = None,
    context_timestamps: dict | None = None,
) -> dict:
    """TradingAgent에 사전 주입할 컨텍스트를 구성한다 (스펙 섹션 2.4)."""
    return {
        "current_phase": phase.value,
        "trading_date": trading_date,
        "regime": regime,
        "drift_status": drift_status,
        "risk_guidance": risk_guidance,
        "portfolio": portfolio_snapshot,
        "daily_pnl": daily_pnl,
        "risk_budget_remaining": risk_budget_remaining,
        "watchlist": watchlist or [],
        "allowed_tools": list(PHASE_TOOLS[phase]),
        "context_timestamps": context_timestamps or {},
    }


class TradingAgent:
    """Phase별 프롬프트 + MIL 도구로 ReAct 루프를 실행하는 단일 LLM 에이전트."""

    def __init__(self, mil: MILContext, llm: LLMClient | None = None, max_steps: int = 6):
        self._mil = mil
        self._llm = llm or LLMClient()
        self._max_steps = max_steps

    def run(self, phase: TradingPhase, context: dict) -> dict:
        system_prompt = inject_agent(_PHASE_PROMPT_NAMES[phase])
        transcript = [json.dumps({"context": context}, ensure_ascii=False)]

        llm_failures = 0
        for _ in range(self._max_steps):
            user_msg = "\n\n---\n\n".join(transcript)
            try:
                response = self._llm.call(
                    system=system_prompt, user=user_msg, tier=ModelTier.STANDARD, expect_json=True
                )
            except ValueError as e:
                # LLM이 유효한 JSON을 반환하지 못한 경우 — 스케줄된 phase 전체가
                # 죽지 않도록 1회 재시도 후 NO_TRADE로 강등한다.
                llm_failures += 1
                if llm_failures >= 2:
                    return {"next_action": "final", "action": "NO_TRADE",
                            "reason": f"llm_invalid_json: {e}"}
                transcript.append(json.dumps(
                    {"error": "invalid_json_response",
                     "instruction": "직전 응답이 유효한 단일 JSON 오브젝트가 아니었다. 반드시 JSON 오브젝트 하나만 반환하라."},
                    ensure_ascii=False,
                ))
                continue

            next_action = response.get("next_action")
            if next_action == "final":
                return response

            if next_action == "call_tool":
                tool_name = response.get("tool", "")
                tool_args = response.get("tool_args", {})
                tool_result = self._execute_tool(phase, tool_name, tool_args)
                transcript.append(json.dumps(
                    {"tool_call": {"tool": tool_name, "args": tool_args}, "tool_result": tool_result},
                    ensure_ascii=False,
                ))
                continue

            return {"next_action": "final", "action": "NO_TRADE",
                    "reason": f"unknown_next_action:{next_action}"}

        return {"next_action": "final", "action": "NO_TRADE", "reason": "max_steps_exceeded"}

    def _execute_tool(self, phase: TradingPhase, tool_name: str, tool_args: dict) -> dict:
        if tool_name not in TOOL_REGISTRY:
            return {"error": "unknown_tool", "tool": tool_name}

        if tool_name not in PHASE_TOOLS[phase]:
            return {"error": "tool_not_allowed_in_phase", "tool": tool_name, "phase": phase.value}

        if not isinstance(tool_args, dict):
            return {"error": "invalid_tool_args", "tool": tool_name}

        func = TOOL_REGISTRY[tool_name]
        call_args: dict[str, Any] = dict(tool_args)
        if tool_name in _TOOLS_REQUIRING_USER_ID:
            call_args["user_id"] = os.environ.get("KIS_HTS_ID", "")

        try:
            return func(self._mil, phase.value, **call_args)
        except ToolFailure as e:
            return {"error": "tool_failure", "tool": tool_name, "message": str(e)}
        except Exception as e:
            return {"error": "tool_execution_error", "tool": tool_name, "message": str(e)}
