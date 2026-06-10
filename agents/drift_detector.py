"""RegimeDriftDetector - 3-tier 비용 모델의 Tier2(코드 감시) + Tier3(Lite LLM) 구현.

Tier1(Full LLM)은 RegimeAgent가 담당. 이 모듈은 5분마다 무료로 drift_triggers를
체크하고(Tier2), 발동 시에만 Lite LLM을 호출한다(Tier3).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from codes.risk_officer import clamp_risk_guidance
from config.settings import ModelTier
from llm.client import LLMClient
from llm.soul import inject_agent

logger = logging.getLogger("mqk_v3")

_SYSTEM_PROMPT = inject_agent("drift_detector")

_STATUS_ORDER = ["GREEN", "YELLOW", "RED"]


def compute_metrics(snapshot: dict[str, Any]) -> dict[str, float]:
    """시장 스냅샷에서 drift_trigger 평가용 지표를 계산한다 (코드, 무료).

    분모(kospi_open/kospi_low)가 0이거나 값이 None/누락이면 해당 지표는 0.0으로
    degrade한다 (ZeroDivisionError/TypeError 방지).
    """
    kospi_current = snapshot.get("kospi_current")
    kospi_open = snapshot.get("kospi_open")
    kospi_low = snapshot.get("kospi_low")
    foreign_net_buy_bln = float(snapshot["foreign_net_buy_bln"])
    advance_count = float(snapshot["advance_count"])
    decline_count = float(snapshot["decline_count"])

    kospi_current = float(kospi_current) if kospi_current is not None else 0.0
    kospi_open = float(kospi_open) if kospi_open is not None else 0.0
    kospi_low = float(kospi_low) if kospi_low is not None else 0.0

    kospi_drop_from_open_pct = (
        (kospi_current - kospi_open) / kospi_open * 100 if kospi_open else 0.0
    )
    kospi_recovery_from_low_pct = (
        (kospi_current - kospi_low) / kospi_low * 100 if kospi_low else 0.0
    )
    # foreign_net_buy_bln이 음수(순매도)일 때 양수 값으로 변환
    foreign_net_sell_cumulative_bln = max(-foreign_net_buy_bln, 0.0)
    total = advance_count + decline_count
    advance_decline_ratio = advance_count / total if total > 0 else 0.0

    return {
        "kospi_drop_from_open_pct": kospi_drop_from_open_pct,
        "kospi_recovery_from_low_pct": kospi_recovery_from_low_pct,
        "foreign_net_sell_cumulative_bln": foreign_net_sell_cumulative_bln,
        "advance_decline_ratio": advance_decline_ratio,
    }


def evaluate_triggers(
    metrics: dict[str, float],
    drift_triggers: list[dict],
    drift_state: dict[str, Any],
    cooldown_minutes: int,
    now: datetime | None = None,
) -> list[dict]:
    """발동된 drift_trigger 목록을 반환한다 (쿨다운 적용)."""
    now = now or datetime.now()
    last_trigger_time = drift_state.get("last_trigger_time", {})
    triggered = []

    for trigger in drift_triggers:
        metric_value = metrics.get(trigger["metric"])
        if metric_value is None:
            continue

        threshold = trigger["threshold"]
        direction = trigger["direction"]
        if direction == "above":
            fired = metric_value > threshold
        elif direction == "below":
            fired = metric_value < threshold
        else:
            continue

        if not fired:
            continue

        last_fired = last_trigger_time.get(trigger["id"])
        if last_fired is not None:
            elapsed = now - datetime.fromisoformat(last_fired)
            if elapsed < timedelta(minutes=cooldown_minutes):
                continue

        triggered.append(trigger)

    return triggered


def _downgrade_status(status: str) -> str:
    """상태를 한 단계 악화시킨다 (GREEN→YELLOW→RED, RED는 유지).

    알 수 없는 입력은 가장 보수적인 "RED"로 클램핑한다.
    """
    if status not in _STATUS_ORDER:
        return "RED"
    idx = _STATUS_ORDER.index(status)
    return _STATUS_ORDER[min(idx + 1, len(_STATUS_ORDER) - 1)]


def _clamp_risk_guidance_delta(delta: dict[str, Any]) -> dict[str, Any]:
    """risk_guidance_delta의 partial-dict(delta) 의미를 유지한 채 안전 범위로 클램핑한다.

    clamp_risk_guidance()는 누락된 키에 기본값을 채워 4개 키를 모두 반환하므로,
    여기서는 클램핑 후 원래 delta에 존재했던 키만 다시 추려낸다.
    """
    if not delta:
        return {}
    clamped = clamp_risk_guidance(delta)
    return {key: clamped[key] for key in delta if key in clamped}


class RegimeDriftDetector:
    """5분마다 drift_triggers를 체크하고, 발동 시 Lite LLM을 호출한다."""

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm or LLMClient()

    def check(
        self,
        market_snapshot: dict[str, Any],
        drift_triggers: list[dict],
        cooldown_minutes: int,
        max_daily_triggers: int,
        drift_state: dict[str, Any],
        current_status: str = "YELLOW",
        current_regime: dict | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now()
        metrics = compute_metrics(market_snapshot)
        triggered = evaluate_triggers(metrics, drift_triggers, drift_state, cooldown_minutes, now)

        new_drift_state = dict(drift_state)
        new_drift_state.setdefault("last_trigger_time", dict(drift_state.get("last_trigger_time", {})))
        new_drift_state.setdefault("today_caution_count", drift_state.get("today_caution_count", 0))
        new_drift_state.setdefault("daily_lite_llm_calls", drift_state.get("daily_lite_llm_calls", 0))

        if not triggered:
            return {
                "drift_judgment": "STABLE",
                "reason": "no_trigger_fired",
                "metrics": metrics,
                "triggered": [],
                "new_status": None,
                "risk_guidance_delta": {},
                "drift_state": new_drift_state,
            }

        if new_drift_state["daily_lite_llm_calls"] >= max_daily_triggers:
            return {
                "drift_judgment": "STABLE",
                "reason": "daily_limit_reached",
                "metrics": metrics,
                "triggered": triggered,
                "new_status": None,
                "risk_guidance_delta": {},
                "drift_state": new_drift_state,
            }

        result = self._call_lite_llm(current_regime or {}, metrics, triggered)

        for trigger in triggered:
            new_drift_state["last_trigger_time"][trigger["id"]] = now.isoformat()
        new_drift_state["daily_lite_llm_calls"] += 1

        drift_judgment = result.get("drift_judgment", "STABLE")
        if drift_judgment == "CAUTION":
            new_drift_state["today_caution_count"] += 1
            if new_drift_state["today_caution_count"] >= 3:
                drift_judgment = "REGIME_SHIFT"
                result["new_status"] = _downgrade_status(current_status)

        if drift_judgment == "REGIME_SHIFT":
            new_status = result.get("new_status")
            if new_status not in {"GREEN", "YELLOW", "RED"}:
                logger.warning(
                    f"[drift_detector] Lite LLM이 유효하지 않은 new_status={new_status!r}를 "
                    f"반환 — _downgrade_status({current_status!r})로 대체"
                )
                result["new_status"] = _downgrade_status(current_status)

        return {
            "drift_judgment": drift_judgment,
            "reason": result.get("reason", ""),
            "metrics": metrics,
            "triggered": triggered,
            "new_status": result.get("new_status"),
            "risk_guidance_delta": _clamp_risk_guidance_delta(result.get("risk_guidance_delta", {})),
            "updated_triggers": result.get("updated_triggers", []),
            "drift_state": new_drift_state,
        }

    def _call_lite_llm(
        self, current_regime: dict, metrics: dict[str, float], triggered: list[dict]
    ) -> dict[str, Any]:
        user_msg = json.dumps(
            {
                "current_regime": current_regime,
                "triggered": triggered,
                "metrics": metrics,
            },
            ensure_ascii=False,
        )
        return self._llm.call(system=_SYSTEM_PROMPT, user=user_msg, tier=ModelTier.FAST, expect_json=True)
