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


def test_raises_when_one_share_exceeds_risk_budget():
    sizer = PositionSizer()
    # 리스크 예산 = 500원, 1주 손절폭 = 75,000원
    with pytest.raises(ValueError, match="리스크 예산"):
        sizer.calculate(
            ticker="000001",
            entry_price=1000,
            atr=50000,
            total_capital=100_000,
        )


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


def test_fixed_stop_raises_when_one_share_exceeds_risk_budget():
    sizer = PositionSizer()
    with pytest.raises(ValueError, match="리스크 예산"):
        sizer.calculate_from_fixed_stop(
            ticker="005930",
            entry_price=70000,
            stop_loss_price=60000,
            total_capital=100_000,
        )


def test_invalid_stop_raises():
    sizer = PositionSizer()
    with pytest.raises(ValueError):
        sizer.calculate_from_fixed_stop(
            ticker="005930",
            entry_price=70000,
            stop_loss_price=71000,  # 손절가 > 진입가
            total_capital=10_000_000,
        )


def test_flexible_stop_uses_support_and_reduces_quantity():
    sizer = PositionSizer()

    atr_result = sizer.calculate(
        ticker="005930",
        entry_price=10000,
        atr=200,
        total_capital=10_000_000,
    )
    flexible = sizer.calculate_flexible_stop(
        ticker="005930",
        entry_price=10000,
        atr=200,
        total_capital=10_000_000,
        support_stop_price=9300,
    )

    assert flexible.stop_loss_price == 9300
    assert flexible.stop_method == "SUPPORT"
    assert flexible.quantity < atr_result.quantity
    assert flexible.risk_pct <= 0.5


def test_flexible_stop_rejects_too_wide_support():
    sizer = PositionSizer()

    result = sizer.calculate_flexible_stop(
        ticker="005930",
        entry_price=10000,
        atr=200,
        total_capital=10_000_000,
        support_stop_price=8500,
    )

    assert result.stop_loss_price == 9700
    assert result.stop_method == "ATR"
