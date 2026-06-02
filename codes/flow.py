"""
Flow Code - 수급 분석
외국인/기관/프로그램 매매 동향 계산
LLM 미사용.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class FlowSignals:
    ticker: str
    foreign_net_3d: float       # 외국인 3일 순매수 (원)
    institution_net_3d: float   # 기관 3일 순매수 (원)
    program_net_3d: float       # 프로그램 3일 순매수 (원)
    combined_net: float         # 합산 순매수
    foreign_consecutive_buy: int    # 외국인 연속 순매수일
    institution_consecutive_buy: int
    is_strong_inflow: bool      # 강한 수급 유입 여부
    trading_value_rank: Optional[int]   # 거래대금 순위


@dataclass
class FlowRecord:
    date: str
    ticker: str
    foreign_net: float
    institution_net: float
    program_net: float
    trading_value: float


class FlowAnalysis:
    """수급 분석 엔진"""

    def analyze(self, ticker: str, records: list[FlowRecord]) -> FlowSignals:
        if not records:
            return self._empty_signals(ticker)

        recent_3 = records[-3:]
        foreign_net_3d = sum(r.foreign_net for r in recent_3)
        institution_net_3d = sum(r.institution_net for r in recent_3)
        program_net_3d = sum(r.program_net for r in recent_3)
        combined_net = foreign_net_3d + institution_net_3d

        foreign_consecutive = self._count_consecutive_buy(
            [r.foreign_net for r in records]
        )
        institution_consecutive = self._count_consecutive_buy(
            [r.institution_net for r in records]
        )

        # 강한 수급: 외국인+기관 동반 순매수 3일 이상
        is_strong = (
            foreign_net_3d > 0
            and institution_net_3d > 0
            and foreign_consecutive >= 2
        )

        return FlowSignals(
            ticker=ticker,
            foreign_net_3d=foreign_net_3d,
            institution_net_3d=institution_net_3d,
            program_net_3d=program_net_3d,
            combined_net=combined_net,
            foreign_consecutive_buy=foreign_consecutive,
            institution_consecutive_buy=institution_consecutive,
            is_strong_inflow=is_strong,
            trading_value_rank=None,
        )

    def _count_consecutive_buy(self, net_series: list[float]) -> int:
        """최근부터 역순으로 연속 순매수일 계산"""
        count = 0
        for net in reversed(net_series):
            if net > 0:
                count += 1
            else:
                break
        return count

    def _empty_signals(self, ticker: str) -> FlowSignals:
        return FlowSignals(
            ticker=ticker,
            foreign_net_3d=0,
            institution_net_3d=0,
            program_net_3d=0,
            combined_net=0,
            foreign_consecutive_buy=0,
            institution_consecutive_buy=0,
            is_strong_inflow=False,
            trading_value_rank=None,
        )
