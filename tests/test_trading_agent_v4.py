"""TradingAgentV4 구조 테스트"""
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
