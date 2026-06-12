"""TradingAgent - 단일 LLM이 Phase별 ReAct 루프로 MIL 16개 도구를 사용해
PREMARKET/SCAN/INTRADAY/CLOSE/MARKET_CLOSE 단계를 수행한다.

최종 출력은 proposal일 뿐이며, v2 Safety Layer(RiskOfficer/PositionSizer/
Telegram approval/OrderManager)가 코드로 강제한다.
"""
from __future__ import annotations

import json
import os
import re
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
    LATE_INTRADAY = "LATE_INTRADAY"  # 폭락일 전용 장 후반(15:1x) 과매도 낙주 진입
    CLOSE = "CLOSE"
    MARKET_CLOSE = "MARKET_CLOSE"


_PHASE_PROMPT_NAMES = {
    TradingPhase.PREMARKET: "trading_agent/premarket",
    TradingPhase.SCAN: "trading_agent/scan",
    TradingPhase.INTRADAY: "trading_agent/intraday",
    TradingPhase.LATE_INTRADAY: "trading_agent/late_intraday",
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
    TradingPhase.LATE_INTRADAY: [
        "get_market_context", "psearch_title", "psearch_result", "get_top_movers",
        "get_ohlcv", "get_intraday_candles", "get_realtime_price",
        "get_flow", "get_news_stock", "get_stock_status",
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

    def __init__(self, mil: MILContext, llm: LLMClient | None = None, max_steps: int = 12):
        self._mil = mil
        self._llm = llm or LLMClient()
        self._max_steps = max_steps

    def run(self, phase: TradingPhase, context: dict) -> dict:
        system_prompt = inject_agent(_PHASE_PROMPT_NAMES[phase])
        transcript = [json.dumps({"context": context}, ensure_ascii=False)]
        tool_history: list[dict[str, Any]] = []

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
                if phase == TradingPhase.SCAN:
                    response = self._maybe_backfill_scan_result(context, tool_history, response)
                return response

            if next_action == "call_tool":
                tool_name = response.get("tool", "")
                tool_args = response.get("tool_args", {})
                tool_result = self._execute_tool(phase, tool_name, tool_args)
                tool_history.append({
                    "tool": tool_name,
                    "args": tool_args,
                    "result": tool_result,
                })
                transcript.append(json.dumps(
                    {"tool_call": {"tool": tool_name, "args": tool_args}, "tool_result": tool_result},
                    ensure_ascii=False,
                ))
                continue

            return {"next_action": "final", "action": "NO_TRADE",
                    "reason": f"unknown_next_action:{next_action}"}

        if phase == TradingPhase.SCAN:
            return self._fallback_scan_result(context, tool_history, reason="max_steps_exceeded")
        return {"next_action": "final", "action": "NO_TRADE", "reason": "max_steps_exceeded"}

    def _fallback_scan_result(self, context: dict, tool_history: list[dict[str, Any]], reason: str) -> dict:
        """LLM scan 루프가 끝까지 수렴하지 못하면 수집한 도구 결과로 보수적 watchlist를 만든다."""
        min_trading_value = float(context.get("risk_guidance", {}).get("min_trading_value_krw", 0) or 0)
        positions_left = int(context.get("risk_budget_remaining", {}).get("positions_left", 0) or 0)
        max_watchlist = min(10, max(positions_left, 0))

        candidates: dict[str, dict[str, Any]] = {}
        overheated_bias_warning = False
        stock_status: dict[str, dict[str, Any]] = {}

        for item in tool_history:
            tool = item.get("tool")
            result = item.get("result") or {}
            if not isinstance(result, dict):
                continue

            if tool == "get_top_movers":
                overheated_bias_warning = bool(result.get("overheated_bias_warning"))
                rows = result.get("change_rate_top") or result.get("movers") or []
                for row in rows:
                    ticker = str(row.get("ticker") or "").strip()
                    if not _is_six_digit_ticker(ticker):
                        continue
                    trading_value = _coerce_float(
                        row.get("trading_value_krw", row.get("trading_value", row.get("volume", 0)))
                    )
                    candidates.setdefault(ticker, {
                        "ticker": ticker,
                        "name": row.get("name"),
                        "trading_value": trading_value,
                        "change_pct": _coerce_float(row.get("change_pct")),
                        "setup": "RELATIVE_STRENGTH",
                        "reason": "fallback:get_top_movers",
                    })
                    candidates[ticker]["trading_value"] = max(candidates[ticker]["trading_value"], trading_value)

            elif tool == "psearch_result":
                for row in result.get("candidates", []):
                    ticker = str(row.get("ticker") or "").strip()
                    if not _is_six_digit_ticker(ticker):
                        continue
                    candidates.setdefault(ticker, {
                        "ticker": ticker,
                        "name": row.get("name"),
                        "trading_value": _coerce_float(row.get("trading_value")),
                        "change_pct": _coerce_float(row.get("change_pct")),
                        "setup": "TREND",
                        "reason": "fallback:psearch_result",
                    })

            elif tool == "get_stock_status":
                ticker = str(result.get("ticker") or item.get("args", {}).get("ticker") or "").strip()
                if ticker:
                    stock_status[ticker] = result

        ranked: list[dict[str, Any]] = []
        for ticker, row in candidates.items():
            if row.get("trading_value", 0.0) < min_trading_value:
                continue
            status = stock_status.get(ticker, {})
            if status.get("trading_halted") or status.get("administrative_issue") or status.get("is_limit_up"):
                continue
            ranked.append(row)

        ranked.sort(key=lambda x: (x.get("trading_value", 0.0), x.get("change_pct", 0.0)), reverse=True)
        watchlist = [row["ticker"] for row in ranked[:max_watchlist]]

        fallback_candidates = [
            {
                "ticker": row["ticker"],
                "confidence": 65,
                "reason": row["reason"],
                "setup": row["setup"],
            }
            for row in ranked[:max_watchlist]
        ]

        return {
            "next_action": "final",
            "action": "WATCHLIST_UPDATE",
            "watchlist": watchlist,
            "candidates": fallback_candidates,
            "overheated_bias_warning": overheated_bias_warning,
            "reason": f"{reason}; deterministic_scan_fallback",
        }

    def _maybe_backfill_scan_result(
        self, context: dict, tool_history: list[dict[str, Any]], response: dict[str, Any]
    ) -> dict[str, Any]:
        """SCAN final 응답이 비어 있으면 deterministic fallback으로 watchlist를 보강한다."""
        if response.get("action") != "WATCHLIST_UPDATE":
            return response
        if response.get("watchlist"):
            return response

        fallback = self._fallback_scan_result(context, tool_history, reason="empty_watchlist_from_llm")
        if fallback.get("watchlist"):
            fallback["reason"] = f"{response.get('reason', '')} | {fallback['reason']}".strip(" |")
            return fallback
        return response

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


def _coerce_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _is_six_digit_ticker(value: str) -> bool:
    return bool(re.fullmatch(r"\d{6}", value))
