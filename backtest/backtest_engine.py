"""
Backtest Engine - 전략 검증 엔진
LLM 미사용. 수익률/MDD/승률/손익비 계산.
자기개선 사이클: Self Improvement Agent → Backtest Code → Paper Trade → User Approval
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field


@dataclass
class BacktestTrade:
    ticker: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    quantity: int
    stop_loss_price: float
    exit_reason: str        # STOP_LOSS / TARGET_1 / TARGET_2 / TRAILING / MANUAL

    @property
    def pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.quantity

    @property
    def pnl_pct(self) -> float:
        return (self.exit_price - self.entry_price) / self.entry_price * 100


@dataclass
class BacktestResult:
    strategy_name: str
    total_trades: int
    win_trades: int
    loss_trades: int
    win_rate: float
    total_return_pct: float
    max_drawdown_pct: float
    profit_factor: float
    avg_win_pct: float
    avg_loss_pct: float
    sharpe_ratio: float
    trades: list[BacktestTrade] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"[{self.strategy_name}] "
            f"총 {self.total_trades}건 | "
            f"승률 {self.win_rate:.1f}% | "
            f"수익률 {self.total_return_pct:+.2f}% | "
            f"MDD {self.max_drawdown_pct:.2f}% | "
            f"손익비 {self.profit_factor:.2f}"
        )


class BacktestEngine:
    """전략 백테스트 엔진"""

    def run(
        self,
        strategy_name: str,
        initial_capital: float,
        trades: list[BacktestTrade],
    ) -> BacktestResult:
        """백테스트 실행 및 결과 계산"""
        if not trades:
            return self._empty_result(strategy_name)

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]

        win_rate = len(wins) / len(trades) * 100
        total_pnl = sum(t.pnl for t in trades)
        total_return_pct = total_pnl / initial_capital * 100

        avg_win_pct = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
        avg_loss_pct = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0

        gross_profit = sum(t.pnl for t in wins) if wins else 0
        gross_loss = abs(sum(t.pnl for t in losses)) if losses else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        mdd = self._calculate_mdd(initial_capital, trades)
        sharpe = self._calculate_sharpe(trades)

        return BacktestResult(
            strategy_name=strategy_name,
            total_trades=len(trades),
            win_trades=len(wins),
            loss_trades=len(losses),
            win_rate=round(win_rate, 2),
            total_return_pct=round(total_return_pct, 2),
            max_drawdown_pct=round(mdd, 2),
            profit_factor=round(profit_factor, 2),
            avg_win_pct=round(avg_win_pct, 2),
            avg_loss_pct=round(avg_loss_pct, 2),
            sharpe_ratio=round(sharpe, 2),
            trades=trades,
        )

    def compare(self, results: list[BacktestResult]) -> BacktestResult:
        """여러 전략 비교 - 최고 위험조정수익률 반환"""
        if not results:
            raise ValueError("비교할 백테스트 결과가 없습니다")
        # 샤프 비율 기준 최고 전략 반환
        return max(results, key=lambda r: r.sharpe_ratio)

    def _calculate_mdd(self, initial_capital: float, trades: list[BacktestTrade]) -> float:
        """Maximum Drawdown 계산"""
        equity = initial_capital
        peak = initial_capital
        max_dd = 0.0
        for t in sorted(trades, key=lambda x: x.exit_date):
            equity += t.pnl
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100
            if dd > max_dd:
                max_dd = dd
        return max_dd

    def _calculate_sharpe(self, trades: list[BacktestTrade], risk_free: float = 0.035) -> float:
        """연환산 샤프 비율 (일별 수익률 기준)"""
        if len(trades) < 2:
            return 0.0
        returns = [t.pnl_pct / 100 for t in trades]
        avg_return = statistics.mean(returns)
        std_return = statistics.stdev(returns)
        if std_return == 0:
            return 0.0
        # 연환산 (약 250거래일)
        daily_rf = risk_free / 250
        sharpe = (avg_return - daily_rf) / std_return * (250 ** 0.5)
        return sharpe

    def _empty_result(self, strategy_name: str) -> BacktestResult:
        return BacktestResult(
            strategy_name=strategy_name,
            total_trades=0, win_trades=0, loss_trades=0,
            win_rate=0, total_return_pct=0, max_drawdown_pct=0,
            profit_factor=0, avg_win_pct=0, avg_loss_pct=0, sharpe_ratio=0,
        )
