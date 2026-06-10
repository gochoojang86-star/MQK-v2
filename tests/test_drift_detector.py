"""RegimeDriftDetector 테스트 - Tier2(코드 감시) + Tier3(Lite LLM) + CAUTION 카운터"""
from agents.drift_detector import (
    RegimeDriftDetector,
    compute_metrics,
    evaluate_triggers,
)


DRIFT_TRIGGERS = [
    {"id": "index_sharp_drop", "metric": "kospi_drop_from_open_pct",
     "threshold": -1.5, "direction": "below", "description": "급락"},
    {"id": "foreign_heavy_sell", "metric": "foreign_net_sell_cumulative_bln",
     "threshold": 4000, "direction": "above", "description": "외인 매도"},
    {"id": "breadth_collapse", "metric": "advance_decline_ratio",
     "threshold": 0.25, "direction": "below", "description": "쏠림"},
    {"id": "recovery_signal", "metric": "kospi_recovery_from_low_pct",
     "threshold": 1.0, "direction": "above", "description": "회복"},
]


class FakeLiteLLM:
    def __init__(self, response):
        self._response = response
        self.calls = 0

    def call(self, system, user, tier=None, expect_json=True):
        self.calls += 1
        return self._response


def test_compute_metrics():
    snapshot = {
        "kospi_current": 2480.0,
        "kospi_open": 2530.0,
        "kospi_low": 2470.0,
        "foreign_net_buy_bln": -4500,
        "advance_count": 150,
        "decline_count": 700,
    }
    metrics = compute_metrics(snapshot)

    assert round(metrics["kospi_drop_from_open_pct"], 2) == round((2480.0 - 2530.0) / 2530.0 * 100, 2)
    assert round(metrics["kospi_recovery_from_low_pct"], 2) == round((2480.0 - 2470.0) / 2470.0 * 100, 2)
    assert metrics["foreign_net_sell_cumulative_bln"] == 4500
    assert round(metrics["advance_decline_ratio"], 4) == round(150 / 850, 4)


def test_evaluate_triggers_fires_on_threshold_breach():
    metrics = {
        "kospi_drop_from_open_pct": -2.0,   # < -1.5 → fires
        "kospi_recovery_from_low_pct": 0.2,  # < 1.0 → no fire
        "foreign_net_sell_cumulative_bln": 1000,  # < 4000 → no fire
        "advance_decline_ratio": 0.5,       # > 0.25 → no fire
    }
    drift_state = {"last_trigger_time": {}}
    triggered = evaluate_triggers(metrics, DRIFT_TRIGGERS, drift_state, cooldown_minutes=60)

    assert len(triggered) == 1
    assert triggered[0]["id"] == "index_sharp_drop"


def test_evaluate_triggers_respects_cooldown():
    from datetime import datetime, timedelta

    metrics = {
        "kospi_drop_from_open_pct": -2.0,
        "kospi_recovery_from_low_pct": 0.2,
        "foreign_net_sell_cumulative_bln": 1000,
        "advance_decline_ratio": 0.5,
    }
    recent = (datetime.now() - timedelta(minutes=10)).isoformat()
    drift_state = {"last_trigger_time": {"index_sharp_drop": recent}}
    triggered = evaluate_triggers(metrics, DRIFT_TRIGGERS, drift_state, cooldown_minutes=60)

    assert triggered == []


def test_check_returns_stable_when_no_trigger_fires():
    snapshot = {
        "kospi_current": 2525.0, "kospi_open": 2530.0, "kospi_low": 2520.0,
        "foreign_net_buy_bln": -100, "advance_count": 500, "decline_count": 400,
    }
    detector = RegimeDriftDetector(llm=FakeLiteLLM({}))
    drift_state = {"last_trigger_time": {}, "today_caution_count": 0, "daily_lite_llm_calls": 0}

    result = detector.check(snapshot, DRIFT_TRIGGERS, cooldown_minutes=60,
                             max_daily_triggers=3, drift_state=drift_state)

    assert result["drift_judgment"] == "STABLE"
    assert detector._llm.calls == 0


