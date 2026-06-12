"""OrchestratorV3 테스트 - drift_state/watchlist 영속화 + Phase 오케스트레이션"""
import json
from pathlib import Path

import pytest

from agents.trading_agent import TradingPhase
from codes.risk_officer import RiskViolation
from orchestrator_v3 import (
    MQKOrchestratorV3,
    load_drift_state,
    save_drift_state,
    load_watchlist,
    save_watchlist,
)


# ── drift_state / watchlist 영속화 ──────────────────────────────────────────

def test_load_drift_state_returns_default_when_missing(tmp_path):
    path = tmp_path / "drift_state.json"
    state = load_drift_state(path=path, today="2026-06-09")

    assert state["date"] == "2026-06-09"
    assert state["today_caution_count"] == 0
    assert state["daily_lite_llm_calls"] == 0
    assert state["last_trigger_time"] == {}


def test_save_and_load_drift_state_same_day(tmp_path):
    path = tmp_path / "drift_state.json"
    state = {"date": "2026-06-09", "last_trigger_time": {"index_sharp_drop": "2026-06-09T10:00:00"},
             "today_caution_count": 1, "daily_lite_llm_calls": 1}
    save_drift_state(state, path=path)

    loaded = load_drift_state(path=path, today="2026-06-09")
    assert loaded == state


def test_load_drift_state_resets_on_new_day(tmp_path):
    path = tmp_path / "drift_state.json"
    save_drift_state({"date": "2026-06-09", "last_trigger_time": {}, "today_caution_count": 2,
                       "daily_lite_llm_calls": 3}, path=path)

    loaded = load_drift_state(path=path, today="2026-06-10")
    assert loaded["date"] == "2026-06-10"
    assert loaded["today_caution_count"] == 0
    assert loaded["daily_lite_llm_calls"] == 0


def test_save_and_load_watchlist_roundtrip(tmp_path):
    path = tmp_path / "watchlist.json"
    save_watchlist(["005930", "000660"], path=path)

    assert load_watchlist(path=path) == ["005930", "000660"]


def test_load_watchlist_returns_empty_when_missing(tmp_path):
    assert load_watchlist(path=tmp_path / "missing.json") == []


# ── Important 6: corrupt JSON state files must not crash loaders ───────────

def test_load_drift_state_returns_default_on_corrupt_json(tmp_path):
    path = tmp_path / "drift_state.json"
    path.write_bytes(b"{not valid json!!")

    state = load_drift_state(path=path, today="2026-06-09")

    assert state["date"] == "2026-06-09"
    assert state["today_caution_count"] == 0


def test_load_watchlist_returns_empty_on_corrupt_json(tmp_path):
    path = tmp_path / "watchlist.json"
    path.write_bytes(b"{not valid json!!")

    assert load_watchlist(path=path) == []


def test_save_drift_state_writes_atomically(tmp_path):
    path = tmp_path / "drift_state.json"
    save_drift_state({"date": "2026-06-09", "last_trigger_time": {}, "today_caution_count": 0,
                       "daily_lite_llm_calls": 0}, path=path)

    assert path.exists()
    assert not (tmp_path / "drift_state.json.tmp").exists()
    loaded = load_drift_state(path=path, today="2026-06-09")
    assert loaded["date"] == "2026-06-09"


def test_save_last_regime_dict_writes_atomically(tmp_path):
    from orchestrator_v3 import save_last_regime_dict, load_drift_state  # noqa: F401
    path = tmp_path / "last_regime.json"
    save_last_regime_dict({"status": "RED", "regime": "RISK_OFF"}, path=path)

    assert path.exists()
    assert not (tmp_path / "last_regime.json.tmp").exists()


# ── 오케스트레이터 헬퍼 ──────────────────────────────────────────────────────

def make_orchestrator(tmp_path: Path) -> MQKOrchestratorV3:
    orch = MQKOrchestratorV3.__new__(MQKOrchestratorV3)
    orch._today = "2026-06-09"
    orch._log_dir = tmp_path
    orch._mil = object()
    orch._atr_cache = {}
    orch._market_data = None
    return orch


def test_build_context_uses_mil_portfolio_tools(monkeypatch, tmp_path):
    import market_intelligence.portfolio as mil_portfolio

    monkeypatch.setattr(mil_portfolio, "get_open_positions",
                         lambda ctx, phase: {"positions": [{"ticker": "005930"}], "position_count": 1})
    monkeypatch.setattr(mil_portfolio, "get_daily_pnl",
                         lambda ctx, phase: {"realized_pnl_pct": -0.5, "realized_pnl_krw": -50000, "total_eval_amt": 10_000_000})

    orch = make_orchestrator(tmp_path)
    regime = {"status": "YELLOW", "regime": "SIDEWAYS", "confidence": 50,
              "risk_guidance": {"max_positions": 4, "buy_confidence_threshold": 75,
                                 "risk_per_trade_pct": 0.35, "min_trading_value_krw": 1_000_000_000},
              "timestamp": "2026-06-09T08:45:00"}

    ctx = orch._build_context(TradingPhase.INTRADAY, regime, "STABLE", watchlist=["005930"])

    assert ctx["portfolio"]["position_count"] == 1
    assert ctx["risk_budget_remaining"]["positions_left"] == 3
    assert ctx["daily_pnl"]["realized_pnl_pct"] == -0.5
    assert ctx["watchlist"] == ["005930"]
    assert ctx["allowed_tools"] == ["get_ohlcv", "get_intraday_candles", "get_flow", "get_news_stock", "get_stock_status"]


