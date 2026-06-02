"""
Stop TakeProfit Code - 손절/익절 관리
LLM 미사용. 순수 계산 로직.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ExitSignal(str, Enum):
    STOP_LOSS = "STOP_LOSS"
    TARGET_1 = "TARGET_1"       # 1차 익절
    TARGET_2 = "TARGET_2"       # 2차 익절
    TRAILING_STOP = "TRAILING_STOP"
    HOLD = "HOLD"


@dataclass
class StopTakeProfitConfig:
    target1_ratio: float = 1.5      # 손절폭 대비 1차 목표 (1.5R)
    target2_ratio: float = 3.0      # 손절폭 대비 2차 목표 (3R)
    partial_exit_pct: float = 0.5   # 1차 익절 시 50% 청산
    trailing_activation_ratio: float = 2.0  # 2R 도달 시 트레일링 활성화
    trailing_atr_multiplier: float = 1.0    # 트레일링 손절 ATR 배수


@dataclass
class PositionStatus:
    ticker: str
    entry_price: float
    stop_loss_price: float
    quantity: int
    atr: float
    highest_price: float        # 진입 후 최고가 (트레일링용)
    target1_hit: bool = False
    trailing_active: bool = False
    config: StopTakeProfitConfig = None

    def __post_init__(self):
        if self.config is None:
            self.config = StopTakeProfitConfig()


class StopTakeProfitManager:
    """손절/익절 관리 엔진"""

    def evaluate(self, position: PositionStatus, current_price: float) -> ExitSignal:
        """
        현재가를 기반으로 청산 신호를 반환한다.
        """
        stop_distance = position.entry_price - position.stop_loss_price
        cfg = position.config

        # 손절 확인
        if current_price <= position.stop_loss_price:
            return ExitSignal.STOP_LOSS

        # 트레일링 스탑 확인
        if position.trailing_active:
            trailing_stop = position.highest_price - (position.atr * cfg.trailing_atr_multiplier)
            if current_price <= trailing_stop:
                return ExitSignal.TRAILING_STOP

        # 2차 익절 확인
        target2 = position.entry_price + (stop_distance * cfg.target2_ratio)
        if current_price >= target2 and position.target1_hit:
            return ExitSignal.TARGET_2

        # 1차 익절 확인
        target1 = position.entry_price + (stop_distance * cfg.target1_ratio)
        if current_price >= target1 and not position.target1_hit:
            return ExitSignal.TARGET_1

        return ExitSignal.HOLD

    def update_trailing(self, position: PositionStatus, current_price: float) -> PositionStatus:
        """트레일링 스탑 상태 업데이트"""
        stop_distance = position.entry_price - position.stop_loss_price
        trailing_activation = position.entry_price + (
            stop_distance * position.config.trailing_activation_ratio
        )

        if current_price > position.highest_price:
            position.highest_price = current_price

        if current_price >= trailing_activation:
            position.trailing_active = True

        return position

    def get_targets(self, entry_price: float, stop_loss_price: float,
                    config: Optional[StopTakeProfitConfig] = None) -> dict:
        """목표가 계산"""
        cfg = config or StopTakeProfitConfig()
        stop_distance = entry_price - stop_loss_price
        return {
            "stop_loss": round(stop_loss_price, 0),
            "target1": round(entry_price + stop_distance * cfg.target1_ratio, 0),
            "target2": round(entry_price + stop_distance * cfg.target2_ratio, 0),
            "trailing_activation": round(
                entry_price + stop_distance * cfg.trailing_activation_ratio, 0
            ),
            "risk_reward_1": cfg.target1_ratio,
            "risk_reward_2": cfg.target2_ratio,
        }