def test_check_calls_lite_llm_and_returns_caution():
    snapshot = {
        "kospi_current": 2480.0, "kospi_open": 2530.0, "kospi_low": 2470.0,
        "foreign_net_buy_bln": -4500, "advance_count": 150, "decline_count": 700,
    }
    lite_response = {
        "drift_judgment": "CAUTION",
        "reason": "외국인 매도 강도 높으나 RED 전환 기준 미달",
        "new_status": None,
        "risk_guidance_delta": {
            "buy_confidence_threshold": 82,
            "risk_per_trade_pct": 0.25,
            "max_positions": 3,
        },
        "updated_triggers": [],
    }
    detector = RegimeDriftDetector(llm=FakeLiteLLM(lite_response))
    drift_state = {"last_trigger_time": {}, "today_caution_count": 0, "daily_lite_llm_calls": 0}

    result = detector.check(snapshot, DRIFT_TRIGGERS, cooldown_minutes=60,
                             max_daily_triggers=3, drift_state=drift_state)

    assert result["drift_judgment"] == "CAUTION"
    assert result["risk_guidance_delta"]["max_positions"] == 3
    assert detector._llm.calls == 1
    assert result["drift_state"]["today_caution_count"] == 1
    assert result["drift_state"]["daily_lite_llm_calls"] == 1
    assert "index_sharp_drop" in result["drift_state"]["last_trigger_time"]


def test_check_skips_lite_llm_when_daily_limit_reached():
    snapshot = {
        "kospi_current": 2480.0, "kospi_open": 2530.0, "kospi_low": 2470.0,
        "foreign_net_buy_bln": -4500, "advance_count": 150, "decline_count": 700,
    }
    detector = RegimeDriftDetector(llm=FakeLiteLLM({}))
    drift_state = {"last_trigger_time": {}, "today_caution_count": 0, "daily_lite_llm_calls": 3}

    result = detector.check(snapshot, DRIFT_TRIGGERS, cooldown_minutes=60,
                             max_daily_triggers=3, drift_state=drift_state)

    assert result["drift_judgment"] == "STABLE"
    assert result["reason"] == "daily_limit_reached"
    assert detector._llm.calls == 0


def test_caution_counter_auto_escalates_to_regime_shift():
    snapshot = {
        "kospi_current": 2480.0, "kospi_open": 2530.0, "kospi_low": 2470.0,
        "foreign_net_buy_bln": -4500, "advance_count": 150, "decline_count": 700,
    }
    lite_response = {
        "drift_judgment": "CAUTION",
        "reason": "지속적 외인 매도",
        "new_status": None,
        "risk_guidance_delta": {"buy_confidence_threshold": 85, "risk_per_trade_pct": 0.20, "max_positions": 2},
        "updated_triggers": [],
    }
    detector = RegimeDriftDetector(llm=FakeLiteLLM(lite_response))
    # 이미 오늘 2번 CAUTION이 있었던 상태 (이번이 3번째)
    drift_state = {"last_trigger_time": {}, "today_caution_count": 2, "daily_lite_llm_calls": 2}

    result = detector.check(snapshot, DRIFT_TRIGGERS, cooldown_minutes=60,
                             max_daily_triggers=5, drift_state=drift_state,
                             current_status="YELLOW")

    assert result["drift_judgment"] == "REGIME_SHIFT"
    assert result["new_status"] == "RED"
    assert result["drift_state"]["today_caution_count"] == 3


def test_check_clamps_out_of_bounds_risk_guidance_delta():
    snapshot = {
        "kospi_current": 2480.0, "kospi_open": 2530.0, "kospi_low": 2470.0,
        "foreign_net_buy_bln": -4500, "advance_count": 150, "decline_count": 700,
    }
    lite_response = {
        "drift_judgment": "CAUTION",
        "reason": "극단적 risk_guidance_delta 시도",
        "new_status": None,
        "risk_guidance_delta": {
            "buy_confidence_threshold": 999,   # 안전 상한(95.0) 초과
            "max_positions": 0,                # 안전 하한(1) 미달
        },
        "updated_triggers": [],
    }
    detector = RegimeDriftDetector(llm=FakeLiteLLM(lite_response))
    drift_state = {"last_trigger_time": {}, "today_caution_count": 0, "daily_lite_llm_calls": 0}

    result = detector.check(snapshot, DRIFT_TRIGGERS, cooldown_minutes=60,
                             max_daily_triggers=3, drift_state=drift_state)

    delta = result["risk_guidance_delta"]
    # delta semantics 유지: 원래 전달된 키만 존재
    assert set(delta.keys()) == {"buy_confidence_threshold", "max_positions"}
    assert delta["buy_confidence_threshold"] == 95.0
    assert delta["max_positions"] == 1


