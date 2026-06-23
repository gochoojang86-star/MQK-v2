"""MQKOrchestratorV4 phase 테스트"""
import pytest
from unittest.mock import MagicMock, patch
from orchestrator_v4 import MQKOrchestratorV4


class StubKisApi:
    def get_balance(self):
        return {"output2": [{"ord_psbl_cash": "50000000"}]}


def _make_orch():
    return MQKOrchestratorV4(kis_api=StubKisApi())


def test_orchestrator_v4_instantiates():
    orch = _make_orch()
    assert orch is not None


def test_run_premarket_sejuk_returns_dict(monkeypatch):
    orch = _make_orch()
    monkeypatch.setattr(
        "orchestrator_v4.MQKOrchestratorV4._run_agent",
        lambda self, phase, ctx: {"action": "WATCHLIST_UPDATE", "watchlist": [], "candidates": []},
    )
    result = orch.run_premarket_sejuk_v4()
    assert isinstance(result, dict)
    assert "watchlist" in result


def test_run_intraday_v4_skips_when_no_regime(monkeypatch):
    orch = _make_orch()
    monkeypatch.setattr("orchestrator_v4.load_last_regime", lambda path: None)
    result = orch.run_intraday_v4()
    assert result.get("action") == "NO_TRADE"
    assert "stale_regime" in result.get("reason", "")


def test_max_positions_is_3():
    from orchestrator_v4 import MAX_POSITIONS_V4
    assert MAX_POSITIONS_V4 == 3
