"""TradingAgent 테스트 - Phase별 도구 바인딩 + ReAct 루프 + 사전주입 컨텍스트"""
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
    assert "psearch_title" not in ctx["allowed_tools"]


def test_phase_tools_only_reference_known_tools():
    for phase, tools in PHASE_TOOLS.items():
        for tool in tools:
            assert tool in TOOL_REGISTRY, f"{phase}: unknown tool {tool}"


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
        {"next_action": "call_tool", "tool": "psearch_title", "tool_args": {}},
        {"next_action": "final", "action": "NO_TRADE", "reason": "psearch 사용 불가, 관망"},
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

