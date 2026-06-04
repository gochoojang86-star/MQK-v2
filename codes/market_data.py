"""
Market Data Code - 시장 데이터 수집
LLM 미사용. KIS API / 데이터 소스 연동.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional


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
    market_cap: float = 0.0 # 시가총액 (원)
    sector: str = ""        # 업종명 (bstp_kor_isnm)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class IndexStatus:
    kospi: float
    kosdaq: float
    kospi_change_pct: float
    kosdaq_change_pct: float
    market_date: str = field(default_factory=lambda: date.today().isoformat())


class MarketDataSourceRequired(RuntimeError):
    """Raised when production market data is requested without a real data source."""


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
            raise MarketDataSourceRequired("MarketData requires a real data_source for get_ohlcv().")
        return [self._coerce_ohlcv_bar(row) for row in self._source.get_ohlcv(ticker, period)]

    def get_snapshot(self, ticker: str) -> MarketSnapshot:
        """현재가 스냅샷 조회"""
        if self._source is None:
            raise MarketDataSourceRequired("MarketData requires a real data_source for get_snapshot().")
        return self._coerce_snapshot(ticker, self._source.get_snapshot(ticker))

    def get_index_status(self) -> IndexStatus:
        """지수 현황 조회"""
        if self._source is None:
            raise MarketDataSourceRequired("MarketData requires a real data_source for get_index_status().")
        return self._coerce_index_status(self._source.get_index_status())

    def get_universe(self) -> list[str]:
        """전체 종목 코드 목록 (약 5000종목)"""
        if self._source is None:
            raise MarketDataSourceRequired("MarketData requires a real data_source for get_universe().")
        raw = self._source.get_universe()
        result = []
        for item in raw:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                code = item.get("ticker") or item.get("code") or item.get("pdno") or item.get("mksc_shrn_iscd")
                if code:
                    result.append(str(code))
        return result

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

    def _coerce_ohlcv_bar(self, row: Any) -> OHLCVBar:
        if isinstance(row, OHLCVBar):
            return row
        if not isinstance(row, dict):
            raise TypeError(f"Unsupported OHLCV row type: {type(row).__name__}")
        return OHLCVBar(
            date=str(row.get("date") or row.get("stck_bsop_date") or ""),
            open=self._to_float(row.get("open") or row.get("stck_oprc")),
            high=self._to_float(row.get("high") or row.get("stck_hgpr")),
            low=self._to_float(row.get("low") or row.get("stck_lwpr")),
            close=self._to_float(row.get("close") or row.get("stck_clpr")),
            volume=self._to_int(row.get("volume") or row.get("acml_vol")),
            trading_value=self._to_float(row.get("trading_value") or row.get("acml_tr_pbmn")),
        )

    def _coerce_snapshot(self, ticker: str, row: Any) -> MarketSnapshot:
        if isinstance(row, MarketSnapshot):
            return row
        if not isinstance(row, dict):
            raise TypeError(f"Unsupported snapshot type: {type(row).__name__}")
        return MarketSnapshot(
            ticker=str(row.get("ticker") or row.get("mksc_shrn_iscd") or ticker),
            name=str(row.get("name") or row.get("hts_kor_isnm") or row.get("prdt_abrv_name") or ticker),
            current_price=self._to_float(row.get("current_price") or row.get("stck_prpr")),
            change_pct=self._to_float(row.get("change_pct") or row.get("prdy_ctrt")),
            volume=self._to_int(row.get("volume") or row.get("acml_vol")),
            trading_value=self._to_float(row.get("trading_value") or row.get("acml_tr_pbmn")),
            foreign_net=self._to_float(row.get("foreign_net") or row.get("frgn_ntby_qty")),
            institution_net=self._to_float(row.get("institution_net") or row.get("orgn_ntby_qty")),
            market_cap=self._to_float(row.get("market_cap") or row.get("hts_avls") or row.get("stck_avls")),
            sector=str(row.get("sector") or row.get("bstp_kor_isnm") or ""),
        )

    def _coerce_index_status(self, row: Any) -> IndexStatus:
        if isinstance(row, IndexStatus):
            return row
        if not isinstance(row, dict):
            raise TypeError(f"Unsupported index status type: {type(row).__name__}")
        return IndexStatus(
            kospi=self._to_float(row.get("kospi")),
            kosdaq=self._to_float(row.get("kosdaq")),
            kospi_change_pct=self._to_float(row.get("kospi_change_pct")),
            kosdaq_change_pct=self._to_float(row.get("kosdaq_change_pct")),
        )

    def _to_float(self, value) -> float:
        if value in (None, ""):
            return 0.0
        return float(str(value).replace(",", ""))

    def _to_int(self, value) -> int:
        if value in (None, ""):
            return 0
        return int(float(str(value).replace(",", "")))