def test_evaluate_triggers_skips_malformed_drift_triggers():
    metrics = {
        "kospi_drop_from_open_pct": -2.0,   # < -1.5 → would fire
        "kospi_recovery_from_low_pct": 0.2,
        "foreign_net_sell_cumulative_bln": 1000,
        "advance_decline_ratio": 0.5,
    }
    drift_state = {"last_trigger_time": {}}
    malformed_triggers = [
        "not_a_dict",
        {"id": "missing_metric", "threshold": -1.5, "direction": "below"},
        {"id": "bad_threshold", "metric": "kospi_drop_from_open_pct",
         "threshold": "abc", "direction": "below"},
        {"id": "index_sharp_drop", "metric": "kospi_drop_from_open_pct",
         "threshold": -1.5, "direction": "below", "description": "급락"},
    ]

    triggered = evaluate_triggers(metrics, malformed_triggers, drift_state, cooldown_minutes=60)

    assert len(triggered) == 1
    assert triggered[0]["id"] == "index_sharp_drop"


def test_downgrade_status_progression():
    from agents.drift_detector import _downgrade_status
    assert _downgrade_status("GREEN") == "YELLOW"
    assert _downgrade_status("YELLOW") == "RED"
    assert _downgrade_status("RED") == "RED"


def test_downgrade_status_unknown_input_clamps_to_red():
    from agents.drift_detector import _downgrade_status
    assert _downgrade_status("PURPLE") == "RED"


# ── Critical 4: zero/None kospi values must not raise ───────────────────────

def test_compute_metrics_handles_zero_kospi_open():
    snapshot = {
        "kospi_current": 2480.0, "kospi_open": 0, "kospi_low": 2470.0,
        "foreign_net_buy_bln": -100, "advance_count": 500, "decline_count": 400,
    }
    metrics = compute_metrics(snapshot)
    assert metrics["kospi_drop_from_open_pct"] == 0.0


def test_compute_metrics_handles_none_kospi_current():
    snapshot = {
        "kospi_current": None, "kospi_open": 2530.0, "kospi_low": 2470.0,
        "foreign_net_buy_bln": -100, "advance_count": 500, "decline_count": 400,
    }
    # 핵심: TypeError/ZeroDivisionError 없이 계산이 완료되어야 한다.
    metrics = compute_metrics(snapshot)
    assert isinstance(metrics["kospi_drop_from_open_pct"], float)
    assert isinstance(metrics["kospi_recovery_from_low_pct"], float)


# ── Important 5: invalid new_status from Lite LLM must be sanitized ─────────

def test_check_sanitizes_invalid_new_status_on_regime_shift():
    snapshot = {
        "kospi_current": 2480.0, "kospi_open": 2530.0, "kospi_low": 2470.0,
        "foreign_net_buy_bln": -4500, "advance_count": 150, "decline_count": 700,
    }
    lite_response = {
        "drift_judgment": "REGIME_SHIFT",
        "reason": "급변",
        "new_status": "PURPLE",
        "risk_guidance_delta": {},
        "updated_triggers": [],
    }
    detector = RegimeDriftDetector(llm=FakeLiteLLM(lite_response))
    drift_state = {"last_trigger_time": {}, "today_caution_count": 0, "daily_lite_llm_calls": 0}

    result = detector.check(snapshot, DRIFT_TRIGGERS, cooldown_minutes=60,
                             max_daily_triggers=3, drift_state=drift_state,
                             current_status="YELLOW")

    assert result["new_status"] in {"GREEN", "YELLOW", "RED"}