# ── Critical 3/4: graceful degradation on ToolFailure / bad kospi values ───

def test_collect_drift_snapshot_returns_none_on_tool_failure(monkeypatch, tmp_path):
    import market_intelligence.market as mil_market
    from market_intelligence.base import ToolFailure

    def _raise(ctx, phase):
        raise ToolFailure("circuit breaker open")

    monkeypatch.setattr(mil_market, "get_market_context", _raise)
    monkeypatch.setattr(mil_market, "get_intraday_index_candles",
                         lambda ctx, phase: {"candles": []})
    monkeypatch.setattr(mil_market, "get_sector_breadth",
                         lambda ctx, phase: {"market_breadth": {}})

    orch = make_orchestrator(tmp_path)
    assert orch._collect_drift_snapshot() is None


def test_collect_drift_snapshot_returns_none_when_kospi_is_none(monkeypatch, tmp_path):
    import market_intelligence.market as mil_market

    monkeypatch.setattr(mil_market, "get_market_context",
                         lambda ctx, phase: {"kospi": None, "foreign_net_buy_krw": 0})
    monkeypatch.setattr(mil_market, "get_intraday_index_candles",
                         lambda ctx, phase: {"candles": []})
    monkeypatch.setattr(mil_market, "get_sector_breadth",
                         lambda ctx, phase: {"market_breadth": {}})

    orch = make_orchestrator(tmp_path)
    assert orch._collect_drift_snapshot() is None


def test_run_intraday_v3_degrades_when_snapshot_unavailable(monkeypatch, tmp_path):
    import market_intelligence.portfolio as mil_portfolio
    import market_intelligence.market as mil_market
    from market_intelligence.base import ToolFailure

    monkeypatch.setattr(mil_portfolio, "get_open_positions",
                         lambda ctx, phase: {"positions": [], "position_count": 0})
    monkeypatch.setattr(mil_portfolio, "get_daily_pnl",
                         lambda ctx, phase: {"realized_pnl_pct": 0.0, "realized_pnl_krw": 0, "total_eval_amt": 10_000_000})

    def _raise(ctx, phase):
        raise ToolFailure("circuit breaker open")

    monkeypatch.setattr(mil_market, "get_market_context", _raise)
    monkeypatch.setattr(mil_market, "get_intraday_index_candles",
                         lambda ctx, phase: {"candles": []})
    monkeypatch.setattr(mil_market, "get_sector_breadth",
                         lambda ctx, phase: {"market_breadth": {}})

    orch = make_orchestrator(tmp_path)

    class RecordingDriftDetector:
        def __init__(self):
            self.calls = 0

        def check(self, **kwargs):
            self.calls += 1
            return {}

    orch._drift_detector = RecordingDriftDetector()
    orch._trading_agent = FakeTradingAgent({"action": "NO_TRADE", "proposals": [], "reason": "관망"})

    regime = {
        "status": "YELLOW", "regime": "SIDEWAYS", "confidence": 50,
        "risk_guidance": {"max_positions": 4, "buy_confidence_threshold": 75,
                           "risk_per_trade_pct": 0.35, "min_trading_value_krw": 1_000_000_000},
        "drift_triggers": [], "cooldown_minutes": 60, "max_daily_triggers": 3,
        "timestamp": "2026-06-09T08:45:00",
    }
    (tmp_path / "last_regime.json").write_text(json.dumps(regime), encoding="utf-8")
    (tmp_path / "watchlist.json").write_text(json.dumps({"watchlist": []}), encoding="utf-8")

    monkeypatch.setattr("orchestrator_v3._LAST_REGIME_PATH", tmp_path / "last_regime.json")
    monkeypatch.setattr("orchestrator_v3._DRIFT_STATE_PATH", tmp_path / "drift_state.json")
    monkeypatch.setattr("orchestrator_v3._WATCHLIST_PATH", tmp_path / "watchlist.json")

    result = orch.run_intraday_v3()

    assert result["action"] == "NO_TRADE"
    assert orch._drift_detector.calls == 0
    assert orch._trading_agent.calls[0][0] == TradingPhase.INTRADAY


def test_build_context_degrades_conservatively_when_portfolio_unavailable(monkeypatch, tmp_path):
    import market_intelligence.portfolio as mil_portfolio
    from market_intelligence.base import ToolFailure

    def boom(ctx, phase):
        raise ToolFailure("get_open_positions: 500 Server Error")

    monkeypatch.setattr(mil_portfolio, "get_open_positions", boom)
    monkeypatch.setattr(mil_portfolio, "get_daily_pnl", boom)

    orch = make_orchestrator(tmp_path)
    regime = {"status": "YELLOW", "regime": "SIDEWAYS", "confidence": 50,
              "risk_guidance": {"max_positions": 4}, "timestamp": "2026-06-12T08:45:00"}

    ctx = orch._build_context(TradingPhase.INTRADAY, regime, "STABLE", watchlist=[])

    assert ctx["risk_budget_remaining"]["positions_left"] == 0
    assert ctx["risk_budget_remaining"]["daily_loss_remaining_pct"] == 0.0
    assert ctx["portfolio"]["data_unavailable"] is True


