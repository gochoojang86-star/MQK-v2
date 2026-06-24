"""OrchestratorV3 테스트 - drift_state/watchlist 영속화 + Phase 오케스트레이션"""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from agents.trading_agent import TradingPhase
from codes.risk_officer import RiskViolation
from market_intelligence.circuit_breaker import CircuitBreaker
from orchestrator_v3 import (
    MQKOrchestratorV3,
    _resolve_regime_evaluation_mode,
    load_drift_state,
    load_next_day_premarket_context,
    load_watchlist_entries,
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


def test_save_and_load_watchlist_filters_invalid_and_duplicate_tickers(tmp_path):
    path = tmp_path / "watchlist.json"
    save_watchlist(["005930", "0054V0", "000660", "005930", " 0041B0 ", "357780"], path=path)

    assert load_watchlist(path=path) == ["005930", "000660", "357780"]


def test_save_and_load_watchlist_preserves_metadata_entries(tmp_path):
    path = tmp_path / "watchlist.json"
    save_watchlist(
        [
            {"ticker": "005930", "setup": "TREND", "confidence": 81, "reason": "leader", "cluster": "memory_core"},
            {
                "ticker": "000660",
                "setup": "D_DAY",
                "confidence": 77,
                "reason": "event",
                "cluster": "memory_core",
                "d_day": "2026-07-10",
            },
        ],
        path=path,
    )

    assert load_watchlist(path=path) == ["005930", "000660"]
    assert load_watchlist_entries(path=path) == [
        {"ticker": "005930", "setup": "TREND", "confidence": 81, "reason": "leader", "cluster": "memory_core"},
        {
            "ticker": "000660",
            "setup": "D_DAY",
            "confidence": 77,
            "reason": "event",
            "cluster": "memory_core",
            "d_day": "2026-07-10",
        },
    ]


def test_watchlist_entries_from_scan_result_preserves_cluster(tmp_path):
    orch = make_orchestrator(tmp_path)

    result = {
        "watchlist": ["005930", "000660"],
        "candidates": [
            {"ticker": "005930", "setup": "TREND", "confidence": 81, "reason": "leader", "cluster": "memory_core"},
            {
                "ticker": "000660",
                "setup": "D_DAY",
                "confidence": 77,
                "reason": "event",
                "cluster": "memory_core",
                "d_day": "2026-07-10",
            },
        ],
    }

    assert orch._watchlist_entries_from_scan_result(result) == [
        {"ticker": "005930", "setup": "TREND", "confidence": 81, "reason": "leader", "cluster": "memory_core"},
        {
            "ticker": "000660",
            "setup": "D_DAY",
            "confidence": 77,
            "reason": "event",
            "cluster": "memory_core",
            "d_day": "2026-07-10",
        },
    ]


def test_resolve_regime_evaluation_mode_by_time():
    from datetime import datetime

    assert _resolve_regime_evaluation_mode(datetime(2026, 6, 19, 9, 3)) == "OPENING"
    assert _resolve_regime_evaluation_mode(datetime(2026, 6, 19, 11, 3)) == "MIDDAY"
    assert _resolve_regime_evaluation_mode(datetime(2026, 6, 19, 13, 3)) == "AFTERNOON"


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
    orch._mil = type("_MIL", (), {})()
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
    orch._kis_api = type(
        "_KIS",
        (),
        {
            "get_balance": lambda self: {
                "output1": [{"pdno": "005930", "hldg_qty": "1", "prpr": "71000"}],
                "output2": [{"tot_evlu_amt": "10000000", "ord_psbl_cash": "3000000"}],
            }
        },
    )()
    regime = {"status": "YELLOW", "regime": "SIDEWAYS", "confidence": 50,
              "risk_guidance": {"max_positions": 4, "buy_confidence_threshold": 75,
                                 "risk_per_trade_pct": 0.35, "min_trading_value_krw": 1_000_000_000},
              "timestamp": "2026-06-09T08:45:00"}

    ctx = orch._build_context(TradingPhase.INTRADAY, regime, "STABLE", watchlist=["005930"])

    assert ctx["portfolio"]["position_count"] == 1
    assert ctx["portfolio"]["available_cash_krw"] == 3_000_000
    assert ctx["portfolio"]["cash_ratio_pct"] == 30.0
    assert ctx["portfolio"]["positions_left_is_soft"] is True
    assert ctx["risk_budget_remaining"]["positions_left"] == 3
    assert ctx["daily_pnl"]["realized_pnl_pct"] == -0.5
    assert ctx["watchlist"] == ["005930"]
    assert ctx["watchlist_tickers"] == ["005930"]
    assert ctx["allowed_tools"] == [
        "get_market_context", "get_sector_breadth", "get_theme_candidates",
        "psearch_title", "psearch_result",
        "kw_psearch_title", "kw_psearch_result",
        "get_top_movers", "get_attention_rank",
        "get_foreign_institution_rank", "get_sector_investor_flow",
        "get_volume_surge", "get_bid_queue_surge", "get_intraday_investor_rank",
        "get_ohlcv", "get_realtime_price", "get_watchlist_intraday_snapshot", "get_intraday_candles",
        "get_flow", "get_intraday_institutional_flow", "get_news_stock", "get_stock_status",
        "get_orderbook",
        "get_intraday_volume_trend",
    ]


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


def test_build_context_retries_portfolio_fetch_before_degrading(monkeypatch, tmp_path):
    """2026-06-15: 잔고 조회가 일시적으로 실패해도 3회 내 성공하면 정상 컨텍스트를 사용한다."""
    import market_intelligence.portfolio as mil_portfolio
    from market_intelligence.base import ToolFailure

    calls = {"n": 0}

    def flaky_positions(ctx, phase):
        calls["n"] += 1
        if calls["n"] < 2:
            raise ToolFailure("get_open_positions: 500 Server Error")
        return {"positions": [{"ticker": "005930"}], "position_count": 1}

    monkeypatch.setattr(mil_portfolio, "get_open_positions", flaky_positions)
    monkeypatch.setattr(mil_portfolio, "get_daily_pnl",
                         lambda ctx, phase: {"realized_pnl_pct": 0.0, "realized_pnl_krw": 0,
                                              "total_eval_amt": 10_000_000})
    monkeypatch.setattr("orchestrator_v3.time.sleep", lambda _: None)

    orch = make_orchestrator(tmp_path)
    orch._mil.circuit_breaker = CircuitBreaker()
    regime = {"status": "YELLOW", "regime": "SIDEWAYS", "confidence": 50,
              "risk_guidance": {"max_positions": 4}, "timestamp": "2026-06-12T08:45:00"}

    ctx = orch._build_context(TradingPhase.INTRADAY, regime, "STABLE", watchlist=[])

    assert calls["n"] == 2
    assert ctx["portfolio"].get("data_unavailable") is None
    assert ctx["risk_budget_remaining"]["positions_left"] == 3


def test_build_context_falls_back_to_last_snapshot_after_retries_exhausted(monkeypatch, tmp_path):
    """2026-06-15: 3회 재시도 후에도 실패하면 직전 성공 스냅샷을(stale) 재사용해
    실제 보유 현황 기준으로 매수 예산을 계산한다 (예산 0으로 잘못 강등하지 않음)."""
    import market_intelligence.portfolio as mil_portfolio
    from market_intelligence.base import ToolFailure

    monkeypatch.setattr("orchestrator_v3.time.sleep", lambda _: None)

    orch = make_orchestrator(tmp_path)
    orch._mil.circuit_breaker = CircuitBreaker()
    orch._last_portfolio_snapshot = (
        {"positions": [{"ticker": "005930"}, {"ticker": "095340"}], "position_count": 2},
        {"realized_pnl_pct": -1.0, "realized_pnl_krw": -50000, "total_eval_amt": 48_000_000},
    )

    def always_fails(ctx, phase):
        raise ToolFailure("get_open_positions: Read timed out")

    monkeypatch.setattr(mil_portfolio, "get_open_positions", always_fails)
    monkeypatch.setattr(mil_portfolio, "get_daily_pnl", always_fails)

    regime = {"status": "GREEN", "regime": "UPTREND", "confidence": 91,
              "risk_guidance": {"max_positions": 4}, "timestamp": "2026-06-15T08:45:00"}

    ctx = orch._build_context(TradingPhase.INTRADAY, regime, "STABLE", watchlist=[])

    assert ctx["portfolio"]["data_unavailable"] is True
    assert ctx["portfolio"]["stale"] is True
    assert ctx["portfolio"]["position_count"] == 2
    # 4 - 2 = 2개 신규 진입 여력 — 0으로 잘못 강등되지 않는다.
    assert ctx["risk_budget_remaining"]["positions_left"] == 2
    assert ctx["risk_budget_remaining"]["daily_loss_remaining_pct"] > 0.0


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


def test_run_scan_v3_keeps_monitoring_watchlist_when_positions_full(monkeypatch, tmp_path):
    import market_intelligence.portfolio as mil_portfolio
    import market_intelligence.screening as mil_screening
    import market_intelligence.risk_filter as mil_risk_filter
    import market_intelligence.theme as mil_theme

    monkeypatch.setattr(mil_portfolio, "get_open_positions",
                         lambda ctx, phase: {"positions": [{"ticker": "005930"}, {"ticker": "000660"}], "position_count": 2})
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
                             ],
                             "overheated_bias_warning": False,
                         })
    monkeypatch.setattr(mil_risk_filter, "get_stock_status",
                         lambda ctx, phase, ticker: {
                             "ticker": ticker,
                             "trading_halted": False,
                             "administrative_issue": False,
                             "is_limit_up": False,
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
    saved = load_watchlist(path=tmp_path / "watchlist.json")
    assert saved == ["357780"]


def test_run_scan_v3_injects_next_day_prior_into_context(monkeypatch, tmp_path):
    import market_intelligence.portfolio as mil_portfolio

    monkeypatch.setattr(mil_portfolio, "get_open_positions",
                         lambda ctx, phase: {"positions": [], "position_count": 0})
    monkeypatch.setattr(mil_portfolio, "get_daily_pnl",
                         lambda ctx, phase: {"realized_pnl_pct": 0.0, "realized_pnl_krw": 0, "total_eval_amt": 10_000_000})

    orch = make_orchestrator(tmp_path)
    orch._trading_agent = FakeTradingAgent({
        "next_action": "final",
        "action": "WATCHLIST_UPDATE",
        "watchlist": ["005930"],
        "candidates": [],
        "reason": "ok",
    })

    regime = {
        "status": "YELLOW", "regime": "SIDEWAYS", "confidence": 50,
        "risk_guidance": {"max_positions": 2, "buy_confidence_threshold": 75,
                           "risk_per_trade_pct": 0.35, "min_trading_value_krw": 12_000_000_000},
        "timestamp": "2026-06-09T08:45:00",
    }
    (tmp_path / "last_regime.json").write_text(json.dumps(regime), encoding="utf-8")
    (tmp_path / "next_day_premarket_context.json").write_text(
        json.dumps({"focus_themes": ["반도체"], "intraday_focus": ["대형 반도체 우선 확인"]}, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr("orchestrator_v3._LAST_REGIME_PATH", tmp_path / "last_regime.json")
    monkeypatch.setattr("orchestrator_v3._DRIFT_STATE_PATH", tmp_path / "drift_state.json")
    monkeypatch.setattr("orchestrator_v3._WATCHLIST_PATH", tmp_path / "watchlist.json")
    monkeypatch.setattr("orchestrator_v3._NEXT_DAY_PREMARKET_CONTEXT_PATH", tmp_path / "next_day_premarket_context.json")

    orch.run_scan_v3()

    injected = orch._trading_agent.calls[0][1]["next_day_prior"]
    assert injected["focus_themes"] == ["반도체"]


def test_run_market_close_v3_persists_next_day_prior_to_data_path(monkeypatch, tmp_path):
    import json as _json
    import market_intelligence.portfolio as mil_portfolio

    orch = make_orchestrator(tmp_path)
    orch._collect_market_close_snapshot = lambda: {"kospi": 2800.0}
    orch._trading_agent = FakeTradingAgent({
        "close_market_read": {"focus_themes": ["반도체"]},
        "next_day_premarket_context": {"focus_themes": ["반도체"], "intraday_focus": ["대형주 먼저"]},
    })
    monkeypatch.setattr(mil_portfolio, "get_open_positions",
                         lambda ctx, phase: {"positions": [], "position_count": 0})
    monkeypatch.setattr(mil_portfolio, "get_daily_pnl",
                         lambda ctx, phase: {"realized_pnl_pct": 0.0, "realized_pnl_krw": 0.0, "total_eval_amt": 0.0})
    orch.run_close_review = lambda: None
    orch._save_json = lambda filename, payload: (tmp_path / filename).write_text(
        _json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    orch._summarize_tool_gaps = lambda: {}

    monkeypatch.setattr("orchestrator_v3._NEXT_DAY_PREMARKET_CONTEXT_PATH", tmp_path / "next_day_premarket_context_data.json")

    orch.run_market_close_v3()

    saved = load_next_day_premarket_context(path=tmp_path / "next_day_premarket_context_data.json")
    assert saved["focus_themes"] == ["반도체"]


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
    assert orch._trading_agent.calls[0][1]["watchlist"] == [
        {"ticker": "005930", "setup": "TREND", "confidence": 0, "reason": ""}
    ]
    assert orch._trading_agent.calls[0][1]["watchlist_tickers"] == ["005930"]
    assert orch._trading_agent.calls[0][1]["exploration_policy"]["allow_intraday_discovery"] is True
    saved_drift = json.loads((tmp_path / "drift_state.json").read_text(encoding="utf-8"))
    assert saved_drift["today_caution_count"] == 0


def test_run_intraday_v3_merges_watchlist_additions(monkeypatch, tmp_path):
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
    orch._trading_agent = FakeTradingAgent({
        "action": "NO_TRADE",
        "proposals": [],
        "watchlist_additions": ["357780"],
        "reason": "신규 리더 감시 등록",
    })

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

    orch.run_intraday_v3()

    assert load_watchlist(path=tmp_path / "watchlist.json") == ["005930", "357780"]


def test_sanitize_intraday_result_clears_proposals_for_hold_and_no_trade(tmp_path):
    orch = make_orchestrator(tmp_path)

    hold_result = orch._sanitize_intraday_result(
        {
            "action": "HOLD",
            "reason": "keep winner",
            "proposals": [
                {"ticker": "005930", "side": "SELL", "reason": "trim"},
                {"ticker": "000660", "side": "BUY", "reason": "add"},
                {"ticker": "067310", "side": "HOLD", "reason": "invalid"},
            ],
        }
    )
    no_trade_result = orch._sanitize_intraday_result(
        {
            "action": "NO_TRADE",
            "reason": "ambiguous",
            "proposals": [
                {"ticker": "005930", "side": "SELL", "reason": "invalid when no-trade"},
            ],
        }
    )

    assert hold_result["action"] == "HOLD"
    assert hold_result["proposals"] == []
    assert no_trade_result["action"] == "NO_TRADE"
    assert no_trade_result["proposals"] == []


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


def test_record_tool_request_appends_jsonl(monkeypatch, tmp_path):
    orch = make_orchestrator(tmp_path)
    tool_gap_path = tmp_path / "tool_gap_log.jsonl"

    orch._record_tool_request(
        {
            "action": "TOOL_REQUEST",
            "tool_request": {
                "missing_capability": "realtime_orderbook_imbalance",
                "priority": "high",
                "why_needed": "돌파 강도 확인 부족",
                "affected_tickers": ["000660"],
                "suggested_data_source": ["KIS websocket"],
                "fallback_action": "NO_TRADE",
            },
        },
        TradingPhase.INTRADAY,
        {"status": "YELLOW"},
    )

    rows = tool_gap_path.read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(rows[0])
    assert payload["missing_capability"] == "realtime_orderbook_imbalance"
    assert payload["priority"] == "high"
    assert payload["phase"] == "INTRADAY"


def test_summarize_tool_gaps_groups_today_records(monkeypatch, tmp_path):
    orch = make_orchestrator(tmp_path)
    tool_gap_path = tmp_path / "tool_gap_log.jsonl"
    tool_gap_path.write_text(
        "\n".join([
            json.dumps({
                "timestamp": "2026-06-09T10:00:00",
                "phase": "INTRADAY",
                "missing_capability": "realtime_orderbook_imbalance",
                "priority": "high",
                "affected_tickers": ["000660"],
            }, ensure_ascii=False),
            json.dumps({
                "timestamp": "2026-06-09T11:00:00",
                "phase": "SCAN",
                "missing_capability": "realtime_orderbook_imbalance",
                "priority": "medium",
                "affected_tickers": ["005930"],
            }, ensure_ascii=False),
            json.dumps({
                "timestamp": "2026-06-08T11:00:00",
                "phase": "SCAN",
                "missing_capability": "old_capability",
                "priority": "high",
                "affected_tickers": ["005930"],
            }, ensure_ascii=False),
        ]),
        encoding="utf-8",
    )

    summary = orch._summarize_tool_gaps(path=tool_gap_path)

    assert summary["high_priority_count"] == 1
    assert summary["top_missing_capabilities"][0]["name"] == "realtime_orderbook_imbalance"
    assert summary["top_missing_capabilities"][0]["count"] == 2


def test_summarize_tool_gaps_prunes_old_records(monkeypatch, tmp_path):
    orch = make_orchestrator(tmp_path)  # _today = "2026-06-09"
    tool_gap_path = tmp_path / "tool_gap_log.jsonl"
    tool_gap_path.write_text(
        "\n".join([
            json.dumps({
                "timestamp": "2026-06-09T10:00:00",
                "phase": "INTRADAY",
                "missing_capability": "realtime_orderbook_imbalance",
                "priority": "high",
                "affected_tickers": ["000660"],
            }, ensure_ascii=False),
            json.dumps({
                "timestamp": "2026-04-01T11:00:00",
                "phase": "SCAN",
                "missing_capability": "ancient_capability",
                "priority": "high",
                "affected_tickers": ["005930"],
            }, ensure_ascii=False),
        ]),
        encoding="utf-8",
    )

    orch._summarize_tool_gaps(path=tool_gap_path)

    remaining = [json.loads(line) for line in tool_gap_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(remaining) == 1
    assert remaining[0]["missing_capability"] == "realtime_orderbook_imbalance"


def test_alert_on_tool_failures_notifies_telegram(tmp_path):
    class FakeTelegram:
        def __init__(self):
            self.messages = []

        def notify(self, message):
            self.messages.append(message)

    orch = make_orchestrator(tmp_path)
    orch._telegram = FakeTelegram()
    result = {
        "action": "NO_TRADE",
        "reason": "tool_failures_exceeded",
        "tool_failures": [
            {"tool": "get_market_context", "error": "tool_failure", "message": "get_market_context: timeout"},
            {"tool": "get_flow", "error": "tool_failure", "message": "get_flow: circuit breaker open"},
            {"tool": "get_flow", "error": "tool_failure", "message": "get_flow: circuit breaker open"},
        ],
    }

    orch._alert_on_tool_failures(result, TradingPhase.INTRADAY)

    assert len(orch._telegram.messages) == 1
    assert "INTRADAY" in orch._telegram.messages[0]
    assert "get_market_context" in orch._telegram.messages[0]


def test_alert_on_tool_failures_skips_when_no_failures(tmp_path):
    class FakeTelegram:
        def __init__(self):
            self.messages = []

        def notify(self, message):
            self.messages.append(message)

    orch = make_orchestrator(tmp_path)
    orch._telegram = FakeTelegram()

    orch._alert_on_tool_failures({"action": "NO_TRADE", "reason": "no_trading_signal"}, TradingPhase.INTRADAY)

    assert orch._telegram.messages == []


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


def test_market_close_snapshot_collected_by_code(monkeypatch, tmp_path):
    """market_close_snapshot은 LLM 출력이 아니라 코드가 수집해 저장한다."""
    import market_intelligence.market as mil_market
    import market_intelligence.portfolio as mil_portfolio
    import json as _json

    monkeypatch.setattr(mil_market, "get_market_context",
                         lambda ctx, phase: {"kospi": 8294.95, "kospi_change_pct": 7.59,
                                              "kosdaq": 1030.66, "kosdaq_change_pct": 4.97,
                                              "foreign_net_buy_krw": 1.0, "institution_net_buy_krw": 2.0,
                                              "program_net_buy_krw": 3.0, "investor_trend_days": []})
    monkeypatch.setattr(mil_market, "get_sector_breadth",
                         lambda ctx, phase: {"market_breadth": {"advancers": 800, "decliners": 90},
                                              "sectors": [{"sector_name": "반도체", "change_pct": 9.5}]})
    monkeypatch.setattr(mil_market, "get_news_market",
                         lambda ctx, phase: {"headlines": [{"title": "마감"}]})
    monkeypatch.setattr(mil_portfolio, "get_open_positions",
                         lambda ctx, phase: {"positions": [], "position_count": 0})
    monkeypatch.setattr(mil_portfolio, "get_daily_pnl",
                         lambda ctx, phase: {"realized_pnl_pct": 0.0, "realized_pnl_krw": 0, "total_eval_amt": 1})

    orch = make_orchestrator(tmp_path)
    orch._trading_agent = FakeTradingAgent({"action": "MARKET_CLOSE_ANALYSIS",
                                             "close_market_read": {"market_quality": "GOOD"},
                                             "next_day_premarket_context": {}})
    monkeypatch.setattr(orch, "run_close_review", lambda: None)
    monkeypatch.setattr(orch, "_save_json",
                         lambda name, data: (tmp_path / name).write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8"))
    monkeypatch.setattr("orchestrator_v3._LAST_REGIME_PATH", tmp_path / "last_regime.json")

    orch.run_market_close_v3()

    snap = _json.loads((tmp_path / "market_close_snapshot.json").read_text(encoding="utf-8"))
    assert snap["kospi_change_pct"] == 7.59
    assert snap["market_breadth"]["advancers"] == 800
    assert snap["data_quality"]["missing_fields"] == []
    # LLM 컨텍스트에 팩트가 주입되었는지
    assert orch._trading_agent.calls[0][1]["market_close_data"]["kospi"] == 8294.95


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
    monkeypatch.setattr(orch, "_review_v3_buy_proposal", lambda **kwargs: {"approve": True, "reason": "ok"})
    monkeypatch.setattr(orch, "build_portfolio_state", lambda: object())
    monkeypatch.setattr(orch, "_estimate_atr", lambda ticker: 1500.0)

    class FakeSnapshot:
        current_price = 70000.0

    monkeypatch.setattr(orch, "_market_data", type("MD", (), {"get_snapshot": staticmethod(lambda t: FakeSnapshot())})())

    proposal = {"ticker": "005930", "side": "BUY", "confidence": 82, "stop_loss_price": 68000, "reason": "강한 회복"}
    result = orch._process_v3_buy_proposal(proposal)

    assert result["action"] == "BUY_EXECUTED"
    assert orch._order_manager.buy_calls[0].ticker == "005930"


def test_orchestrator_init_builds_milcontext_with_kiwoom_api(monkeypatch, tmp_path):
    import orchestrator_v3 as mod

    class StubMarketData:
        def __init__(self, data_source=None):
            self.data_source = data_source

    class StubApproval:
        pass

    class StubJournal:
        pass

    class StubOrderManager:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class StubNewsFetcher:
        def __init__(self):
            self.available = True

    class StubImprovementManager:
        def __init__(self, telegram=None):
            self.telegram = telegram

        def process_telegram_actions(self):
            return 0

    class StubTradingAgent:
        def __init__(self, mil=None):
            self.mil = mil

    captured: dict[str, object] = {}

    class StubMILContext:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.kis_api = kwargs.get("kis_api")
            self.kiwoom_api = kwargs.get("kiwoom_api")
            self.cache = kwargs.get("cache")
            self.circuit_breaker = kwargs.get("circuit_breaker")

    monkeypatch.setattr(mod, "KISApi", lambda: object())
    monkeypatch.setattr(mod, "KiwoomApi", lambda: object())
    monkeypatch.setattr(mod, "MarketData", StubMarketData)
    monkeypatch.setattr(mod, "RiskOfficer", lambda: object())
    monkeypatch.setattr(mod, "PositionSizer", lambda: object())
    monkeypatch.setattr(mod, "StopTakeProfitManager", lambda: object())
    monkeypatch.setattr(mod, "TechnicalAnalysis", lambda: object())
    monkeypatch.setattr(mod, "RegimeAgent", lambda: object())
    monkeypatch.setattr(mod, "ReviewAgent", lambda: object())
    monkeypatch.setattr(mod, "SelfImprovementAgent", lambda: object())
    monkeypatch.setattr(mod, "LLMClient", lambda: object())
    monkeypatch.setattr(mod, "TelegramApproval", StubApproval)
    monkeypatch.setattr(mod, "TradeJournal", StubJournal)
    monkeypatch.setattr(mod, "OrderManager", StubOrderManager)
    monkeypatch.setattr(mod, "NaverNewsFetcher", StubNewsFetcher)
    monkeypatch.setattr(mod, "ImprovementManager", StubImprovementManager)
    monkeypatch.setattr(mod, "MILContext", StubMILContext)
    monkeypatch.setattr(mod, "MILCache", lambda: object())
    monkeypatch.setattr(mod, "CircuitBreaker", lambda: object())
    monkeypatch.setattr(mod, "RegimeDriftDetector", lambda: object())
    monkeypatch.setattr(mod, "TradingAgent", StubTradingAgent)
    monkeypatch.setattr(mod, "LOG_CONFIG", replace(mod.LOG_CONFIG, base_dir=tmp_path))

    orch = mod.MQKOrchestratorV3()

    assert orch._mil.kiwoom_api is captured["kiwoom_api"]
    assert captured["kiwoom_api"] is not None
    assert orch._trading_agent.mil is orch._mil


# ── Critical 1/2: malformed proposals must not crash _handle_proposals ─────

def test_handle_proposals_skips_malformed_and_executes_valid(tmp_path, monkeypatch):
    orch = make_orchestrator(tmp_path)
    orch._risk_officer = FakeRiskOfficer()
    orch._position_sizer = FakePositionSizer()
    orch._telegram = FakeTelegramApproval(approved=True)
    orch._order_manager = FakeOrderManager()
    monkeypatch.setattr(orch, "_review_v3_buy_proposal", lambda **kwargs: {"approve": True, "reason": "ok"})
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
    monkeypatch.setattr(orch, "_review_v3_buy_proposal", lambda **kwargs: {"approve": True, "reason": "ok"})
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
    monkeypatch.setattr(orch, "_review_v3_buy_proposal", lambda **kwargs: {"approve": True, "reason": "ok"})
    monkeypatch.setattr(orch, "build_portfolio_state", lambda: object())
    monkeypatch.setattr(orch, "_estimate_atr", lambda ticker: 1500.0)

    class FakeSnapshot:
        current_price = 70000.0

    monkeypatch.setattr(orch, "_market_data", type("MD", (), {"get_snapshot": staticmethod(lambda t: FakeSnapshot())})())

    proposals = [{"ticker": "005930", "side": "BUY", "confidence": 82, "stop_loss_price": "abc", "reason": "이상값"}]
    results = orch._handle_proposals(proposals)

    assert results[0]["action"] == "SKIP"
    assert results[0]["reason"] == "malformed_proposal"


def test_process_v3_buy_proposal_resolves_stock_name_for_approval(tmp_path, monkeypatch):
    """텔레그램 승인 요청과 주문에 종목명이 포함되어야 한다 (코드만 ❌)."""
    import orchestrator_v3
    monkeypatch.setattr(orchestrator_v3, "RISK", replace(orchestrator_v3.RISK, require_telegram_approval=True))
    orch = make_orchestrator(tmp_path)
    orch._risk_officer = FakeRiskOfficer()
    orch._position_sizer = FakePositionSizer()
    orch._telegram = FakeTelegramApproval(approved=True)
    orch._order_manager = FakeOrderManager()
    monkeypatch.setattr(orch, "_review_v3_buy_proposal", lambda **kwargs: {"approve": True, "reason": "ok"})
    monkeypatch.setattr(orch, "build_portfolio_state", lambda: object())
    monkeypatch.setattr(orch, "_estimate_atr", lambda ticker: 1500.0)

    class NamedSnapshot:
        current_price = 70000.0
        name = "삼성전자"

    monkeypatch.setattr(orch, "_market_data",
                         type("MD", (), {"get_snapshot": staticmethod(lambda t: NamedSnapshot())})())

    proposal = {"ticker": "005930", "side": "BUY", "confidence": 82,
                "stop_loss_price": 68000, "reason": "테스트"}
    orch._process_v3_buy_proposal(proposal)

    assert orch._telegram.requests[0].name == "삼성전자"
    assert orch._order_manager.buy_calls[0].name == "삼성전자"


def test_process_v3_buy_proposal_blocked_by_risk_officer(tmp_path, monkeypatch):
    orch = make_orchestrator(tmp_path)
    orch._risk_officer = FakeRiskOfficer(raise_violation=True)
    orch._position_sizer = FakePositionSizer()
    orch._telegram = FakeTelegramApproval(approved=True)
    orch._order_manager = FakeOrderManager()
    monkeypatch.setattr(orch, "_review_v3_buy_proposal", lambda **kwargs: {"approve": True, "reason": "ok"})
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
    monkeypatch.setattr(orch, "_review_v3_buy_proposal", lambda **kwargs: {"approve": True, "reason": "ok"})
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


def test_process_v3_buy_proposal_rejected_by_buy_review(tmp_path, monkeypatch):
    orch = make_orchestrator(tmp_path)
    orch._risk_officer = FakeRiskOfficer()
    orch._position_sizer = FakePositionSizer()
    orch._telegram = FakeTelegramApproval(approved=True)
    orch._order_manager = FakeOrderManager()
    monkeypatch.setattr(orch, "_review_v3_buy_proposal", lambda **kwargs: {"approve": False, "reason": "weak follower"})
    monkeypatch.setattr(orch, "build_portfolio_state", lambda: object())
    monkeypatch.setattr(orch, "_estimate_atr", lambda ticker: 1500.0)

    class FakeSnapshot:
        current_price = 70000.0

    monkeypatch.setattr(orch, "_market_data", type("MD", (), {"get_snapshot": staticmethod(lambda t: FakeSnapshot())})())

    proposal = {"ticker": "005930", "side": "BUY", "confidence": 82, "stop_loss_price": 68000, "reason": "강한 회복"}
    result = orch._process_v3_buy_proposal(proposal)

    assert result["action"] == "REJECTED"
    assert result["reason"] == "weak follower"
    assert orch._order_manager.buy_calls == []
