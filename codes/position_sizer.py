"""
Position Sizer Code - 수량/손절폭 계산

Agent는 매수 판단만 한다.
수량 계산과 손절가 확정은 이 모듈의 전권이다.
"""
from dataclasses import dataclass
from config.settings import RISK


@dataclass
class SizingResult:
    ticker: str
    entry_price: float
    stop_loss_price: float
    quantity: int
    risk_amount: float          # 실제 리스크 금액 (원)
    risk_pct: float             # 자본 대비 리스크 비율 (%)
    position_value: float       # 포지션 총가치 (원)
    atr_used: float


class PositionSizer:
    """
    ATR 기반 포지션 사이징
    risk_per_trade_pct=0.5%, atr_multiplier=1.5 고정
    """

    def __init__(self, config=None):
        self._cfg = config or RISK

    def calculate(
        self,
        ticker: str,
        entry_price: float,
        atr: float,
        total_capital: float,
    ) -> SizingResult:
        """
        ATR 기반 수량 계산.

        손절폭 = ATR × atr_multiplier
        수량 = (자본 × risk_per_trade_pct) / 손절폭
        """
        if self._cfg.allow_averaging_down is False:
            pass  # 외부에서 이미 RiskOfficer가 체크

        stop_distance = atr * self._cfg.atr_multiplier
        stop_loss_price = entry_price - stop_distance

        risk_budget = total_capital * (self._cfg.risk_per_trade_pct / 100)
        raw_quantity = risk_budget / stop_distance

        # 주식은 정수 단위, 보수적으로 내림
        quantity = max(1, int(raw_quantity))

        # 내림으로 인한 실제 리스크가 예산을 초과하지 않는지 검증
        actual_risk = stop_distance * quantity
        actual_risk_pct = (actual_risk / total_capital) * 100

        return SizingResult(
            ticker=ticker,
            entry_price=entry_price,
            stop_loss_price=round(stop_loss_price, 0),
            quantity=quantity,
            risk_amount=round(actual_risk, 0),
            risk_pct=round(actual_risk_pct, 4),
            position_value=round(entry_price * quantity, 0),
            atr_used=round(atr, 2),
        )

    def calculate_from_fixed_stop(
        self,
        ticker: str,
        entry_price: float,
        stop_loss_price: float,
        total_capital: float,
    ) -> SizingResult:
        """손절가가 고정된 경우 (차트 지지선 기준)"""
        stop_distance = entry_price - stop_loss_price
        if stop_distance <= 0:
            raise ValueError(f"{ticker}: 손절가({stop_loss_price})가 진입가({entry_price}) 이상")

        risk_budget = total_capital * (self._cfg.risk_per_trade_pct / 100)
        quantity = max(1, int(risk_budget / stop_distance))

        actual_risk = stop_distance * quantity
        actual_risk_pct = (actual_risk / total_capital) * 100
        atr_equivalent = stop_distance / self._cfg.atr_multiplier

        return SizingResult(
            ticker=ticker,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            quantity=quantity,
            risk_amount=round(actual_risk, 0),
            risk_pct=round(actual_risk_pct, 4),
            position_value=round(entry_price * quantity, 0),
            atr_used=round(atr_equivalent, 2),
        )