def test_collect_drift_snapshot_combines_market_tools(monkeypatch, tmp_path):
    import market_intelligence.market as mil_market

    monkeypatch.setattr(mil_market, "get_market_context",
                         lambda ctx, phase: {"kospi": 2480.0, "foreign_net_buy_krw": -450_000_000_000})
    monkeypatch.setattr(mil_market, "get_intraday_index_candles",
                         lambda ctx, phase: {"candles": [
                             {"open": 2530.0, "high": 2531.0, "low": 2525.0, "close": 2528.0},
                             {"open": 2528.0, "high": 2529.0, "low": 2470.0, "close": 2480.0},
                         ]})
    monkeypatch.setattr(mil_market, "get_sector_breadth",
                         lambda ctx, phase: {"market_breadth": {"advancers": 150, "decliners": 550}})

    orch = make_orchestrator(tmp_path)
    snapshot = orch._collect_drift_snapshot()

    assert snapshot["kospi_current"] == 2480.0
    assert snapshot["kospi_open"] == 2530.0
    assert snapshot["kospi_low"] == 2470.0
    assert snapshot["foreign_net_buy_bln"] == -4500.0
    assert snapshot["advance_count"] == 150
    assert snapshot["decline_count"] == 550


# ── run_intraday_v3 ──────────────────────────────────────────────────────────

class FakeDriftDetector:
    def __init__(self, response):
        self._response = response

    def check(self, **kwargs):
        return self._response


class FakeTradingAgent:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def run(self, phase, context):
        self.calls.append((phase, context))
        return self._response


def test_run_scan_v3_backfills_watchlist_when_agent_returns_empty(monkeypatch, tmp_path):
    import market_intelligence.portfolio as mil_portfolio
    import market_intelligence.screening as mil_screening
    import market_intelligence.risk_filter as mil_risk_filter
    import market_intelligence.theme as mil_theme

    monkeypatch.setattr(mil_portfolio, "get_open_positions",
                         lambda ctx, phase: {"positions": [], "position_count": 0})
    monkeypatch.setattr(mil_portfolio, "get_daily_pnl",
                         lambda ctx, phase: {"realized_pnl_pct": 0.0, "realized_pnl_krw": 0, "total_eval_amt": 10_000_000})
    monkeypatch.setattr(mil_theme, "get_theme_candidates",
                         lambda ctx, phase: {
                             "candidates": [
                                 {"ticker": "357780", "name": "솔브레인", "change_pct": 27.89, "trading_value": 128_000_000_000, "theme_name": "반도체"},
                             ]
                         })
    monkeypatch.setattr(mil_screening, "get_top_movers",
                         lambda ctx, phase: {
                             "change_rate_top": [
                                 {"ticker": "357780", "name": "솔브레인", "change_pct": 27.89, "trading_value_krw": 128_000_000_000},
                                 {"ticker": "403870", "name": "HPSP", "change_pct": 30.0, "trading_value_krw": 1_102_000_000_000},
                             ],
                             "overheated_bias_warning": True,
                         })
    monkeypatch.setattr(mil_risk_filter, "get_stock_status",
                         lambda ctx, phase, ticker: {
                             "ticker": ticker,
                             "trading_halted": False,
                             "administrative_issue": False,
                             "is_limit_up": ticker == "403870",
                         })

    orch = make_orchestrator(tmp_path)
    orch._trading_agent = FakeTradingAgent({
        "next_action": "final",
        "action": "WATCHLIST_UPDATE",
        "watchlist": [],
        "candidates": [],
        "reason": "llm empty",
    })

    regime = {
        "status": "YELLOW", "regime": "SIDEWAYS", "confidence": 50,
        "risk_guidance": {"max_positions": 2, "buy_confidence_threshold": 75,
                           "risk_per_trade_pct": 0.35, "min_trading_value_krw": 12_000_000_000},
        "timestamp": "2026-06-09T08:45:00",
    }
    (tmp_path / "last_regime.json").write_text(json.dumps(regime), encoding="utf-8")

    monkeypatch.setattr("orchestrator_v3._LAST_REGIME_PATH", tmp_path / "last_regime.json")
    monkeypatch.setattr("orchestrator_v3._DRIFT_STATE_PATH", tmp_path / "drift_state.json")
    monkeypatch.setattr("orchestrator_v3._WATCHLIST_PATH", tmp_path / "watchlist.json")

    result = orch.run_scan_v3()

    assert result["watchlist"] == ["357780"]
    assert result["overheated_bias_warning"] is True
    assert "orchestrator_scan_backfill" in result["reason"]
    assert load_watchlist(path=tmp_path / "watchlist.json") == ["357780"]


