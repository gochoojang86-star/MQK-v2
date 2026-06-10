"""
Technical Code - 기술적 지표 계산
LLM 미사용. 순수 수학 계산.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Optional

from codes.market_data import OHLCVBar


@dataclass
class TechnicalSignals:
    ticker: str
    atr: float
    rsi: float
    is_vcp: bool                # Volatility Contraction Pattern
    is_box_breakout: bool       # 박스 돌파
    is_pullback: bool           # 눌림목
    ma20: float
    ma60: float
    ma120: float
    above_ma20: bool
    above_ma60: bool
    above_ma120: bool
    new_high_52w: bool          # 52주 신고가
    disparity20_pct: float
    disparity60_pct: float
    disparity120_pct: float


class TechnicalAnalysis:
    """기술적 지표 계산 엔진"""

    def analyze(self, ticker: str, bars: list[OHLCVBar]) -> TechnicalSignals:
        closes = [b.close for b in bars]
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]

        atr = self.calculate_atr(bars)
        rsi = self.calculate_rsi(closes)
        ma20 = self.calculate_ma(closes, 20)
        ma60 = self.calculate_ma(closes, 60)
        ma120 = self.calculate_ma(closes, 120)
        current = closes[-1]

        return TechnicalSignals(
            ticker=ticker,
            atr=atr,
            rsi=rsi,
            is_vcp=self.detect_vcp(bars),
            is_box_breakout=self.detect_box_breakout(bars),
            is_pullback=self.detect_pullback(bars, ma20, ma60),
            ma20=ma20,
            ma60=ma60,
            ma120=ma120,
            above_ma20=current > ma20 if ma20 else False,
            above_ma60=current > ma60 if ma60 else False,
            above_ma120=current > ma120 if ma120 else False,
            new_high_52w=self.is_52w_high(highs),
            disparity20_pct=self.calculate_disparity_pct(current, ma20),
            disparity60_pct=self.calculate_disparity_pct(current, ma60),
            disparity120_pct=self.calculate_disparity_pct(current, ma120),
        )

    def calculate_atr(self, bars: list[OHLCVBar], period: int = 14) -> float:
        """Average True Range"""
        if len(bars) < 2:
            return 0.0
        trs = []
        for i in range(1, len(bars)):
            tr = max(
                bars[i].high - bars[i].low,
                abs(bars[i].high - bars[i - 1].close),
                abs(bars[i].low - bars[i - 1].close),
            )
            trs.append(tr)
        recent = trs[-period:] if len(trs) >= period else trs
        return sum(recent) / len(recent)

    def calculate_rsi(self, closes: list[float], period: int = 14) -> float:
        """Relative Strength Index"""
        if len(closes) < period + 1:
            return 50.0
        changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        recent = changes[-period:]
        gains = [c for c in recent if c > 0]
        losses = [-c for c in recent if c < 0]
        avg_gain = sum(gains) / period if gains else 0
        avg_loss = sum(losses) / period if losses else 0
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)

    def calculate_ma(self, closes: list[float], period: int) -> Optional[float]:
        """단순 이동평균"""
        if len(closes) < period:
            return None
        return sum(closes[-period:]) / period

    def calculate_disparity_pct(self, current: float, ma: Optional[float]) -> float:
        """현재가와 이동평균의 이격도(%)"""
        if not ma:
            return 0.0
        return round(((current - ma) / ma) * 100, 2)

    def detect_vcp(self, bars: list[OHLCVBar], lookback: int = 60) -> bool:
        """
        Volatility Contraction Pattern (Minervini)
        3단계 이상 변동성 수축 + 거래량 감소
        """
        if len(bars) < lookback:
            return False
        recent = bars[-lookback:]
        segments = [recent[i:i+15] for i in range(0, 45, 15)]
        volatilities = []
        for seg in segments:
            highs = [b.high for b in seg]
            lows = [b.low for b in seg]
            vol = (max(highs) - min(lows)) / min(lows) * 100
            volatilities.append(vol)
        # 연속적으로 변동성이 줄어드는지 확인
        is_contracting = all(
            volatilities[i] > volatilities[i + 1]
            for i in range(len(volatilities) - 1)
        )
        # 최근 거래량이 평균보다 낮아야 함
        avg_volume = sum(b.volume for b in recent[:-10]) / max(len(recent) - 10, 1)
        recent_volume = sum(b.volume for b in recent[-10:]) / 10
        low_volume = recent_volume < avg_volume * 0.8
        return is_contracting and low_volume

    def detect_box_breakout(self, bars: list[OHLCVBar], box_period: int = 20) -> bool:
        """
        박스권 돌파 감지
        최근 N일 고점 돌파 + 거래량 급증
        """
        if len(bars) < box_period + 1:
            return False
        box = bars[-(box_period + 1):-1]
        current = bars[-1]
        box_high = max(b.high for b in box)
        avg_volume = sum(b.volume for b in box) / len(box)
        price_breakout = current.close > box_high
        volume_surge = current.volume > avg_volume * 1.5
        return price_breakout and volume_surge

    def detect_pullback(
        self, bars: list[OHLCVBar], ma20: Optional[float], ma60: Optional[float]
    ) -> bool:
        """
        눌림목 감지
        상승 추세 중 20일선 또는 60일선 근처 조정
        """
        if not ma20 or not ma60 or len(bars) < 5:
            return False
        current_close = bars[-1].close
        # 추세 확인: 20MA > 60MA (상승 배열)
        uptrend = ma20 > ma60
        # 눌림: 현재가가 20MA ±3% 이내
        near_ma20 = abs(current_close - ma20) / ma20 < 0.03
        # 최근 하락 후 반등 조짐
        recent_closes = [b.close for b in bars[-5:]]
        recent_low_then_recovery = recent_closes[-1] > min(recent_closes[:-1])
        return uptrend and near_ma20 and recent_low_then_recovery

    def is_52w_high(self, highs: list[float]) -> bool:
        """52주(약 250거래일) 신고가 여부"""
        if len(highs) < 2:
            return False
        period = min(len(highs), 250)
        historical = highs[-period:-1]
        current = highs[-1]
        return current >= max(historical) if historical else False
