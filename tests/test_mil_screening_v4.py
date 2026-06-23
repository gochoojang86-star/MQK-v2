"""get_limit_up_stocks 테스트"""
import pytest
from unittest.mock import MagicMock
from market_intelligence.base import MILContext, ToolFailure
from market_intelligence.screening import get_limit_up_stocks


class StubKisApi:
    def __init__(self, rows):
        self._rows = rows

    def raw_get(self, tr_id, path, params):
        return {"output": self._rows}


def _make_ctx(rows):
    return MILContext(kis_api=StubKisApi(rows))


def test_get_limit_up_stocks_filters_above_25pct():
    rows = [
        {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스",
         "prdy_ctrt": "29.90", "acml_tr_pbmn": "500000000000", "stck_prpr": "100000"},
        {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자",
         "prdy_ctrt": "10.00", "acml_tr_pbmn": "300000000000", "stck_prpr": "80000"},
        {"mksc_shrn_iscd": "035420", "hts_kor_isnm": "NAVER",
         "prdy_ctrt": "27.00", "acml_tr_pbmn": "100000000000", "stck_prpr": "200000"},
    ]
    ctx = _make_ctx(rows)
    result = get_limit_up_stocks(ctx, "PREMARKET_SEJUK")
    stocks = result["stocks"]
    # 25% 이상만 포함 (000660: 29.9%, NAVER: 27%)
    assert len(stocks) == 2
    tickers = [s["ticker"] for s in stocks]
    assert "000660" in tickers
    assert "035420" in tickers
    assert "005930" not in tickers


def test_get_limit_up_stocks_is_limit_up_flag():
    rows = [
        {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스",
         "prdy_ctrt": "29.90", "acml_tr_pbmn": "500000000000", "stck_prpr": "100000"},
        {"mksc_shrn_iscd": "035420", "hts_kor_isnm": "NAVER",
         "prdy_ctrt": "26.00", "acml_tr_pbmn": "100000000000", "stck_prpr": "200000"},
    ]
    ctx = _make_ctx(rows)
    result = get_limit_up_stocks(ctx, "PREMARKET_SEJUK")
    stocks = {s["ticker"]: s for s in result["stocks"]}
    assert stocks["000660"]["is_limit_up"] is True   # 29.9% → 상한가
    assert stocks["035420"]["is_limit_up"] is False  # 26% → 상한가 근접


def test_get_limit_up_stocks_empty_when_no_rows():
    ctx = _make_ctx([])
    result = get_limit_up_stocks(ctx, "PREMARKET_SEJUK")
    assert result["stocks"] == []


def test_get_limit_up_stocks_uses_cache(monkeypatch):
    calls = []
    rows = [{"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK",
             "prdy_ctrt": "29.9", "acml_tr_pbmn": "100000000000", "stck_prpr": "100000"}]
    ctx = _make_ctx(rows)
    original_raw_get = ctx.kis_api.raw_get
    def counting_raw_get(*args, **kwargs):
        calls.append(1)
        return original_raw_get(*args, **kwargs)
    ctx.kis_api.raw_get = counting_raw_get

    get_limit_up_stocks(ctx, "PREMARKET_SEJUK")
    get_limit_up_stocks(ctx, "PREMARKET_SEJUK")
    assert len(calls) == 1  # 캐시 적중