def test_run_intraday_v3_stable_executes_no_trade(monkeypatch, tmp_path):
    import market_intelligence.portfolio as mil_portfolio
    import market_intelligence.market as mil_market

    monkeypatch.setattr(mil_portfolio, "get_open_positions",
                         lambda ctx, phase: {"positions": [], "position_count": 0})
    monkeypatch.setattr(mil_portfolio, "get_daily_pnl",
                         lambda ctx, phase: {"realized_pnl_pct": 0.0, "realized_pnl_krw": 0, "total_eval_amt": 10_000_000})
    monkeypatch.setattr(mil_market, "get_market_context",
                         lambda ctx, phase: {"kospi": 2520.0, "foreign_net_buy_krw": 0})
    monkeypatch.setattr(mil_market, "get_intraday_index_candles",
                         lambda ctx, phase: {"candles": [{"open": 2525.0, "high": 2526.0, "low": 2515.0, "close": 2520.0}]})
    monkeypatch.setattr(mil_market, "get_sector_breadth",
                         lambda ctx, phase: {"market_breadth": {"advancers": 400, "decliners": 300}})

    orch = make_orchestrator(tmp_path)
    orch._drift_detector = FakeDriftDetector({
        "drift_judgment": "STABLE", "reason": "no_trigger_fired", "metrics": {}, "triggered": [],
        "new_status": None, "risk_guidance_delta": {},
        "drift_state": {"date": "2026-06-09", "last_trigger_time": {}, "today_caution_count": 0, "daily_lite_llm_calls": 0},
    })
    orch._trading_agent = FakeTradingAgent({"action": "NO_TRADE", "proposals": [], "reason": "조건 미충족"})

    regime = {
        "status": "YELLOW", "regime": "SIDEWAYS", "confidence": 50,
        "risk_guidance": {"max_positions": 4, "buy_confidence_threshold": 75,
                           "risk_per_trade_pct": 0.35, "min_trading_value_krw": 1_000_000_000},
        "drift_triggers": [], "cooldown_minutes": 60, "max_daily_triggers": 3,
        "timestamp": "2026-06-09T08:45:00",
    }
    (tmp_path / "last_regime.json").write_text(json.dumps(regime), encoding="utf-8")
    (tmp_path / "watchlist.json").write_text(json.dumps({"watchlist": ["005930"]}), encoding="utf-8")

    monkeypatch.setattr("orchestrator_v3._LAST_REGIME_PATH", tmp_path / "last_regime.json")
    monkeypatch.setattr("orchestrator_v3._DRIFT_STATE_PATH", tmp_path / "drift_state.json")
    monkeypatch.setattr("orchestrator_v3._WATCHLIST_PATH", tmp_path / "watchlist.json")

    result = orch.run_intraday_v3()

    assert result["action"] == "NO_TRADE"
    assert orch._trading_agent.calls[0][0] == TradingPhase.INTRADAY
    assert orch._trading_agent.calls[0][1]["watchlist"] == ["005930"]
    saved_drift = json.loads((tmp_path / "drift_state.json").read_text(encoding="utf-8"))
    assert saved_drift["today_caution_count"] == 0


def test_run_intraday_v3_skips_on_stale_regime(monkeypatch, tmp_path):
    orch = make_orchestrator(tmp_path)  # _today = "2026-06-09"
    regime = {"status": "YELLOW", "regime": "SIDEWAYS", "confidence": 50,
              "risk_guidance": {}, "drift_triggers": [],
              "timestamp": "2026-06-08T09:03:00"}  # 전일 레짐
    import json as _json
    (tmp_path / "last_regime.json").write_text(_json.dumps(regime), encoding="utf-8")
    monkeypatch.setattr("orchestrator_v3._LAST_REGIME_PATH", tmp_path / "last_regime.json")

    result = orch.run_intraday_v3()

    assert result == {"action": "NO_TRADE", "reason": "stale_regime"}


def test_run_intraday_v3_idle_skip_gate(monkeypatch, tmp_path):
    """watchlist 0 + 보유 0 + STABLE이면 LLM을 호출하지 않는다 (비용 게이트)."""
    import market_intelligence.market as mil_market

    monkeypatch.setattr(mil_market, "get_market_context",
                         lambda ctx, phase: {"kospi": 2520.0, "foreign_net_buy_krw": 0})
    monkeypatch.setattr(mil_market, "get_intraday_index_candles",
                         lambda ctx, phase: {"candles": [{"open": 2525.0, "high": 2526.0, "low": 2515.0, "close": 2520.0}]})
    monkeypatch.setattr(mil_market, "get_sector_breadth",
                         lambda ctx, phase: {"market_breadth": {"advancers": 400, "decliners": 300}})

    orch = make_orchestrator(tmp_path)
    orch._drift_detector = FakeDriftDetector({
        "drift_judgment": "STABLE", "reason": "no_trigger_fired", "metrics": {}, "triggered": [],
        "new_status": None, "risk_guidance_delta": {},
        "drift_state": {"date": "2026-06-09", "last_trigger_time": {}, "today_caution_count": 0, "daily_lite_llm_calls": 0},
    })
    orch._trading_agent = FakeTradingAgent({"action": "NO_TRADE", "proposals": []})
    orch._journal = type("J", (), {"get_open_positions": staticmethod(lambda: [])})()

    regime = {"status": "YELLOW", "regime": "SIDEWAYS", "confidence": 50,
              "risk_guidance": {}, "drift_triggers": [], "cooldown_minutes": 60,
              "max_daily_triggers": 3, "timestamp": "2026-06-09T09:03:00"}
    import json as _json
    (tmp_path / "last_regime.json").write_text(_json.dumps(regime), encoding="utf-8")
    (tmp_path / "watchlist.json").write_text(_json.dumps({"watchlist": []}), encoding="utf-8")
    monkeypatch.setattr("orchestrator_v3._LAST_REGIME_PATH", tmp_path / "last_regime.json")
    monkeypatch.setattr("orchestrator_v3._DRIFT_STATE_PATH", tmp_path / "drift_state.json")
    monkeypatch.setattr("orchestrator_v3._WATCHLIST_PATH", tmp_path / "watchlist.json")

    result = orch.run_intraday_v3()

    assert result == {"action": "NO_TRADE", "reason": "idle_skip"}
    assert orch._trading_agent.calls == []  # LLM 미호출

    # 보유 포지션이 있으면 게이트가 열리지 않는다
    import market_intelligence.portfolio as mil_portfolio
    monkeypatch.setattr(mil_portfolio, "get_open_positions",
                         lambda ctx, phase: {"positions": [], "position_count": 0})
    monkeypatch.setattr(mil_portfolio, "get_daily_pnl",
                         lambda ctx, phase: {"realized_pnl_pct": 0.0, "realized_pnl_krw": 0, "total_eval_amt": 1})
    orch._journal = type("J", (), {"get_open_positions": staticmethod(lambda: [{"ticker": "095340"}])})()
    result2 = orch.run_intraday_v3()
    assert orch._trading_agent.calls  # 보유 있음 → LLM 호출됨


