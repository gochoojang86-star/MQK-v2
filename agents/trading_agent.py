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
from market_intelligence import market, portfolio, risk_filter, screening, stock, theme
from market_intelligence.base import MILContext, ToolFailure


class TradingPhase(str, Enum):
    PREMARKET_SEJUK = "PREMARKET_SEJUK"  # 08:45 장전 상한가 + 장전거래 세력 검증
    PREMARKET = "PREMARKET"
    SCAN = "SCAN"
    INTRADAY = "INTRADAY"
    LATE_INTRADAY = "LATE_INTRADAY"  # 폭락일 전용 장 후반(15:1x) 과매도 낙주 진입
    CLOSE = "CLOSE"
    MARKET_CLOSE = "MARKET_CLOSE"


_PHASE_PROMPT_NAMES = {
    TradingPhase.PREMARKET_SEJUK: "trading_agent/premarket_sejuk",
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
    "get_theme_candidates": theme.get_theme_candidates,
    "psearch_title": screening.psearch_title,
    "psearch_result": screening.psearch_result,
    "get_top_movers": screening.get_top_movers,
    "get_attention_rank": screening.get_attention_rank,
    "kw_psearch_title": screening.kw_psearch_title,
    "kw_psearch_result": screening.kw_psearch_result,
    "get_sector_investor_flow": screening.get_sector_investor_flow,
    "get_bid_queue_surge": screening.get_bid_queue_surge,
    "get_premarket_movers": screening.get_premarket_movers,
    "get_disparity_rank": screening.get_disparity_rank,
    "get_foreign_institution_rank": screening.get_foreign_institution_rank,
    "get_foreign_continuous_rank": screening.get_foreign_continuous_rank,
    "get_volume_surge": screening.get_volume_surge,
    "get_intraday_investor_rank": screening.get_intraday_investor_rank,
    "get_ohlcv": stock.get_ohlcv,
    "get_realtime_price": stock.get_realtime_price,
    "get_watchlist_intraday_snapshot": stock.get_watchlist_intraday_snapshot,
    "get_intraday_candles": stock.get_intraday_candles,
    "get_flow": stock.get_flow,
    "get_news_stock": stock.get_news_stock,
    "get_fundamentals": stock.get_fundamentals,
    "get_intraday_institutional_flow": stock.get_intraday_institutional_flow,
    "get_orderbook": stock.get_orderbook,
    "get_stock_status": risk_filter.get_stock_status,
    "get_event_schedule": risk_filter.get_event_schedule,
    "get_open_positions": portfolio.get_open_positions,
    "get_daily_pnl": portfolio.get_daily_pnl,
    # v4 tools
    "get_limit_up_stocks": screening.get_limit_up_stocks,
    "get_intraday_volume_trend": stock.get_intraday_volume_trend,
}

