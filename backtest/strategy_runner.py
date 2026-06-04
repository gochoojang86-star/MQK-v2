"""
Strategy Runner - 과거 OHLCV로 전략 시뮬레이션
BacktestEngine에 넘길 BacktestTrade 리스트 생성. LLM 미사용.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from codes.market_data import OHLCVBar
from backtest.backtest_engine import BacktestTrade


@dataclass
class StrategyConfig:
    entry_condition: str = "new_high"   # "new_high" | "vcp"
    stop_atr_multiplier: float = 1.5
    target1_ratio: float = 1.5
    target2_ratio: float = 3.0
    risk_per_trade_pct: float = 0.5


class StrategyRunner:
    """단순 신고가 전략 시뮬레이터."""

    def __init__(self, config: StrategyConfig | None = None):
        self._cfg = config or StrategyConfig()

    def run(
        self,
        ticker: str,
        bars: list[OHLCVBar],
        initial_capital: float,
    ) -> list[BacktestTrade]:
        if len(bars) < 20:
            return []
        trades: list[BacktestTrade] = []
        capital = initial_capital
        in_position = False
        entry_bar: OHLCVBar | None = None
        stop_price = 0.0
        t1_price = 0.0
        t2_price = 0.0
        quantity = 0

        for i, bar in enumerate(bars):
            if i < 20:
                continue
            atr = self._atr(bars[max(0, i - 14):i])
            if not in_position:
                if self._is_entry(bars, i):
                    stop_distance = atr * self._cfg.stop_atr_multiplier
                    if stop_distance <= 0:
                        continue
                    stop_price = bar.close - stop_distance
                    risk_budget = capital * (self._cfg.risk_per_trade_pct / 100)
                    qty = int(risk_budget / stop_distance)
                    if qty < 1:
                        continue
                    quantity = qty
                    t1_price = bar.close + stop_distance * self._cfg.target1_ratio
                    t2_price = bar.close + stop_distance * self._cfg.target2_ratio
                    entry_bar = bar
                    in_position = True
            else:
                assert entry_bar is not None
                exit_reason: str | None = None
                exit_price = bar.close
                if bar.low <= stop_price:
                    exit_reason, exit_price = "STOP_LOSS", stop_price
                elif bar.high >= t2_price:
                    exit_reason, exit_price = "TARGET_2", t2_price
                elif bar.high >= t1_price:
                    exit_reason, exit_price = "TARGET_1", t1_price
                if exit_reason:
                    pnl = (exit_price - entry_bar.close) * quantity
                    capital += pnl
                    trades.append(BacktestTrade(
                        ticker=ticker,
                        entry_date=entry_bar.date,
                        exit_date=bar.date,
                        entry_price=entry_bar.close,
                        exit_price=exit_price,
                        quantity=quantity,
                        stop_loss_price=stop_price,
                        exit_reason=exit_reason,
                    ))
                    in_position = False
        return trades

    def _is_entry(self, bars: list[OHLCVBar], i: int) -> bool:
        recent_high = max(b.high for b in bars[i - 20:i])
        return bars[i].close >= recent_high

    @staticmethod
    def _atr(bars: list[OHLCVBar]) -> float:
        if not bars:
            return 1.0
        trs = [b.high - b.low for b in bars]
        return statistics.mean(trs) if trs else 1.0