def test_run_intraday_v3_regime_shift_updates_status_and_rescans(monkeypatch, tmp_path):
    import market_intelligence.portfolio as mil_portfolio
    import market_intelligence.market as mil_market

    monkeypatch.setattr(mil_portfolio, "get_open_positions",
                         lambda ctx, phase: {"positions": [], "position_count": 0})
    monkeypatch.setattr(mil_portfolio, "get_daily_pnl",
                         lambda ctx, phase: {"realized_pnl_pct": 0.0, "realized_pnl_krw": 0, "total_eval_amt": 10_000_000})
    monkeypatch.setattr(mil_market, "get_market_context",
                         lambda ctx, phase: {"kospi": 2470.0, "foreign_net_buy_krw": -500_000_000_000})
    monkeypatch.setattr(mil_market, "get_intraday_index_candles",
                         lambda ctx, phase: {"candles": [{"open": 2530.0, "high": 2531.0, "low": 2465.0, "close": 2470.0}]})
    monkeypatch.setattr(mil_market, "get_sector_breadth",
                         lambda ctx, phase: {"market_breadth": {"advancers": 100, "decliners": 600}})

    orch = make_orchestrator(tmp_path)
    orch._drift_detector = FakeDriftDetector({
        "drift_judgment": "REGIME_SHIFT", "reason": "지수 급락 + 외인 대량매도", "metrics": {}, "triggered": [],
        "new_status": "RED", "risk_guidance_delta": {"buy_confidence_threshold": 88, "risk_per_trade_pct": 0.15, "max_positions": 2},
        "drift_state": {"date": "2026-06-09", "last_trigger_time": {}, "today_caution_count": 3, "daily_lite_llm_calls": 1},
    })
    orch._trading_agent = FakeTradingAgent({"action": "NO_TRADE", "proposals": [], "reason": "관망"})
    rescan_calls = []
    monkeypatch.setattr(orch, "run_scan_v3", lambda: rescan_calls.append(1))

    regime = {
        "status": "YELLOW", "regime": "SIDEWAYS", "confidence": 50,
        "risk_guidance": {"max_positions": 4, "buy_confidence_threshold": 75,
                           "risk_per_trade_pct": 0.35, "min_trading_value_krw": 1_000_000_000},
        "drift_triggers": [], "cooldown_minutes": 60, "max_daily_triggers": 3,
        "timestamp": "2026-06-09T08:45:00",
    }
    last_regime_path = tmp_path / "last_regime.json"
    last_regime_path.write_text(json.dumps(regime), encoding="utf-8")
    (tmp_path / "watchlist.json").write_text(json.dumps({"watchlist": []}), encoding="utf-8")

    monkeypatch.setattr("orchestrator_v3._LAST_REGIME_PATH", last_regime_path)
    monkeypatch.setattr("orchestrator_v3._DRIFT_STATE_PATH", tmp_path / "drift_state.json")
    monkeypatch.setattr("orchestrator_v3._WATCHLIST_PATH", tmp_path / "watchlist.json")

    orch.run_intraday_v3()

    assert rescan_calls == [1]
    updated = json.loads(last_regime_path.read_text(encoding="utf-8"))
    assert updated["status"] == "RED"
    assert updated["risk_guidance"]["max_positions"] == 2


# ── run_late_intraday_v3 (폭락일 전용) ──────────────────────────────────────

def _write_today_regime(tmp_path, monkeypatch, status="YELLOW"):
    import json as _json
    regime = {"status": status, "regime": "SIDEWAYS", "confidence": 50,
              "risk_guidance": {"max_positions": 4}, "drift_triggers": [],
              "timestamp": "2026-06-09T09:03:00"}
    (tmp_path / "last_regime.json").write_text(_json.dumps(regime), encoding="utf-8")
    monkeypatch.setattr("orchestrator_v3._LAST_REGIME_PATH", tmp_path / "last_regime.json")
    monkeypatch.setattr("orchestrator_v3._DRIFT_STATE_PATH", tmp_path / "drift_state.json")
    monkeypatch.setattr("orchestrator_v3._WATCHLIST_PATH", tmp_path / "watchlist.json")


