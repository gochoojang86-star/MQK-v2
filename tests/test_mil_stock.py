"""market_intelligence/stock.py 테스트"""
from market_intelligence.base import MILContext
from market_intelligence.stock import (
    get_ohlcv,
    get_realtime_price,
    get_intraday_candles,
    get_flow,
    get_news_stock,
)


class StubKisApi:
    def __init__(self, raw_responses=None):
        self._raw_responses = raw_responses or {}
        self.raw_get_calls = []

    def raw_get(self, tr_id, path, params, mode=None):
        self.raw_get_calls.append((tr_id, path, params, mode))
        return self._raw_responses[tr_id]


class StubMcpClient:
    @property
    def available(self):
        return False


def make_ctx(**kwargs):
    return MILContext(kis_api=StubKisApi(**kwargs), mcp_client=StubMcpClient())


def test_get_ohlcv_returns_output1_valuation_and_output2_candles():
    ctx = make_ctx(
        raw_responses={
            "FHKST03010100": {
                "output1": {
                    "stck_prpr": "70000", "askp": "70100", "bidp": "69900",
                    "per": "12.5", "eps": "5600", "pbr": "1.2",
                    "hts_avls": "420000000000000",
                    "stck_mxpr": "91000", "stck_llam": "49000",
                },
                "output2": [
                    {"stck_bsop_date": "20260609", "stck_oprc": "69500", "stck_hgpr": "70200",
                     "stck_lwpr": "69300", "stck_clpr": "70000", "acml_vol": "1000000",
                     "acml_tr_pbmn": "70000000000", "flng_cls_code": "00"},
                ],
            },
        },
    )
    result = get_ohlcv(ctx, "SCAN", ticker="005930", period=60)
    assert result["current_price"] == 70000.0
    assert result["per"] == 12.5
    assert result["candles"][0]["close"] == 70000.0
    assert result["candles"][0]["rights_event_code"] == "00"


def test_get_realtime_price_batches_tickers():
    ctx = make_ctx(
        raw_responses={
            "FHKST11300006": {
                "output": [
                    {"inter_shrn_iscd": "005930", "inter2_prpr": "70000", "prdy_ctrt": "1.0", "acml_vol": "100"},
                    {"inter_shrn_iscd": "000660", "inter2_prpr": "180000", "prdy_ctrt": "5.0", "acml_vol": "200"},
                ],
            },
        },
    )
    result = get_realtime_price(ctx, "INTRADAY", tickers=["005930", "000660"])
    assert result["prices"][0]["ticker"] == "005930"
    assert result["prices"][1]["price"] == 180000.0


def test_get_realtime_price_rejects_more_than_30_tickers():
    import pytest
    from market_intelligence.base import ToolFailure

    ctx = make_ctx(raw_responses={})
    with pytest.raises(ToolFailure):
        get_realtime_price(ctx, "INTRADAY", tickers=[f"{i:06d}" for i in range(31)])


def test_get_intraday_candles_parses_minute_bars():
    ctx = make_ctx(
        raw_responses={
            "FHKST03010200": {
                "output2": [
                    {"stck_cntg_hour": "093000", "stck_oprc": "69800", "stck_hgpr": "70000",
                     "stck_lwpr": "69700", "stck_prpr": "69900", "cntg_vol": "5000"},
                ],
            },
        },
    )
    result = get_intraday_candles(ctx, "INTRADAY", ticker="005930")
    assert result["candles"][0]["close"] == 69900.0


def test_get_flow_parses_investor_breakdown():
    ctx = make_ctx(
        raw_responses={
            "FHPTJ04160001": {
                "output": [
                    {"stck_bsop_date": "20260609", "stck_clpr": "70000",
                     "frgn_ntby_qty": "-10000", "orgn_ntby_qty": "5000",
                     "prsn_ntby_qty": "5000", "invtrt_ntby_qty": "1000",
                     "prvt_fund_ntby_qty": "500", "bank_ntby_qty": "100",
                     "insu_ntby_qty": "200", "pe_fund_ntby_qty": "300"},
                ],
            },
        },
    )
    result = get_flow(ctx, "SCAN", ticker="005930")
    assert result["days"][0]["foreign_net_qty"] == -10000.0
    assert result["days"][0]["institution_net_qty"] == 5000.0


def test_get_news_stock_filters_by_ticker():
    ctx = make_ctx(
        raw_responses={
            "FHKST01011800": {
                "output": [
                    {"hts_pbnt_titl_cntt": "삼성전자 신규 수주", "data_dt": "20260609", "data_tm": "100000"},
                ],
            },
        },
    )
    result = get_news_stock(ctx, "SCAN", ticker="005930")
    assert result["headlines"][0]["title"] == "삼성전자 신규 수주"


def test_get_ohlcv_caches_per_ticker_and_period():
    ctx = make_ctx(
        raw_responses={
            "FHKST03010100": {"output1": {}, "output2": []},
        },
    )
    get_ohlcv(ctx, "SCAN", ticker="005930", period=60)
    get_ohlcv(ctx, "SCAN", ticker="005930", period=60)
    assert len(ctx.kis_api.raw_get_calls) == 1