PHASE_TOOLS: dict[TradingPhase, list[str]] = {
    TradingPhase.PREMARKET_SEJUK: [
        "get_limit_up_stocks", "get_premarket_movers",
        "get_news_market", "get_news_stock", "get_ohlcv",
        "get_market_context", "get_sector_investor_flow",
    ],
    TradingPhase.PREMARKET: [
        "get_market_context", "get_sector_breadth", "get_intraday_index_candles",
        "get_news_market", "get_event_schedule", "get_ohlcv", "get_flow",
        "get_premarket_movers", "get_sector_investor_flow",
    ],
    TradingPhase.SCAN: [
        "get_market_context", "get_sector_breadth", "get_intraday_index_candles", "get_news_market",
        "get_theme_candidates",
        "psearch_title", "psearch_result",
        "kw_psearch_title", "kw_psearch_result",
        "get_top_movers", "get_attention_rank",
        "get_premarket_movers", "get_disparity_rank",
        "get_foreign_institution_rank", "get_foreign_continuous_rank",
        "get_sector_investor_flow", "get_volume_surge", "get_bid_queue_surge",
        "get_ohlcv", "get_realtime_price", "get_watchlist_intraday_snapshot",
        "get_flow", "get_stock_status", "get_news_stock", "get_fundamentals",
        "get_intraday_institutional_flow", "get_orderbook",
    ],
    TradingPhase.INTRADAY: [
        "get_market_context", "get_sector_breadth", "get_theme_candidates",
        "psearch_title", "psearch_result",
        "kw_psearch_title", "kw_psearch_result",
        "get_top_movers", "get_attention_rank",
        "get_foreign_institution_rank", "get_sector_investor_flow",
        "get_volume_surge", "get_bid_queue_surge", "get_intraday_investor_rank",
        "get_ohlcv", "get_realtime_price", "get_watchlist_intraday_snapshot", "get_intraday_candles",
        "get_flow", "get_intraday_institutional_flow", "get_news_stock", "get_stock_status",
        "get_orderbook",
        "get_intraday_volume_trend",  # 세력 이탈 감지 (VOLUME_DRY 신호)
    ],
    TradingPhase.LATE_INTRADAY: [
        "get_market_context", "psearch_title", "psearch_result",
        "kw_psearch_title", "kw_psearch_result",
        "get_top_movers", "get_disparity_rank", "get_attention_rank",
        "get_ohlcv", "get_intraday_candles", "get_realtime_price", "get_watchlist_intraday_snapshot",
        "get_flow", "get_intraday_institutional_flow", "get_news_stock", "get_stock_status",
        "get_orderbook",
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
_NO_ARG_TOOLS = {
    "get_market_context",
    "get_sector_breadth",
    "get_intraday_index_candles",
    "get_news_market",
    "get_top_movers",
    "get_attention_rank",
    "kw_psearch_title",
    "get_premarket_movers",
    "get_disparity_rank",
    "get_foreign_institution_rank",
    "get_foreign_continuous_rank",
    "get_sector_investor_flow",
    "get_volume_surge",
    "get_bid_queue_surge",
    "get_intraday_investor_rank",
    "get_open_positions",
    "get_daily_pnl",
    # v4 no-arg tools
    "get_limit_up_stocks",
}
_ALLOWED_TOOL_ARGS: dict[str, set[str]] = {
    "get_theme_candidates": {"topn_themes", "theme_date_tp", "component_date_tp"},
    "psearch_result": {"seq"},
    "kw_psearch_result": {"seq"},
    "get_ohlcv": {"ticker", "period"},
    "get_realtime_price": {"tickers"},
    "get_watchlist_intraday_snapshot": {"tickers"},
    "get_intraday_candles": {"ticker"},
    "get_flow": {"ticker"},
    "get_news_stock": {"ticker"},
    "get_fundamentals": {"ticker"},
    "get_intraday_institutional_flow": {"ticker"},
    "get_stock_status": {"ticker"},
    "get_event_schedule": {"ticker"},
    "get_orderbook": {"ticker"},
    # v4 tools
    "get_intraday_volume_trend": {"ticker"},
}
_MIN_MONITORING_WATCHLIST = 6
_MAX_WATCHLIST_SIZE = 10


def build_context(
    phase: TradingPhase,
    trading_date: str,
    regime: dict,
    drift_status: str,
    risk_guidance: dict,
    portfolio_snapshot: dict,
    daily_pnl: dict,
    risk_budget_remaining: dict,
    watchlist: list[Any] | None = None,
    context_timestamps: dict | None = None,
    exploration_policy: dict | None = None,
    allowed_tools: list[str] | None = None,
) -> dict:
    """TradingAgent에 사전 주입할 컨텍스트를 구성한다 (스펙 섹션 2.4)."""
    watchlist = watchlist or []
    watchlist_tickers = _normalize_tickers([], watchlist)
    return {
        "current_phase": phase.value if hasattr(phase, "value") else str(phase),
        "trading_date": trading_date,
        "regime": regime,
        "drift_status": drift_status,
        "risk_guidance": risk_guidance,
        "portfolio": portfolio_snapshot,
        "daily_pnl": daily_pnl,
        "risk_budget_remaining": risk_budget_remaining,
        "watchlist": watchlist,
        "watchlist_tickers": watchlist_tickers,
        "exploration_policy": exploration_policy or {},
        "allowed_tools": allowed_tools if allowed_tools is not None else list(PHASE_TOOLS[phase]),
        "context_timestamps": context_timestamps or {},
    }


class TradingAgent:
    """Phase별 프롬프트 + MIL 도구로 ReAct 루프를 실행하는 단일 LLM 에이전트."""

    def __init__(
        self,
        mil: MILContext | None = None,
        llm: LLMClient | None = None,
        max_steps: int = 12,
        phase_prompt_names: dict | None = None,
        phase_tools: dict | None = None,
        tier_map: dict | None = None,
    ) -> None:
        self._mil = mil
        self._llm = llm or LLMClient()
        self._max_steps = max_steps
        self._phase_prompt_names = phase_prompt_names or _PHASE_PROMPT_NAMES
        self._phase_tools = phase_tools or PHASE_TOOLS
        self._tier_map_override = tier_map

    def run(self, phase: TradingPhase, context: dict) -> dict:
        system_prompt = inject_agent(self._phase_prompt_names[phase])
        transcript = [json.dumps({"context": context}, ensure_ascii=False)]
        tool_history: list[dict[str, Any]] = []
        tier = self._tier_for_phase(phase)

        tool_failures: list[dict[str, Any]] = []
        llm_failures = 0
        for _ in range(self._max_steps):
            user_msg = "\n\n---\n\n".join(transcript)
            try:
                response = self._llm.call(
                    system=system_prompt, user=user_msg, tier=tier, expect_json=True
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

            # SCAN 모델이 next_action 없이 WATCHLIST_UPDATE/WAIT를 반환하는 경우 방어 처리.
            # 최신 모델(gpt-5.4-mini 등)은 ReAct 형식을 건너뛰고 바로 최종 답을 내기도 한다.
            _is_scan = hasattr(phase, "value") and str(phase.value) == "SCAN" or phase == TradingPhase.SCAN
            if next_action is None and _is_scan and response.get("action") in {"WATCHLIST_UPDATE", "WAIT"}:
                response["next_action"] = "final"
                next_action = "final"

            if next_action == "final":
                if phase == TradingPhase.SCAN or _is_scan:
                    response = self._maybe_backfill_scan_result(context, tool_history, response)
                return response

            if next_action == "tool_request":
                return self._finalize_tool_request(phase, context, response)

            if next_action == "call_tool":
                tool_name = response.get("tool", "")
                tool_args = response.get("tool_args", {})
                tool_result = self._execute_tool(phase, tool_name, tool_args)
                tool_history.append({
                    "tool": tool_name,
                    "args": tool_args,
                    "result": tool_result,
                })
                if "error" in tool_result:
                    tool_failures.append({
                        "tool": tool_name,
                        "error": tool_result.get("error"),
                        "message": tool_result.get("message"),
                    })
                    if len(tool_failures) >= 3:
                        return {
                            "next_action": "final",
                            "action": "NO_TRADE",
                            "reason": "tool_failures_exceeded",
                            "tool_failures": tool_failures,
                        }
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

    def _tier_for_phase(self, phase) -> ModelTier:
        if self._tier_map_override and phase in self._tier_map_override:
            return self._tier_map_override[phase]
        if phase in {TradingPhase.SCAN, TradingPhase.PREMARKET_SEJUK}:
            return ModelTier.STANDARD
        if phase in {TradingPhase.PREMARKET, TradingPhase.CLOSE, TradingPhase.MARKET_CLOSE}:
            return ModelTier.FAST
        return ModelTier.STANDARD

    def _finalize_tool_request(
        self,
        phase: TradingPhase,
        context: dict[str, Any],
        response: dict[str, Any],
    ) -> dict[str, Any]:
        tool_request = {
            "missing_capability": str(response.get("missing_capability") or "unspecified_capability"),
            "why_needed": str(response.get("why_needed") or "missing capability reduced decision quality"),
            "priority": str(response.get("priority") or "medium").lower(),
            "phase": str(response.get("phase") or (phase.value if hasattr(phase, "value") else str(phase))),
            "affected_tickers": _normalize_tickers(response.get("affected_tickers"), context.get("watchlist", [])),
            "suggested_data_source": _normalize_str_list(response.get("suggested_data_source")),
            "fallback_action": str(response.get("fallback_action") or "NO_TRADE"),
        }
        return {
            "next_action": "final",
            "action": "TOOL_REQUEST",
            "tool_request": tool_request,
            "reason": "missing_capability_detected",
        }

    def _fallback_scan_result(self, context: dict, tool_history: list[dict[str, Any]], reason: str) -> dict:
        """LLM scan 루프가 끝까지 수렴하지 못하면 수집한 도구 결과로 보수적 watchlist를 만든다."""
        min_trading_value = float(context.get("risk_guidance", {}).get("min_trading_value_krw", 0) or 0)
        max_watchlist = _scan_watchlist_limit(context)

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

    def _execute_tool(self, phase, tool_name: str, tool_args: dict) -> dict:
        if tool_name not in TOOL_REGISTRY:
            return {"error": "unknown_tool", "tool": tool_name}

        if tool_name not in self._phase_tools.get(phase, PHASE_TOOLS.get(phase, [])):
            phase_str = phase.value if hasattr(phase, "value") else str(phase)
            return {"error": "tool_not_allowed_in_phase", "tool": tool_name, "phase": phase_str}

        if not isinstance(tool_args, dict):
            return {"error": "invalid_tool_args", "tool": tool_name}

        func = TOOL_REGISTRY[tool_name]
        call_args = self._sanitize_tool_args(tool_name, tool_args)
        if tool_name in _TOOLS_REQUIRING_USER_ID:
            call_args["user_id"] = os.environ.get("KIS_HTS_ID", "")

        try:
            phase_str = phase.value if hasattr(phase, "value") else str(phase)
            return func(self._mil, phase_str, **call_args)
        except ToolFailure as e:
            return {"error": "tool_failure", "tool": tool_name, "message": str(e)}
        except Exception as e:
            return {"error": "tool_execution_error", "tool": tool_name, "message": str(e)}

    def _sanitize_tool_args(self, tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
        if tool_name in _NO_ARG_TOOLS:
            return {}
        if tool_name == "get_ohlcv":
            normalized: dict[str, Any] = {}
            ticker = tool_args.get("ticker")
            if ticker not in (None, ""):
                normalized["ticker"] = ticker
            period = tool_args.get("period", tool_args.get("days"))
            if period not in (None, ""):
                normalized["period"] = period
            return normalized
        if tool_name == "get_realtime_price":
            normalized: dict[str, Any] = {}
            tickers = tool_args.get("tickers")
            if isinstance(tickers, list):
                filtered = [str(t).strip() for t in tickers if str(t).strip()]
                if filtered:
                    normalized["tickers"] = filtered
                    return normalized
            ticker = str(tool_args.get("ticker") or "").strip()
            if ticker:
                return {"tickers": [ticker]}
            return {}
        if tool_name == "get_watchlist_intraday_snapshot":
            normalized: dict[str, Any] = {}
            tickers = tool_args.get("tickers")
            if isinstance(tickers, list):
                filtered = [str(t).strip() for t in tickers if str(t).strip()]
                if filtered:
                    normalized["tickers"] = filtered
                    return normalized
            ticker = str(tool_args.get("ticker") or "").strip()
            if ticker:
                return {"tickers": [ticker]}
            return {}
        allowed = _ALLOWED_TOOL_ARGS.get(tool_name)
        if allowed is None:
            return dict(tool_args)
        return {key: value for key, value in tool_args.items() if key in allowed}


def _coerce_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _is_six_digit_ticker(value: str) -> bool:
    return bool(re.fullmatch(r"\d{6}", value))


def _scan_watchlist_limit(context: dict[str, Any]) -> int:
    remaining = context.get("risk_budget_remaining", {}) or {}
    monitoring_slots = remaining.get("monitoring_slots")
    if monitoring_slots not in (None, ""):
        try:
            return min(_MAX_WATCHLIST_SIZE, max(int(monitoring_slots), 0))
        except (TypeError, ValueError):
            pass

    positions_left = remaining.get("positions_left", 0)
    try:
        positions_left_int = int(positions_left)
    except (TypeError, ValueError):
        positions_left_int = 0
    return min(_MAX_WATCHLIST_SIZE, max(positions_left_int, _MIN_MONITORING_WATCHLIST))


def _normalize_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    return [str(value).strip()]


def _normalize_tickers(value: Any, default_watchlist: list[Any]) -> list[str]:
    tickers = _normalize_str_list(value)
    filtered = [ticker for ticker in tickers if _is_six_digit_ticker(ticker)]
    if filtered:
        return filtered
    extracted: list[str] = []
    for item in default_watchlist:
        if isinstance(item, dict):
            ticker = str(item.get("ticker") or "").strip()
        else:
            ticker = str(item).strip()
        if _is_six_digit_ticker(ticker):
            extracted.append(ticker)
    return extracted
