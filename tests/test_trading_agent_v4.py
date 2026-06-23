"""TradingAgentV4 구조 테스트"""
from pathlib import Path
import pytest
from agents.trading_agent_v4 import TradingPhaseV4, PHASE_TOOLS_V4, TradingAgentV4


def test_all_phases_defined():
    phases = list(TradingPhaseV4)
    names = {p.value for p in phases}
    assert "PREMARKET_SEJUK" in names
    assert "PREMARKET" in names
    assert "SCAN" in names
    assert "INTRADAY" in names
    assert "CLOSE" in names
    assert "MARKET_CLOSE" in names


def test_premarket_sejuk_has_limit_up_tool():
    tools = PHASE_TOOLS_V4[TradingPhaseV4.PREMARKET_SEJUK]
    assert "get_limit_up_stocks" in tools
    assert "get_premarket_movers" in tools
    assert "get_news_stock" in tools


def test_intraday_has_volume_trend_tool():
    tools = PHASE_TOOLS_V4[TradingPhaseV4.INTRADAY]
    assert "get_intraday_volume_trend" in tools
    assert "get_watchlist_intraday_snapshot" in tools


def test_scan_has_theme_and_news_tools():
    tools = PHASE_TOOLS_V4[TradingPhaseV4.SCAN]
    assert "get_theme_candidates" in tools
    assert "get_news_market" in tools
    assert "get_volume_surge" in tools


def test_trading_agent_v4_instantiates():
    agent = TradingAgentV4()
    assert agent is not None


# ── 프롬프트-도구 계약 검증 (코덱스 이슈 3 재발 방지) ───────────────────────
def test_intraday_has_market_context_for_reversal_bottom():
    """REVERSAL_BOTTOM 진입 전 폭락일 확인에 get_market_context가 필요하다."""
    tools = PHASE_TOOLS_V4[TradingPhaseV4.INTRADAY]
    assert "get_market_context" in tools, (
        "intraday 프롬프트가 REVERSAL_BOTTOM 폭락일 확인에 get_market_context를 요구함"
    )


def test_premarket_phase_is_not_supported_by_trading_agent_v4():
    """v4 PREMARKET은 RegimeAgent 직행이므로 TradingAgentV4가 직접 처리하면 안 된다."""
    agent = TradingAgentV4()
    with pytest.raises(ValueError, match="RegimeAgent directly"):
        agent.run(TradingPhaseV4.PREMARKET, {})


def test_scan_tools_include_market_context():
    """scan 프롬프트가 get_market_context로 폭락일을 감지한다."""
    tools = PHASE_TOOLS_V4[TradingPhaseV4.SCAN]
    assert "get_market_context" in tools


def test_market_close_prompt_does_not_reference_missing_snapshot():
    prompt_path = Path("prompts/agents/trading_agent_v4/market_close.md")
    prompt = prompt_path.read_text(encoding="utf-8")
    assert "**`market_close_data`: 마감 팩트 스냅샷이 이미 주입되어 있다**" not in prompt
    assert "없는 주입 데이터(`market_close_data` 등)를 있다고 가정하지 말 것" in prompt
