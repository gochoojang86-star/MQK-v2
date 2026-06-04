import pytest
from codes.improvement_manager import ImprovementManager
from agents.self_improvement_agent import ImprovementProposal, ChangeType
from config.runtime_overrides import load_runtime_overrides, write_runtime_overrides


def _proposal(title="테스트 제안"):
    return ImprovementProposal(
        title=title,
        hypothesis="승률 개선 가설",
        change_type=ChangeType.FILTER,
        expected_effect="승률 +5%",
        risk="필터 과도 강화",
        requires_backtest=True,
        settings_patch=[
            {"section": "SCANNER", "key": "min_trading_value_krw", "value": 6000000000},
        ],
        auto_apply=False,
    )


def test_save_and_list_proposals(tmp_path):
    mgr = ImprovementManager(db_path=tmp_path / "improve.db")
    mgr.save(_proposal("제안A"))
    mgr.save(_proposal("제안B"))
    pending = mgr.get_pending()
    assert len(pending) == 2
    assert pending[0]["title"] == "제안A"


def test_approve_proposal(tmp_path):
    mgr = ImprovementManager(db_path=tmp_path / "improve.db")
    mgr.save(_proposal())
    pid = mgr.get_pending()[0]["id"]
    mgr.approve(pid)
    assert len(mgr.get_pending()) == 0
    assert mgr.get_approved()[0]["status"] == "APPROVED"


def test_reject_proposal(tmp_path):
    mgr = ImprovementManager(db_path=tmp_path / "improve.db")
    mgr.save(_proposal())
    pid = mgr.get_pending()[0]["id"]
    mgr.reject(pid, reason="효과 불확실")
    assert len(mgr.get_pending()) == 0
    assert mgr.get_approved() == []


def test_approve_writes_runtime_overrides(tmp_path):
    mgr = ImprovementManager(db_path=tmp_path / "improve.db")
    mgr.save(_proposal())
    pid = mgr.get_pending()[0]["id"]
    override_path = tmp_path / "approved_settings.json"

    mgr.approve(pid)
    mgr.apply_approved_settings(path=override_path)

    overrides = load_runtime_overrides(override_path)
    assert overrides["SCANNER"]["min_trading_value_krw"] == 6000000000


def test_runtime_override_helpers_roundtrip(tmp_path):
    path = tmp_path / "approved_settings.json"
    write_runtime_overrides(
        {
            "RISK": {"max_positions": 4},
            "INVALID": {"x": 1},
        },
        path=path,
    )

    overrides = load_runtime_overrides(path)
    assert overrides["RISK"]["max_positions"] == 4
    assert "INVALID" not in overrides
