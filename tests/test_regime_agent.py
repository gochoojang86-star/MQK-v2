"""RegimeAgent v3 확장 테스트 - risk_guidance/drift_triggers 출력 + last_regime 캐시"""
from agents.regime_agent import (
    RegimeAgent,
    RegimeJudgment,
    MarketStatus,
    Regime,
    save_last_regime,
    load_last_regime,
)


class FakeLLMClient:
    def __init__(self, response):
        self._response = response
        self.last_user = None

    def call(self, system, user, tier=None, expect_json=True):
        self.last_user = user
        return self._response


def test_judge_extracts_risk_guidance_and_drift_triggers():
    raw = {
        "status": "YELLOW", "regime": "SIDEWAYS", "confidence": 44,
        "reason": "혼조세",
        "risk_guidance": {
            "buy_confidence_threshold": 75, "risk_per_trade_pct": 0.35,
            "max_positions": 4, "min_trading_value_krw": 10_000_000_000,
        },
        "drift_triggers": [
            {"id": "index_sharp_drop", "metric": "kospi_drop_from_open_pct",
             "threshold": -1.5, "direction": "below"},
        ],
        "cooldown_minutes": 60,
        "max_daily_triggers": 3,
    }
    agent = RegimeAgent(llm=FakeLLMClient(raw))
    judgment = agent.judge({})

    assert judgment.risk_guidance["buy_confidence_threshold"] == 75
    assert judgment.drift_triggers[0]["id"] == "index_sharp_drop"
    assert judgment.cooldown_minutes == 60
    assert judgment.max_daily_triggers == 3


def test_judge_uses_opening_weighting_by_default():
    raw = {"status": "GREEN", "regime": "UPTREND", "confidence": 70, "reason": "강세"}
    llm = FakeLLMClient(raw)
    agent = RegimeAgent(llm=llm)

    agent.judge({})

    assert "장초반 레짐 평가 데이터" in llm.last_user
    assert "전일 확정 데이터를 주요 근거로" in llm.last_user


def test_judge_uses_intraday_weighting_for_midday():
    raw = {"status": "GREEN", "regime": "UPTREND", "confidence": 70, "reason": "강세"}
    llm = FakeLLMClient(raw)
    agent = RegimeAgent(llm=llm)

    agent.judge({}, evaluation_mode="MIDDAY", evaluation_time="11:03")

    assert "장중 레짐 재평가 데이터 (11:03 기준)" in llm.last_user
    assert "당일 장중 데이터를 주요 근거로 사용" in llm.last_user
    assert "전일 확정 데이터는 배경 참고로만 사용" in llm.last_user


def test_judge_clamps_extreme_risk_guidance_via_safety_bounds():
    raw = {
        "status": "RED", "regime": "RISK_OFF", "confidence": 90, "reason": "급락",
        "risk_guidance": {
            "buy_confidence_threshold": 30,   # 너무 낮음 → 65로 클램프
            "risk_per_trade_pct": 5.0,        # 너무 큼 → 0.50로 클램프
            "max_positions": 99,              # 너무 큼 → 5로 클램프
            "min_trading_value_krw": 1,       # 너무 작음 → 50억으로 클램프
        },
        "drift_triggers": [],
    }
    agent = RegimeAgent(llm=FakeLLMClient(raw))
    judgment = agent.judge({})

    assert judgment.risk_guidance["buy_confidence_threshold"] == 65.0
    assert judgment.risk_guidance["risk_per_trade_pct"] == 0.50
    assert judgment.risk_guidance["max_positions"] == 5
    assert judgment.risk_guidance["min_trading_value_krw"] == 5_000_000_000


