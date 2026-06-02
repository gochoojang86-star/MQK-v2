"""Position Sizer Code 테스트"""
import pytest
from codes.position_sizer import PositionSizer


def test_basic_sizing():
    sizer = PositionSizer()
    result = sizer.calculate(
        ticker="005930",
        entry_price=70000,
        atr=2000,
        total_capital=10_000_000,
    )
    # 손절폭 = 2000 * 1.5 = 3000
    # 리스크 예산 = 10M * 0.5% = 50,000
    # 수량 = 50000 / 3000 = 16
    assert result.quantity == 16
    assert result.stop_loss_price == 70000 - 3000
    assert result.risk_pct <= 0.5


def test_quantity_at_least_one():
    sizer = PositionSizer()
    # 매우 큰 ATR로 수량이 0이 되는 경우 → 1로 보정
    result = sizer.calculate(
        ticker="000001",
        entry_price=1000,
        atr=50000,
        total_capital=100_000,
    )
    assert result.quantity >= 1


def test_fixed_stop_sizing():
    sizer = PositionSizer()
    result = sizer.calculate_from_fixed_stop(
        ticker="005930",
        entry_price=70000,
        stop_loss_price=69000,
        total_capital=10_000_000,
    )
    assert result.quantity >= 1
    assert result.stop_loss_price == 69000


def test_invalid_stop_raises():
    sizer = PositionSizer()
    with pytest.raises(ValueError):
        sizer.calculate_from_fixed_stop(
            ticker="005930",
            entry_price=70000,
            stop_loss_price=71000,  # 손절가 > 진입가
            total_capital=10_000_000,
        )
