"""market_intelligence/market.py 테스트"""
from market_intelligence.base import MILContext
from market_intelligence.market import (
    get_market_context,
    get_sector_breadth,
    get_intraday_index_candles,
    get_news_market,
)


class StubKisApi:
    def __init__(self, raw_responses=None, index_status=None):
        self._raw_responses = raw_responses or {}
        self._index_status = index_status or {}
        self.raw_get_calls = []

    def get_index_status(self):
        return self._index_status

    def raw_get(self, tr_id, path, params, mode=None):
        self.raw_get_calls.append((tr_id, path, params))
        return self._raw_responses[tr_id]


class StubMcpClient:
    @property
    def available(self):
        return False


def make_ctx(**kwargs):
    return MILContext(kis_api=StubKisApi(**kwargs), mcp_client=StubMcpClient())


def test_get_market_context_combines_index_and_flow():
    ctx = make_ctx(
        index_status={
            "kospi": "2800.50", "kospi_change_pct": "0.75",
            "kosdaq": "850.10", "kosdaq_change_pct": "-0.30",
            "kospi_advancers": 500, "kospi_decliners": 350,
            "prev_kospi_change_pct": -8.29, "prev_kospi_trading_value": 48338891000000.0,
            "prev_kosdaq_change_pct": -9.08, "prev_kosdaq_trading_value": 8929291000000.0,
        },
        raw_responses={
            "FHPTJ04400000": {
                "output2": [
                    {"frgn_ntby_tr_pbmn": "-1000", "orgn_ntby_tr_pbmn": "500"},
                    {"frgn_ntby_tr_pbmn": "-2000", "orgn_ntby_tr_pbmn": "1500"},
                ]
            },
        },
    )
    result = get_market_context(ctx, "PREMARKET")
    assert result["kospi"] == "2800.50"
    assert result["foreign_net_buy_krw"] == -3000.0
    assert result["institution_net_buy_krw"] == 2000.0
    assert result["prev_kospi_change_pct"] == -8.29


def test_get_sector_breadth_parses_output():
    ctx = make_ctx(
        raw_responses={
            "FHPUP02140000": {
                "output2": [
                    {
                        "hts_kor_isnm": "전기전자", "bstp_cls_code": "031",
                        "bstp_nmix_prdy_ctrt": "1.20",
                        "ascn_issu_cnt": "120", "down_issu_cnt": "60",
                        "stnr_issu_cnt": "10", "uplm_issu_cnt": "2", "lslm_issu_cnt": "0",
                    },
                ]
            },
        },
    )
    result = get_sector_breadth(ctx, "SCAN")
    assert result["sectors"][0]["sector_name"] == "전기전자"
    assert result["sectors"][0]["advancers"] == 120
    assert result["sectors"][0]["upper_limit"] == 2


def test_get_intraday_index_candles_parses_output2():
    ctx = make_ctx(
        raw_responses={
            "FHKUP03500200": {
                "output2": [
                    {"stck_cntg_hour": "100000", "bstp_nmix_oprc": "2800",
                     "bstp_nmix_hgpr": "2810", "bstp_nmix_lwpr": "2795",
                     "bstp_nmix_prpr": "2805", "acml_vol": "12345"},
                ]
            },
        },
    )
    result = get_intraday_index_candles(ctx, "INTRADAY", index_code="0001")
    assert result["index_code"] == "0001"
    assert result["candles"][0]["close"] == 2805.0


def test_get_news_market_parses_headlines():
    ctx = make_ctx(
        raw_responses={
            "FHKST01011800": {
                "output": [
                    {"hts_pbnt_titl_cntt": "코스피 급락", "data_dt": "20260609", "data_tm": "090500"},
                ]
            },
        },
    )
    result = get_news_market(ctx, "PREMARKET")
    assert result["headlines"][0]["title"] == "코스피 급락"


def test_get_market_context_caches_second_call():
    ctx = make_ctx(
        index_status={"kospi": "2800.50", "kospi_change_pct": "0.75",
                       "kosdaq": "850.10", "kosdaq_change_pct": "-0.30",
                       "kospi_advancers": 0, "kospi_decliners": 0,
                       "prev_kospi_change_pct": 0, "prev_kospi_trading_value": 0,
                       "prev_kosdaq_change_pct": 0, "prev_kosdaq_trading_value": 0},
        raw_responses={"FHPTJ04400000": {"output2": []}},
    )
    get_market_context(ctx, "SCAN")
    get_market_context(ctx, "SCAN")
    assert len(ctx.kis_api.raw_get_calls) == 1
