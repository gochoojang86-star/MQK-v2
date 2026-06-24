"""TradingAgent 테스트 - Phase별 도구 바인딩 + ReAct 루프 + 사전주입 컨텍스트"""
from pathlib import Path
import pytest

from agents import trading_agent
from agents.trading_agent import (
    TOOL_REGISTRY,
    PHASE_TOOLS,
    TradingAgent,
    TradingPhase,
    build_context,
)
from market_intelligence.base import ToolFailure


class FakeLLMClient:
    """미리 정해진 응답 큐를 순서대로 반환하는 LLM 스텁."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def call(self, system, user, tier=None, expect_json=True):
        self.calls.append((system, user))
        return self._responses.pop(0)


def test_build_context_includes_allowed_tools_for_phase():
    ctx = build_context(
        phase=TradingPhase.INTRADAY,
        trading_date="2026-06-09",
        regime={"status": "YELLOW", "confidence": 44},
        drift_status="STABLE",
        risk_guidance={"buy_confidence_threshold": 75, "risk_per_trade_pct": 0.35,
                        "max_positions": 4, "min_trading_value_krw": 10_000_000_000},
        portfolio_snapshot={"positions": [], "position_count": 0},
        daily_pnl={"realized_pnl_pct": 0.0},
        risk_budget_remaining={"positions_left": 4, "daily_loss_remaining_pct": 2.0},
        watchlist=["005930", "000660"],
    )

    assert ctx["current_phase"] == "INTRADAY"
    assert ctx["watchlist"] == ["005930", "000660"]
    assert ctx["allowed_tools"] == PHASE_TOOLS[TradingPhase.INTRADAY]
    assert "psearch_title" in ctx["allowed_tools"]
    assert ctx["exploration_policy"] == {}


def test_phase_tools_only_reference_known_tools():
    for phase, tools in PHASE_TOOLS.items():
        for tool in tools:
            assert tool in TOOL_REGISTRY, f"{phase}: unknown tool {tool}"


def test_tier_for_phase_uses_reasoning_only_for_scan():
    agent = TradingAgent(mil=object(), llm=FakeLLMClient([]))

    assert agent._tier_for_phase(TradingPhase.SCAN).value == "standard"
    assert agent._tier_for_phase(TradingPhase.PREMARKET_SEJUK).value == "standard"
    assert agent._tier_for_phase(TradingPhase.INTRADAY).value == "standard"
    assert agent._tier_for_phase(TradingPhase.LATE_INTRADAY).value == "standard"
    assert agent._tier_for_phase(TradingPhase.PREMARKET).value == "fast"


def test_run_executes_allowed_tool_then_returns_final(monkeypatch):
    def fake_get_market_context(ctx, phase):
        return {"status": "YELLOW", "confidence": 44, "kospi_change_pct": -0.5}

    monkeypatch.setitem(TOOL_REGISTRY, "get_market_context", fake_get_market_context)

    llm = FakeLLMClient([
        {"next_action": "call_tool", "tool": "get_market_context", "tool_args": {}},
        {"next_action": "final", "action": "WATCHLIST_UPDATE",
         "watchlist": ["005930"], "reason": "반도체 강세"},
    ])
    agent = TradingAgent(mil=object(), llm=llm)
    context = build_context(
        phase=TradingPhase.SCAN, trading_date="2026-06-09",
        regime={"status": "YELLOW"}, drift_status="STABLE",
        risk_guidance={}, portfolio_snapshot={}, daily_pnl={}, risk_budget_remaining={},
    )

    result = agent.run(TradingPhase.SCAN, context)

    assert result["action"] == "WATCHLIST_UPDATE"
    assert result["watchlist"] == ["005930"]
    assert len(llm.calls) == 2
    # 두 번째 호출의 user 메시지에 첫 번째 도구 결과가 포함되어야 한다
    assert "kospi_change_pct" in llm.calls[1][1]


def test_run_blocks_tool_not_allowed_in_phase():
    llm = FakeLLMClient([
        {"next_action": "call_tool", "tool": "get_open_positions", "tool_args": {}},
        {"next_action": "final", "action": "NO_TRADE", "reason": "포지션 조회 도구는 intraday 직접 호출 불가, 관망"},
    ])
    agent = TradingAgent(mil=object(), llm=llm)
    context = build_context(
        phase=TradingPhase.INTRADAY, trading_date="2026-06-09",
        regime={"status": "YELLOW"}, drift_status="STABLE",
        risk_guidance={}, portfolio_snapshot={}, daily_pnl={}, risk_budget_remaining={},
        watchlist=["005930"],
    )

    result = agent.run(TradingPhase.INTRADAY, context)

    assert result["action"] == "NO_TRADE"
    # 차단 사유가 다음 턴 LLM에게 전달되었어야 한다
    assert "tool_not_allowed_in_phase" in llm.calls[1][1]


def test_run_handles_tool_failure(monkeypatch):
    def failing_get_flow(ctx, phase, ticker):
        raise ToolFailure("get_flow: circuit breaker open")

    monkeypatch.setitem(TOOL_REGISTRY, "get_flow", failing_get_flow)

    llm = FakeLLMClient([
        {"next_action": "call_tool", "tool": "get_flow", "tool_args": {"ticker": "005930"}},
        {"next_action": "final", "action": "NO_TRADE", "reason": "수급 데이터 없음"},
    ])
    agent = TradingAgent(mil=object(), llm=llm)
    context = build_context(
        phase=TradingPhase.INTRADAY, trading_date="2026-06-09",
        regime={"status": "YELLOW"}, drift_status="STABLE",
        risk_guidance={}, portfolio_snapshot={}, daily_pnl={}, risk_budget_remaining={},
        watchlist=["005930"],
    )

    result = agent.run(TradingPhase.INTRADAY, context)

    assert result["action"] == "NO_TRADE"
    assert "tool_failure" in llm.calls[1][1]


def test_run_aborts_to_no_trade_after_three_tool_failures(monkeypatch):
    def failing_get_flow(ctx, phase, ticker):
        raise ToolFailure("get_flow: circuit breaker open")

    def failing_get_market_context(ctx, phase):
        raise ToolFailure("get_market_context: timeout")

    monkeypatch.setitem(TOOL_REGISTRY, "get_flow", failing_get_flow)
    monkeypatch.setitem(TOOL_REGISTRY, "get_market_context", failing_get_market_context)

    llm = FakeLLMClient([
        {"next_action": "call_tool", "tool": "get_market_context", "tool_args": {}},
        {"next_action": "call_tool", "tool": "get_flow", "tool_args": {"ticker": "005930"}},
        {"next_action": "call_tool", "tool": "get_flow", "tool_args": {"ticker": "000660"}},
    ])
    agent = TradingAgent(mil=object(), llm=llm)
    context = build_context(
        phase=TradingPhase.INTRADAY, trading_date="2026-06-09",
        regime={"status": "YELLOW"}, drift_status="STABLE",
        risk_guidance={}, portfolio_snapshot={}, daily_pnl={}, risk_budget_remaining={},
        watchlist=["005930", "000660"],
    )

    result = agent.run(TradingPhase.INTRADAY, context)

    assert result["action"] == "NO_TRADE"
    assert result["reason"] == "tool_failures_exceeded"
    assert len(result["tool_failures"]) == 3
    assert len(llm.calls) == 3  # 3번째 실패 후 LLM을 더 호출하지 않고 즉시 종료


def test_run_handles_unexpected_tool_exception(monkeypatch):
    def crashing_get_flow(ctx, phase, ticker):
        raise TypeError("get_flow() missing 1 required positional argument")

    monkeypatch.setitem(TOOL_REGISTRY, "get_flow", crashing_get_flow)

    llm = FakeLLMClient([
        {"next_action": "call_tool", "tool": "get_flow", "tool_args": {"ticker": "005930"}},
        {"next_action": "final", "action": "NO_TRADE", "reason": "도구 오류"},
    ])
    agent = TradingAgent(mil=object(), llm=llm)
    context = build_context(
        phase=TradingPhase.INTRADAY, trading_date="2026-06-09",
        regime={"status": "YELLOW"}, drift_status="STABLE",
        risk_guidance={}, portfolio_snapshot={}, daily_pnl={}, risk_budget_remaining={},
        watchlist=["005930"],
    )

    result = agent.run(TradingPhase.INTRADAY, context)

    assert result["action"] == "NO_TRADE"
    assert "tool_execution_error" in llm.calls[1][1]


def test_run_handles_non_dict_tool_args(monkeypatch):
    def fake_get_flow(ctx, phase, **kwargs):
        return {"flow": "ok"}

    monkeypatch.setitem(TOOL_REGISTRY, "get_flow", fake_get_flow)

    llm = FakeLLMClient([
        {"next_action": "call_tool", "tool": "get_flow", "tool_args": "not_a_dict"},
        {"next_action": "final", "action": "NO_TRADE", "reason": "잘못된 인자"},
    ])
    agent = TradingAgent(mil=object(), llm=llm)
    context = build_context(
        phase=TradingPhase.INTRADAY, trading_date="2026-06-09",
        regime={"status": "YELLOW"}, drift_status="STABLE",
        risk_guidance={}, portfolio_snapshot={}, daily_pnl={}, risk_budget_remaining={},
        watchlist=["005930"],
    )

    result = agent.run(TradingPhase.INTRADAY, context)

    assert result["action"] == "NO_TRADE"
    assert "invalid_tool_args" in llm.calls[1][1]


def test_run_returns_no_trade_after_max_steps():
    llm = FakeLLMClient([
        {"next_action": "call_tool", "tool": "get_open_positions", "tool_args": {}},
    ] * 10)

    def fake_get_open_positions(ctx, phase):
        return {"positions": []}

    trading_agent.TOOL_REGISTRY["get_open_positions"] = fake_get_open_positions
    agent = TradingAgent(mil=object(), llm=llm, max_steps=3)
    context = build_context(
        phase=TradingPhase.CLOSE, trading_date="2026-06-09",
        regime={"status": "YELLOW"}, drift_status="STABLE",
        risk_guidance={}, portfolio_snapshot={}, daily_pnl={}, risk_budget_remaining={},
    )

    result = agent.run(TradingPhase.CLOSE, context)

    assert result["action"] == "NO_TRADE"
    assert result["reason"] == "max_steps_exceeded"
    assert len(llm.calls) == 3


def test_v3_strategy_prompts_do_not_contain_v4_titles():
    prompt_paths = [
        Path("prompts/agents/trading_agent/premarket_sejuk.md"),
        Path("prompts/agents/trading_agent/scan.md"),
        Path("prompts/agents/trading_agent/intraday.md"),
    ]
    for path in prompt_paths:
        text = path.read_text(encoding="utf-8")
        assert "TradingAgent v4" not in text


def test_regime_prompt_uses_korean_swing_philosophy_not_minervini():
    text = Path("prompts/agents/regime_agent.md").read_text(encoding="utf-8")
    assert "마크 미네르비니" not in text
    assert "국내 주식 스윙 트레이딩 시장 전략가" in text
    assert "국장 특유의 유동성·테마 순환·리스크오프 압력" in text
    assert "전략 허용/금지 결정 금지" in text


def test_close_prompt_mentions_gap_risk_and_time_exit():
    text = Path("prompts/agents/trading_agent/close.md").read_text(encoding="utf-8")
    assert "GAP_RISK_EXIT" in text
    assert "TIME_EXIT" in text
    assert "익일 갭 리스크" in text


def test_v3_reversal_prompts_use_single_reversal_tactic_name():
    scan_text = Path("prompts/agents/trading_agent/scan.md").read_text(encoding="utf-8")
    intraday_text = Path("prompts/agents/trading_agent/intraday.md").read_text(encoding="utf-8")

    assert "REVERSAL_BOTTOM" not in scan_text
    assert "REVERSAL_BOTTOM" not in intraday_text
    assert '"setup": "VOLUME_SURGE_LEADER|THEME_CATALYST|REVERSAL"' in scan_text
    assert "### REVERSAL (폭락 대장주 저점 반등)" in intraday_text
    assert '"next_action": "final"' in intraday_text


def test_v3_prompts_treat_regime_as_reference_only():
    premarket_sejuk_text = Path("prompts/agents/trading_agent/premarket_sejuk.md").read_text(encoding="utf-8")
    scan_text = Path("prompts/agents/trading_agent/scan.md").read_text(encoding="utf-8")
    intraday_text = Path("prompts/agents/trading_agent/intraday.md").read_text(encoding="utf-8")
    close_text = Path("prompts/agents/trading_agent/close.md").read_text(encoding="utf-8")

    assert "RED면 전체 스킵" not in premarket_sejuk_text
    assert "후보 제외/채택은 장전 갭·뉴스·거래대금 연속성으로 결정" in premarket_sejuk_text
    assert "`regime`은 시장 온도 참고용일 뿐" in scan_text
    assert "실제 진입/청산 판단은 분봉, 거래대금, 수급 이탈 여부로 결정" in intraday_text
    assert "실제 청산 여부는 보유 이유 유지 여부, 거래대금, 장후반 가격 실패" in close_text


def test_run_normalizes_tool_request():
    llm = FakeLLMClient([
        {
            "next_action": "tool_request",
            "missing_capability": "realtime_orderbook_imbalance",
            "why_needed": "돌파 강도를 체결강도와 호가잔량으로 검증할 수 없음",
            "priority": "high",
            "affected_tickers": ["005930"],
            "suggested_data_source": ["KIS websocket"],
            "fallback_action": "NO_TRADE",
        }
    ])
    agent = TradingAgent(mil=object(), llm=llm)
    context = build_context(
        phase=TradingPhase.INTRADAY, trading_date="2026-06-09",
        regime={"status": "YELLOW"}, drift_status="STABLE",
        risk_guidance={}, portfolio_snapshot={}, daily_pnl={}, risk_budget_remaining={},
        watchlist=["005930"],
    )

    result = agent.run(TradingPhase.INTRADAY, context)

    assert result["action"] == "TOOL_REQUEST"
    assert result["tool_request"]["missing_capability"] == "realtime_orderbook_imbalance"
    assert result["tool_request"]["priority"] == "high"
    assert result["tool_request"]["phase"] == "INTRADAY"
    assert result["tool_request"]["affected_tickers"] == ["005930"]


def test_execute_tool_ignores_args_for_no_arg_tools(monkeypatch):
    called = {}

    def fake_get_market_context(ctx, phase):
        called["phase"] = phase
        return {"ok": True}

    monkeypatch.setitem(TOOL_REGISTRY, "get_market_context", fake_get_market_context)
    agent = TradingAgent(mil=object(), llm=FakeLLMClient([]))

    result = agent._execute_tool(
        TradingPhase.INTRADAY,
        "get_market_context",
        {"date": "2026-06-15", "scope": "intraday", "phase": "INTRADAY"},
    )

    assert result == {"ok": True}
    assert called["phase"] == "INTRADAY"


def test_execute_tool_filters_unrecognized_args(monkeypatch):
    called = {}

    def fake_get_realtime_price(ctx, phase, tickers):
        called["tickers"] = tickers
        return {"tickers": tickers}

    monkeypatch.setitem(TOOL_REGISTRY, "get_realtime_price", fake_get_realtime_price)
    agent = TradingAgent(mil=object(), llm=FakeLLMClient([]))

    result = agent._execute_tool(
        TradingPhase.INTRADAY,
        "get_realtime_price",
        {"ticker": "005930", "date": "2026-06-15", "scope": "intraday"},
    )

    assert result == {"tickers": ["005930"]}
    assert called["tickers"] == ["005930"]


def test_execute_tool_normalizes_ohlcv_days_to_period(monkeypatch):
    called = {}

    def fake_get_ohlcv(ctx, phase, ticker, period=60):
        called["ticker"] = ticker
        called["period"] = period
        return {"ticker": ticker, "period": period}

    monkeypatch.setitem(TOOL_REGISTRY, "get_ohlcv", fake_get_ohlcv)
    agent = TradingAgent(mil=object(), llm=FakeLLMClient([]))

    result = agent._execute_tool(
        TradingPhase.INTRADAY,
        "get_ohlcv",
        {"ticker": "005930", "days": 20, "date": "2026-06-15"},
    )

    assert result == {"ticker": "005930", "period": 20}
    assert called["ticker"] == "005930"
    assert called["period"] == 20


def test_execute_tool_normalizes_watchlist_snapshot_single_ticker(monkeypatch):
    called = {}

    def fake_watchlist_snapshot(ctx, phase, tickers):
        called["tickers"] = tickers
        return {"tickers": tickers}

    monkeypatch.setitem(TOOL_REGISTRY, "get_watchlist_intraday_snapshot", fake_watchlist_snapshot)
    agent = TradingAgent(mil=object(), llm=FakeLLMClient([]))

    result = agent._execute_tool(
        TradingPhase.INTRADAY,
        "get_watchlist_intraday_snapshot",
        {"ticker": "005930", "scope": "watchlist"},
    )

    assert result == {"tickers": ["005930"]}
    assert called["tickers"] == ["005930"]


def test_scan_max_steps_falls_back_to_deterministic_watchlist(monkeypatch):
    def fake_get_top_movers(ctx, phase):
        return {
            "change_rate_top": [
                {"ticker": "357780", "name": "솔브레인", "change_pct": 27.89, "trading_value_krw": 128_000_000_000},
                {"ticker": "403870", "name": "HPSP", "change_pct": 30.0, "trading_value_krw": 1_102_000_000_000},
            ],
            "overheated_bias_warning": True,
        }

    def fake_get_stock_status(ctx, phase, ticker):
        return {
            "ticker": ticker,
            "trading_halted": False,
            "administrative_issue": False,
            "is_limit_up": ticker == "403870",
        }

    monkeypatch.setitem(TOOL_REGISTRY, "get_top_movers", fake_get_top_movers)
    monkeypatch.setitem(TOOL_REGISTRY, "get_stock_status", fake_get_stock_status)

    llm = FakeLLMClient([
        {"next_action": "call_tool", "tool": "get_top_movers", "tool_args": {}},
        {"next_action": "call_tool", "tool": "get_stock_status", "tool_args": {"ticker": "357780"}},
        {"next_action": "call_tool", "tool": "get_stock_status", "tool_args": {"ticker": "403870"}},
    ])
    agent = TradingAgent(mil=object(), llm=llm, max_steps=3)
    context = build_context(
        phase=TradingPhase.SCAN,
        trading_date="2026-06-09",
        regime={"status": "YELLOW"},
        drift_status="STABLE",
        risk_guidance={"min_trading_value_krw": 12_000_000_000},
        portfolio_snapshot={},
        daily_pnl={},
        risk_budget_remaining={"positions_left": 2},
    )

    result = agent.run(TradingPhase.SCAN, context)

    assert result["action"] == "WATCHLIST_UPDATE"
    assert result["watchlist"] == ["357780"]
    assert result["overheated_bias_warning"] is True
    assert "deterministic_scan_fallback" in result["reason"]


def test_scan_empty_watchlist_from_llm_is_backfilled(monkeypatch):
    def fake_get_top_movers(ctx, phase):
        return {
            "change_rate_top": [
                {"ticker": "357780", "name": "솔브레인", "change_pct": 27.89, "trading_value_krw": 128_000_000_000},
            ],
            "overheated_bias_warning": True,
        }

    monkeypatch.setitem(TOOL_REGISTRY, "get_top_movers", fake_get_top_movers)

    llm = FakeLLMClient([
        {"next_action": "call_tool", "tool": "get_top_movers", "tool_args": {}},
        {"next_action": "final", "action": "WATCHLIST_UPDATE", "watchlist": [], "candidates": [], "reason": "llm empty"},
    ])
    agent = TradingAgent(mil=object(), llm=llm, max_steps=3)
    context = build_context(
        phase=TradingPhase.SCAN,
        trading_date="2026-06-09",
        regime={"status": "YELLOW"},
        drift_status="STABLE",
        risk_guidance={"min_trading_value_krw": 12_000_000_000},
        portfolio_snapshot={},
        daily_pnl={},
        risk_budget_remaining={"positions_left": 2},
    )

    result = agent.run(TradingPhase.SCAN, context)

    assert result["action"] == "WATCHLIST_UPDATE"
    assert result["watchlist"] == ["357780"]
    assert "llm empty" in result["reason"]


def test_scan_fallback_keeps_monitoring_watchlist_when_positions_left_zero(monkeypatch):
    def fake_get_top_movers(ctx, phase):
        return {
            "change_rate_top": [
                {"ticker": "357780", "name": "솔브레인", "change_pct": 27.89, "trading_value_krw": 128_000_000_000},
            ],
            "overheated_bias_warning": False,
        }

    monkeypatch.setitem(TOOL_REGISTRY, "get_top_movers", fake_get_top_movers)

    llm = FakeLLMClient([
        {"next_action": "call_tool", "tool": "get_top_movers", "tool_args": {}},
        {"next_action": "final", "action": "WATCHLIST_UPDATE", "watchlist": [], "candidates": [], "reason": "llm empty"},
    ])
    agent = TradingAgent(mil=object(), llm=llm, max_steps=3)
    context = build_context(
        phase=TradingPhase.SCAN,
        trading_date="2026-06-09",
        regime={"status": "YELLOW"},
        drift_status="STABLE",
        risk_guidance={"min_trading_value_krw": 12_000_000_000},
        portfolio_snapshot={},
        daily_pnl={},
        risk_budget_remaining={"positions_left": 0, "monitoring_slots": 6},
    )

    result = agent.run(TradingPhase.SCAN, context)

    assert result["action"] == "WATCHLIST_UPDATE"
    assert result["watchlist"] == ["357780"]


def test_scan_phase_allows_watchlist_intraday_snapshot():
    assert "get_watchlist_intraday_snapshot" in PHASE_TOOLS[TradingPhase.SCAN]


def test_psearch_tools_inject_hts_user_id_from_env(monkeypatch):
    monkeypatch.setenv("KIS_HTS_ID", "test-hts-id")
    captured = {}

    def fake_psearch_title(ctx, phase, user_id):
        captured["user_id"] = user_id
        return {"conditions": []}

    monkeypatch.setitem(TOOL_REGISTRY, "psearch_title", fake_psearch_title)

    llm = FakeLLMClient([
        {"next_action": "call_tool", "tool": "psearch_title", "tool_args": {}},
        {"next_action": "final", "action": "WATCHLIST_UPDATE", "watchlist": [], "reason": "조건없음"},
    ])
    agent = TradingAgent(mil=object(), llm=llm)
    context = build_context(
        phase=TradingPhase.SCAN, trading_date="2026-06-09",
        regime={"status": "YELLOW"}, drift_status="STABLE",
        risk_guidance={}, portfolio_snapshot={}, daily_pnl={}, risk_budget_remaining={},
    )

    agent.run(TradingPhase.SCAN, context)

    assert captured["user_id"] == "test-hts-id"

class FailingJsonLLM:
    """항상 ValueError(잘못된 JSON)를 던지는 LLM 스텁."""
    def __init__(self):
        self.calls = 0

    def call(self, system, user, tier=None, expect_json=True):
        self.calls += 1
        raise ValueError("LLM이 유효한 JSON을 반환하지 않았습니다.")


def test_run_degrades_to_no_trade_on_persistent_invalid_json():
    from agents.trading_agent import TradingAgent, TradingPhase
    agent = TradingAgent(mil=object(), llm=FailingJsonLLM())
    result = agent.run(TradingPhase.SCAN, {"watchlist": []})
    assert result["action"] == "NO_TRADE"
    assert "llm_invalid_json" in result["reason"]
    assert agent._llm.calls == 2  # 1회 재시도 후 강등