def test_judge_defaults_when_v3_fields_missing():
    raw = {"status": "GREEN", "regime": "UPTREND", "confidence": 70, "reason": "강세"}
    agent = RegimeAgent(llm=FakeLLMClient(raw))
    judgment = agent.judge({})

    assert judgment.drift_triggers == []
    assert judgment.cooldown_minutes == 60
    assert judgment.max_daily_triggers == 3
    # risk_guidance 누락 시 RegimeSafetyBounds의 최소값으로 채워진다 (가장 보수적)
    assert judgment.risk_guidance["max_positions"] == 1


def test_judge_clamps_extreme_cooldown_and_daily_triggers():
    raw = {
        "status": "YELLOW", "regime": "SIDEWAYS", "confidence": 50, "reason": "혼조세",
        "max_daily_triggers": 1000,
        "cooldown_minutes": 0,
    }
    agent = RegimeAgent(llm=FakeLLMClient(raw))
    judgment = agent.judge({})

    assert judgment.max_daily_triggers == 5
    assert judgment.cooldown_minutes == 15


def test_judge_uses_defaults_for_garbage_cooldown_and_daily_triggers():
    raw = {
        "status": "YELLOW", "regime": "SIDEWAYS", "confidence": 50, "reason": "혼조세",
        "max_daily_triggers": "abc",
        "cooldown_minutes": "abc",
    }
    agent = RegimeAgent(llm=FakeLLMClient(raw))
    judgment = agent.judge({})

    assert judgment.max_daily_triggers == 3
    assert judgment.cooldown_minutes == 60


def test_save_and_load_last_regime(tmp_path):
    raw = {
        "status": "YELLOW", "regime": "SIDEWAYS", "confidence": 44, "reason": "혼조세",
        "risk_guidance": {
            "buy_confidence_threshold": 75, "risk_per_trade_pct": 0.35,
            "max_positions": 4, "min_trading_value_krw": 10_000_000_000,
        },
        "drift_triggers": [
            {"id": "recovery_signal", "metric": "kospi_recovery_from_low_pct",
             "threshold": 1.0, "direction": "above"},
        ],
    }
    agent = RegimeAgent(llm=FakeLLMClient(raw))
    judgment = agent.judge({})

    path = tmp_path / "last_regime.json"
    save_last_regime(judgment, path=path)
    loaded = load_last_regime(path=path)

    assert loaded["status"] == "YELLOW"
    assert loaded["regime"] == "SIDEWAYS"
    assert loaded["drift_triggers"][0]["id"] == "recovery_signal"
    assert loaded["risk_guidance"]["max_positions"] == 4
    assert "timestamp" in loaded


def test_load_last_regime_returns_none_if_missing(tmp_path):
    assert load_last_regime(path=tmp_path / "missing.json") is None


def test_load_last_regime_returns_none_on_corrupt_json(tmp_path):
    path = tmp_path / "last_regime.json"
    path.write_bytes(b"{not valid json!!")

    assert load_last_regime(path=path) is None


def test_save_last_regime_writes_atomically(tmp_path):
    path = tmp_path / "last_regime.json"
    raw = {
        "status": "YELLOW", "regime": "SIDEWAYS", "confidence": 44, "reason": "혼조세",
        "risk_guidance": {"max_positions": 4, "buy_confidence_threshold": 75,
                           "risk_per_trade_pct": 0.35, "min_trading_value_krw": 1_000_000_000},
        "drift_triggers": [], "cooldown_minutes": 60, "max_daily_triggers": 3,
        "risk_notes": [], "opportunity_mode": "NORMAL", "scanner_mode": "TREND",
    }
    judgment = RegimeJudgment(
        status=MarketStatus(raw["status"]), regime=Regime(raw["regime"]),
        confidence=raw["confidence"], reason=raw["reason"],
        risk_guidance=raw["risk_guidance"], drift_triggers=raw["drift_triggers"],
        cooldown_minutes=raw["cooldown_minutes"], max_daily_triggers=raw["max_daily_triggers"],
    )
    save_last_regime(judgment, path=path)

    assert path.exists()
    assert not (tmp_path / "last_regime.json.tmp").exists()