def test_late_intraday_skips_without_crash_gate(monkeypatch, tmp_path):
    import market_intelligence.market as mil_market

    monkeypatch.setattr(mil_market, "get_market_context",
                         lambda ctx, phase: {"kospi_change_pct": -1.2, "kosdaq_change_pct": -2.0})

    orch = make_orchestrator(tmp_path)
    orch._trading_agent = FakeTradingAgent({"action": "BUY", "proposals": []})
    _write_today_regime(tmp_path, monkeypatch, status="YELLOW")

    result = orch.run_late_intraday_v3()

    assert result == {"action": "NO_TRADE", "reason": "no_crash_gate"}
    assert orch._trading_agent.calls == []  # LLM 미호출


def test_late_intraday_runs_agent_on_crash(monkeypatch, tmp_path):
    import market_intelligence.market as mil_market
    import market_intelligence.portfolio as mil_portfolio

    monkeypatch.setattr(mil_market, "get_market_context",
                         lambda ctx, phase: {"kospi_change_pct": -3.5, "kosdaq_change_pct": -5.1})
    monkeypatch.setattr(mil_portfolio, "get_open_positions",
                         lambda ctx, phase: {"positions": [], "position_count": 0})
    monkeypatch.setattr(mil_portfolio, "get_daily_pnl",
                         lambda ctx, phase: {"realized_pnl_pct": 0.0, "realized_pnl_krw": 0, "total_eval_amt": 1})

    orch = make_orchestrator(tmp_path)
    orch._trading_agent = FakeTradingAgent({"action": "NO_TRADE", "proposals": [], "reason": "후보 없음"})
    _write_today_regime(tmp_path, monkeypatch, status="YELLOW")

    result = orch.run_late_intraday_v3()

    assert result["action"] == "NO_TRADE"
    assert orch._trading_agent.calls[0][0] == TradingPhase.LATE_INTRADAY
    allowed = orch._trading_agent.calls[0][1]["allowed_tools"]
    assert "psearch_result" in allowed and "get_top_movers" in allowed


def test_late_intraday_runs_on_red_regime_without_index_crash(monkeypatch, tmp_path):
    import market_intelligence.market as mil_market
    import market_intelligence.portfolio as mil_portfolio

    monkeypatch.setattr(mil_market, "get_market_context",
                         lambda ctx, phase: {"kospi_change_pct": -1.0, "kosdaq_change_pct": -1.5})
    monkeypatch.setattr(mil_portfolio, "get_open_positions",
                         lambda ctx, phase: {"positions": [], "position_count": 0})
    monkeypatch.setattr(mil_portfolio, "get_daily_pnl",
                         lambda ctx, phase: {"realized_pnl_pct": 0.0, "realized_pnl_krw": 0, "total_eval_amt": 1})

    orch = make_orchestrator(tmp_path)
    orch._trading_agent = FakeTradingAgent({"action": "NO_TRADE", "proposals": []})
    _write_today_regime(tmp_path, monkeypatch, status="RED")

    result = orch.run_late_intraday_v3()
    assert orch._trading_agent.calls  # RED면 지수 폭락 없어도 게이트 통과


def test_late_intraday_skips_when_gate_data_unavailable(monkeypatch, tmp_path):
    import market_intelligence.market as mil_market
    from market_intelligence.base import ToolFailure

    def boom(ctx, phase):
        raise ToolFailure("get_market_context: 500")

    monkeypatch.setattr(mil_market, "get_market_context", boom)

    orch = make_orchestrator(tmp_path)
    orch._trading_agent = FakeTradingAgent({"action": "BUY", "proposals": []})
    _write_today_regime(tmp_path, monkeypatch, status="RED")

    result = orch.run_late_intraday_v3()

    assert result == {"action": "NO_TRADE", "reason": "gate_data_unavailable"}
    assert orch._trading_agent.calls == []


# ── BUY proposal → Safety Layer ──────────────────────────────────────────────

class FakeOrderManager:
    def __init__(self):
        self.buy_calls = []

    def execute_buy(self, order):
        self.buy_calls.append(order)
        from codes.order_manager import ExecutionResult
        return ExecutionResult(
            success=True, ticker=order.ticker, side="BUY", quantity=order.quantity,
            executed_price=order.price, order_no="ORD1", timestamp="2026-06-09T09:30:00",
        )


class FakeRiskOfficer:
    def __init__(self, raise_violation=False):
        self._raise = raise_violation
        self.checked = []

    def check(self, proposal, portfolio_state):
        self.checked.append(proposal)
        if self._raise:
            raise RiskViolation(rule="MAX_POSITIONS", detail="포지션 한도 초과")


