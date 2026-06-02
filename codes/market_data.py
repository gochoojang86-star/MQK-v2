"""
Market Data Code - 시장 데이터 수집
LLM 미사용. KIS API / 데이터 소스 연동.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional


@dataclass
class OHLCVBar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    trading_value: float    # 거래대금 (원)


@dataclass
class MarketSnapshot:
    ticker: str
    name: str
    current_price: float
    change_pct: float
    volume: int
    trading_value: float    # 거래대금 (원)
    foreign_net: float      # 외국인 순매수
    institution_net: float  # 기관 순매수
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class IndexStatus:
    kospi: float
    kosdaq: float
    kospi_change_pct: float
    kosdaq_change_pct: float
    market_date: str = field(default_factory=lambda: date.today().isoformat())


class MarketData:
    """
    시장 데이터 수집기.
    실제 데이터 소스(KIS API)와 연동하는 인터페이스를 정의한다.
    broker/kis_api.py를 통해 실제 호출이 이루어진다.
    """

    def __init__(self, data_source=None):
        self._source = data_source  # KISApi 인스턴스 또는 mock

    def get_ohlcv(self, ticker: str, period: int = 60) -> list[OHLCVBar]:
        """일봉 OHLCV 데이터 조회 (최근 period일)"""
        if self._source is None:
            return self._mock_ohlcv(ticker, period)
        return self._source.get_ohlcv(ticker, period)

    def get_snapshot(self, ticker: str) -> MarketSnapshot:
        """현재가 스냅샷 조회"""
        if self._source is None:
            return self._mock_snapshot(ticker)
        return self._source.get_snapshot(ticker)

    def get_index_status(self) -> IndexStatus:
        """지수 현황 조회"""
        if self._source is None:
            return IndexStatus(
                kospi=2500.0, kosdaq=750.0,
                kospi_change_pct=0.5, kosdaq_change_pct=0.8
            )
        return self._source.get_index_status()

    def get_universe(self) -> list[str]:
        """전체 종목 코드 목록 (약 5000종목)"""
        if self._source is None:
            return []
        return self._source.get_universe()

    def save_market_status(self, output_path: Path, index: IndexStatus) -> None:
        """market_status.json 저장"""
        data = {
            "kospi": index.kospi,
            "kosdaq": index.kosdaq,
            "kospi_change_pct": index.kospi_change_pct,
            "kosdaq_change_pct": index.kosdaq_change_pct,
            "market_date": index.market_date,
        }
        output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Mock (개발/테스트용) ──────────────────────────────────────────────────

    def _mock_ohlcv(self, ticker: str, period: int) -> list[OHLCVBar]:
        price = 50000.0
        bars = []
        for i in range(period):
            change = random.uniform(-0.03, 0.03)
            close = price * (1 + change)
            bars.append(OHLCVBar(
                date=f"2026-{(i // 30 + 1):02d}-{(i % 30 + 1):02d}",
                open=price * (1 + random.uniform(-0.01, 0.01)),
                high=max(price, close) * 1.01,
                low=min(price, close) * 0.99,
                close=close,
                volume=random.randint(100000, 1000000),
                trading_value=close * random.randint(100000, 1000000),
            ))
            price = close
        return bars

    def _mock_snapshot(self, ticker: str) -> MarketSnapshot:
        return MarketSnapshot(
            ticker=ticker,
            name=f"종목_{ticker}",
            current_price=50000.0,
            change_pct=1.5,
            volume=500000,
            trading_value=25_000_000_000,
            foreign_net=1_000_000_000,
            institution_net=-500_000_000,
        )
