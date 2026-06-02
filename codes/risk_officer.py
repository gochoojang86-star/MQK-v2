"""
Risk Officer Code - 최종 리스크 통제 권력

Agent는 판단하고, Risk Officer는 거부한다.
이 모듈은 LLM을 사용하지 않는다. 수학만 사용한다.
"""
from dataclasses import dataclass
from typing import Optional
from config.settings import RISK


class RiskViolation(Exception):
    """리스크 규칙 위반 시 raise - 주문 실행 불가"""
    def __init__(self, rule: str, detail: str):
        self.rule = rule
        self.detail = detail
        super().__init__(f"[RISK VIOLATION] {rule}: {detail}")


@dataclass
class PortfolioState:
    total_capital: float           # 총 자본금 (원)
    daily_pnl: float               # 오늘 손익 (원)
    open_positions: list           # 보유 종목 list[dict]
    theme_exposure: dict           # 테마별 비중 {theme: pct}


@dataclass
class TradeProposal:
    ticker: str
    theme: str
    entry_price: float
    stop_loss_price: float
    quantity: int


class RiskOfficer:
    """
    리스크 최종 심판관 - 거부권 보유
    check() 통과 없이는 어떤 주문도 실행되지 않는다
    """

    def __init__(self, config=None):
        self._cfg = config or RISK

    def check(self, proposal: TradeProposal, state: PortfolioState) -> None:
        """
        모든 리스크 규칙을 순서대로 검증한다.
        단 하나라도 위반하면 RiskViolation을 raise한다.
        """
        self._check_averaging_down(proposal, state)
        self._check_daily_loss(state)
        self._check_max_positions(state)
        self._check_single_position_size(proposal, state)
        self._check_theme_exposure(proposal, state)
        self._check_trade_risk(proposal, state)

    # ── 개별 규칙 ──────────────────────────────────────────────────────────────

    def _check_averaging_down(self, proposal: TradeProposal, state: PortfolioState) -> None:
        if not self._cfg.allow_averaging_down:
            for pos in state.open_positions:
                if pos["ticker"] == proposal.ticker:
                    raise RiskViolation(
                        "AVERAGING_DOWN_FORBIDDEN",
                        f"{proposal.ticker} 이미 보유 중. 물타기 금지."
                    )

    def _check_daily_loss(self, state: PortfolioState) -> None:
        daily_loss_pct = (-state.daily_pnl / state.total_capital) * 100
        if daily_loss_pct >= self._cfg.max_daily_loss_pct:
            raise RiskViolation(
                "MAX_DAILY_LOSS",
                f"일일 손실 {daily_loss_pct:.2f}% >= 한도 {self._cfg.max_daily_loss_pct}%. "
                "오늘 신규 매수 불가."
            )

    def _check_max_positions(self, state: PortfolioState) -> None:
        if len(state.open_positions) >= self._cfg.max_positions:
            raise RiskViolation(
                "MAX_POSITIONS",
                f"보유종목 {len(state.open_positions)}개 = 최대 {self._cfg.max_positions}개. "
                "신규 매수 불가."
            )

    def _check_single_position_size(self, proposal: TradeProposal, state: PortfolioState) -> None:
        position_value = proposal.entry_price * proposal.quantity
        position_pct = (position_value / state.total_capital) * 100
        if position_pct > self._cfg.max_single_position_pct:
            raise RiskViolation(
                "MAX_SINGLE_POSITION",
                f"{proposal.ticker} 비중 {position_pct:.1f}% > 한도 {self._cfg.max_single_position_pct}%."
            )

    def _check_theme_exposure(self, proposal: TradeProposal, state: PortfolioState) -> None:
        current_theme_pct = state.theme_exposure.get(proposal.theme, 0.0)
        position_value = proposal.entry_price * proposal.quantity
        additional_pct = (position_value / state.total_capital) * 100
        total_theme_pct = current_theme_pct + additional_pct
        if total_theme_pct > self._cfg.max_theme_exposure_pct:
            raise RiskViolation(
                "MAX_THEME_EXPOSURE",
                f"테마 '{proposal.theme}' 비중 {total_theme_pct:.1f}% > "
                f"한도 {self._cfg.max_theme_exposure_pct}%."
            )

    def _check_trade_risk(self, proposal: TradeProposal, state: PortfolioState) -> None:
        risk_amount = (proposal.entry_price - proposal.stop_loss_price) * proposal.quantity
        risk_pct = (risk_amount / state.total_capital) * 100
        if risk_pct > self._cfg.risk_per_trade_pct:
            raise RiskViolation(
                "RISK_PER_TRADE",
                f"종목 리스크 {risk_pct:.3f}% > 한도 {self._cfg.risk_per_trade_pct}%."
            )

    def get_risk_summary(self, state: PortfolioState) -> dict:
        """현재 리스크 현황 요약 - 로그/모니터링용"""
        daily_loss_pct = (-state.daily_pnl / state.total_capital) * 100 if state.daily_pnl < 0 else 0
        return {
            "daily_loss_pct": round(daily_loss_pct, 3),
            "max_daily_loss_pct": self._cfg.max_daily_loss_pct,
            "open_positions": len(state.open_positions),
            "max_positions": self._cfg.max_positions,
            "theme_exposure": state.theme_exposure,
            "trading_allowed": daily_loss_pct < self._cfg.max_daily_loss_pct
                               and len(state.open_positions) < self._cfg.max_positions,
        }
