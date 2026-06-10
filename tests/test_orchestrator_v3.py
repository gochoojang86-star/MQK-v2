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
                         lambda ctx, phase: {"sectors": [
                             {"advancers": 100, "decliners": 300},
                             {"advancers": 50, "decliners": 250},
                         ]})

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
                         lambda ctx, phase: {"sectors": [{"advancers": 400, "decliners": 300}]})

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
                         lambda ctx, phase: {"sectors": [{"advancers": 100, "decliners": 600}]})

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