class FakePositionSizer:
    def calculate_flexible_stop(self, ticker, entry_price, atr, total_capital, support_stop_price=None, risk_pct_override=None):
        from codes.position_sizer import SizingResult
        stop_loss_price = support_stop_price or entry_price * 0.95
        quantity = 10
        return SizingResult(
            ticker=ticker, entry_price=entry_price, stop_loss_price=stop_loss_price,
            quantity=quantity, risk_amount=(entry_price - stop_loss_price) * quantity,
            risk_pct=0.3, position_value=entry_price * quantity, atr_used=atr,
            stop_method="support",
        )


class FakeTelegramApproval:
    def __init__(self, approved=True):
        self._approved = approved
        self.requests = []

    def request_approval(self, req):
        self.requests.append(req)
        from broker.telegram import ApprovalResult
        return ApprovalResult(approved=self._approved, request_id="REQ1")


def test_process_v3_buy_proposal_executes_order_when_approved(tmp_path, monkeypatch):
    orch = make_orchestrator(tmp_path)
    orch._risk_officer = FakeRiskOfficer()
    orch._position_sizer = FakePositionSizer()
    orch._telegram = FakeTelegramApproval(approved=True)
    orch._order_manager = FakeOrderManager()
    monkeypatch.setattr(orch, "build_portfolio_state", lambda: object())
    monkeypatch.setattr(orch, "_estimate_atr", lambda ticker: 1500.0)

    class FakeSnapshot:
        current_price = 70000.0

    monkeypatch.setattr(orch, "_market_data", type("MD", (), {"get_snapshot": staticmethod(lambda t: FakeSnapshot())})())

    proposal = {"ticker": "005930", "side": "BUY", "confidence": 82, "stop_loss_price": 68000, "reason": "강한 회복"}
    result = orch._process_v3_buy_proposal(proposal)

    assert result["action"] == "BUY_EXECUTED"
    assert orch._order_manager.buy_calls[0].ticker == "005930"


# ── Critical 1/2: malformed proposals must not crash _handle_proposals ─────

def test_handle_proposals_skips_malformed_and_executes_valid(tmp_path, monkeypatch):
    orch = make_orchestrator(tmp_path)
    orch._risk_officer = FakeRiskOfficer()
    orch._position_sizer = FakePositionSizer()
    orch._telegram = FakeTelegramApproval(approved=True)
    orch._order_manager = FakeOrderManager()
    monkeypatch.setattr(orch, "build_portfolio_state", lambda: object())
    monkeypatch.setattr(orch, "_estimate_atr", lambda ticker: 1500.0)

    class FakeSnapshot:
        current_price = 70000.0

    monkeypatch.setattr(orch, "_market_data", type("MD", (), {"get_snapshot": staticmethod(lambda t: FakeSnapshot())})())

    proposals = [
        "not_a_dict",
        {"ticker": "000660", "side": "BUY", "confidence": 80, "reason": "누락"},  # missing stop_loss_price
        {"ticker": "005930", "side": "BUY", "confidence": 82, "stop_loss_price": 68000, "reason": "정상"},
    ]

    results = orch._handle_proposals(proposals)

    assert results[0]["action"] == "SKIP"
    assert results[0]["reason"] == "malformed_proposal"
    assert results[1]["action"] == "SKIP"
    assert results[1]["reason"] == "malformed_proposal"
    assert results[2]["action"] == "BUY_EXECUTED"
    assert orch._order_manager.buy_calls[0].ticker == "005930"


def test_handle_sell_proposals_skips_malformed(tmp_path, monkeypatch):
    orch = make_orchestrator(tmp_path)
    orch._journal = type("J", (), {"get_open_positions": staticmethod(lambda: [])})()

    proposals = ["not_a_dict", {"reason": "no ticker key"}]
    results = orch._handle_sell_proposals(proposals)

    assert all(r["action"] == "SKIP" and r["reason"] == "malformed_proposal" for r in results)


# ── Critical 2: non-numeric stop_loss_price coercion ────────────────────────

def test_process_v3_buy_proposal_coerces_string_stop_loss_price(tmp_path, monkeypatch):
    orch = make_orchestrator(tmp_path)
    orch._risk_officer = FakeRiskOfficer()
    orch._position_sizer = FakePositionSizer()
    orch._telegram = FakeTelegramApproval(approved=True)
    orch._order_manager = FakeOrderManager()
    monkeypatch.setattr(orch, "build_portfolio_state", lambda: object())
    monkeypatch.setattr(orch, "_estimate_atr", lambda ticker: 1500.0)

    class FakeSnapshot:
        current_price = 70000.0

    monkeypatch.setattr(orch, "_market_data", type("MD", (), {"get_snapshot": staticmethod(lambda t: FakeSnapshot())})())

    proposal = {"ticker": "005930", "side": "BUY", "confidence": 82, "stop_loss_price": "68000", "reason": "강한 회복"}
    result = orch._process_v3_buy_proposal(proposal)

    assert result["action"] == "BUY_EXECUTED"


def test_handle_proposals_skips_non_numeric_stop_loss_price(tmp_path, monkeypatch):
    orch = make_orchestrator(tmp_path)
    orch._risk_officer = FakeRiskOfficer()
    orch._position_sizer = FakePositionSizer()
    orch._telegram = FakeTelegramApproval(approved=True)
    orch._order_manager = FakeOrderManager()
    monkeypatch.setattr(orch, "build_portfolio_state", lambda: object())
    monkeypatch.setattr(orch, "_estimate_atr", lambda ticker: 1500.0)

    class FakeSnapshot:
        current_price = 70000.0

    monkeypatch.setattr(orch, "_market_data", type("MD", (), {"get_snapshot": staticmethod(lambda t: FakeSnapshot())})())

    proposals = [{"ticker": "005930", "side": "BUY", "confidence": 82, "stop_loss_price": "abc", "reason": "이상값"}]
    results = orch._handle_proposals(proposals)

    assert results[0]["action"] == "SKIP"
    assert results[0]["reason"] == "malformed_proposal"


