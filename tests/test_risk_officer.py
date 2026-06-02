"""Risk Officer Code 테스트"""
import pytest
from codes.risk_officer import RiskOfficer, RiskViolation, PortfolioState, TradeProposal
from config.settings import RiskConfig


def make_state(**kwargs):
    defaults = {
        "total_capital": 10_000_000,
        "daily_pnl": 0,
        "open_positions": [],
        "theme_exposure": {},
    }
    defaults.update(kwargs)
    return PortfolioState(**defaults)


def make_proposal(**kwargs):
    defaults = {
        "ticker": "005930",
        "theme": "반도체",
        "entry_price": 70000,
        "stop_loss_price": 67000,
        "quantity": 10,
    }
    defaults.update(kwargs)
    return TradeProposal(**defaults)


def test_clean_trade_passes():
    officer = RiskOfficer()
    proposal = make_proposal()
    state = make_state()
    officer.check(proposal, state)  # 예외 없어야 함


def test_averaging_down_blocked():
    officer = RiskOfficer()
    proposal = make_proposal(ticker="005930")
    state = make_state(open_positions=[{"ticker": "005930", "quantity": 5}])
    with pytest.raises(RiskViolation) as exc_info:
        officer.check(proposal, state)
    assert exc_info.value.rule == "AVERAGING_DOWN_FORBIDDEN"


def test_daily_loss_limit():
    officer = RiskOfficer()
    proposal = make_proposal()
    # 일일 손실 2% 초과: 10M * 2% = 200,000
    state = make_state(daily_pnl=-200_001)
    with pytest.raises(RiskViolation) as exc_info:
        officer.check(proposal, state)
    assert exc_info.value.rule == "MAX_DAILY_LOSS"


def test_max_positions_blocked():
    officer = RiskOfficer()
    proposal = make_proposal()
    positions = [
        {"ticker": f"A{i}", "quantity": 10} for i in range(5)
    ]
    state = make_state(open_positions=positions)
    with pytest.raises(RiskViolation) as exc_info:
        officer.check(proposal, state)
    assert exc_info.value.rule == "MAX_POSITIONS"


def test_theme_exposure_blocked():
    officer = RiskOfficer()
    # 반도체 테마 이미 40% 노출
    proposal = make_proposal(
        theme="반도체",
        entry_price=100000,
        stop_loss_price=97000,
        quantity=10,  # 1,000,000원 = 10% 추가
    )
    state = make_state(
        total_capital=10_000_000,
        theme_exposure={"반도체": 35.0},  # 35% + 10% = 45% > 40%
    )
    with pytest.raises(RiskViolation) as exc_info:
        officer.check(proposal, state)
    assert exc_info.value.rule == "MAX_THEME_EXPOSURE"


def test_risk_per_trade_blocked():
    officer = RiskOfficer()
    # position: 70000 * 10 = 700,000 = 7% (MAX_SINGLE_POSITION 통과)
    # risk: (70000 - 60000) * 10 = 100,000 = 1.0% > 0.5%
    proposal = make_proposal(
        entry_price=70000,
        stop_loss_price=60000,
        quantity=10,
    )
    state = make_state(total_capital=10_000_000)
    with pytest.raises(RiskViolation) as exc_info:
        officer.check(proposal, state)
    assert exc_info.value.rule == "RISK_PER_TRADE"