def test_process_v3_buy_proposal_blocked_by_risk_officer(tmp_path, monkeypatch):
    orch = make_orchestrator(tmp_path)
    orch._risk_officer = FakeRiskOfficer(raise_violation=True)
    orch._position_sizer = FakePositionSizer()
    orch._telegram = FakeTelegramApproval(approved=True)
    orch._order_manager = FakeOrderManager()
    monkeypatch.setattr(orch, "build_portfolio_state", lambda: object())
    monkeypatch.setattr(orch, "_estimate_atr", lambda ticker: 1500.0)

    class FakeSnapshot:
        current_price = 70000.0

    monkeypatch.setattr(orch, "_market_data", type("MD", (), {"get_snapshot": staticmethod(lambda t: FakeSnapshot())})())

    proposal = {"ticker": "005930", "side": "BUY", "confidence": 82, "stop_loss_price": 68000, "reason": "강한 회복"}
    result = orch._process_v3_buy_proposal(proposal)

    assert result["action"] == "BLOCKED"
    assert orch._order_manager.buy_calls == []


# ── 매수가능현금 가드 (insufficient_cash) ───────────────────────────────────

class FakeKISApiBuyable:
    def __init__(self, buyable_cash_krw):
        self._buyable_cash_krw = buyable_cash_krw
        self.calls = []

    def get_buyable_cash(self, ticker="", price=0):
        self.calls.append((ticker, price))
        return {"buyable_cash_krw": self._buyable_cash_krw, "max_buy_qty": 999}


class FakeKISApiBuyableError:
    def get_buyable_cash(self, ticker="", price=0):
        raise RuntimeError("network error")


def _setup_buy_proposal_orch(tmp_path, monkeypatch):
    orch = make_orchestrator(tmp_path)
    orch._risk_officer = FakeRiskOfficer()
    orch._position_sizer = FakePositionSizer()
    orch._telegram = FakeTelegramApproval(approved=True)
    orch._order_manager = FakeOrderManager()
    monkeypatch.setattr(orch, "build_portfolio_state", lambda: object())
    monkeypatch.setattr(orch, "_estimate_atr", lambda ticker: 1500.0)

    class FakeSnapshot:
        current_price = 70000.0

    monkeypatch.setattr(orch, "_market_data", type("MD", (), {"get_snapshot": staticmethod(lambda t: FakeSnapshot())})())
    return orch


def test_process_v3_buy_proposal_blocked_when_buyable_cash_insufficient(tmp_path, monkeypatch):
    orch = _setup_buy_proposal_orch(tmp_path, monkeypatch)
    # FakePositionSizer: quantity=10, entry_price=70000 → order value 700,000
    orch._kis_api = FakeKISApiBuyable(buyable_cash_krw=500_000)

    proposal = {"ticker": "005930", "side": "BUY", "confidence": 82, "stop_loss_price": 68000, "reason": "강한 회복"}
    result = orch._process_v3_buy_proposal(proposal)

    assert result["action"] == "BLOCKED"
    assert result["reason"] == "insufficient_cash"
    assert orch._order_manager.buy_calls == []


def test_process_v3_buy_proposal_proceeds_when_buyable_cash_ample(tmp_path, monkeypatch):
    orch = _setup_buy_proposal_orch(tmp_path, monkeypatch)
    orch._kis_api = FakeKISApiBuyable(buyable_cash_krw=10_000_000)

    proposal = {"ticker": "005930", "side": "BUY", "confidence": 82, "stop_loss_price": 68000, "reason": "강한 회복"}
    result = orch._process_v3_buy_proposal(proposal)

    assert result["action"] == "BUY_EXECUTED"
    assert orch._order_manager.buy_calls[0].ticker == "005930"


def test_process_v3_buy_proposal_proceeds_when_buyable_cash_check_fails(tmp_path, monkeypatch):
    orch = _setup_buy_proposal_orch(tmp_path, monkeypatch)
    orch._kis_api = FakeKISApiBuyableError()

    proposal = {"ticker": "005930", "side": "BUY", "confidence": 82, "stop_loss_price": 68000, "reason": "강한 회복"}
    result = orch._process_v3_buy_proposal(proposal)

    assert result["action"] == "BUY_EXECUTED"


def test_process_v3_buy_proposal_proceeds_when_kis_api_attribute_absent(tmp_path, monkeypatch):
    orch = _setup_buy_proposal_orch(tmp_path, monkeypatch)
    # orch._kis_api intentionally not set (mirrors make_orchestrator default)

    proposal = {"ticker": "005930", "side": "BUY", "confidence": 82, "stop_loss_price": 68000, "reason": "강한 회복"}
    result = orch._process_v3_buy_proposal(proposal)

    assert result["action"] == "BUY_EXECUTED"
