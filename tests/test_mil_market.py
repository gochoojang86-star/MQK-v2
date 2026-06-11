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
            "FHPPG04600101": {
                "output": [
                    {"bsop_hour": "180500", "whol_smtn_ntby_tr_pbmn": "-2380577"},
                    {"bsop_hour": "173600", "whol_smtn_ntby_tr_pbmn": "-2200000"},
                ]
            },
            "FHPTJ04040000": {
                "output": [
                    {"stck_bsop_date": "20260611", "frgn_ntby_tr_pbmn": "-1464017",
                     "orgn_ntby_tr_pbmn": "-756138", "prsn_ntby_tr_pbmn": "2080254"},
                    {"stck_bsop_date": "20260610", "frgn_ntby_tr_pbmn": "-2775389",
                     "orgn_ntby_tr_pbmn": "-2266525", "prsn_ntby_tr_pbmn": "4864291"},
                ]
            },
        },
    )
    result = get_market_context(ctx, "PREMARKET")
    assert result["kospi"] == "2800.50"
    assert result["foreign_net_buy_krw"] == -3000.0
    assert result["institution_net_buy_krw"] == 2000.0
    assert result["prev_kospi_change_pct"] == -8.29
    assert result["program_net_buy_krw"] == -2380577 * 1_000_000
    assert result["investor_trend_days"][0]["date"] == "20260611"
    assert result["investor_trend_days"][0]["foreign_net_krw"] == -1464017 * 1_000_000
    assert "missing_fields" not in result


def test_get_market_context_marks_missing_when_program_and_investor_empty():
    ctx = make_ctx(
        index_status={"kospi": "2800.50", "kospi_change_pct": "0.75",
                       "kosdaq": "850.10", "kosdaq_change_pct": "-0.30",
                       "kospi_advancers": 0, "kospi_decliners": 0,
                       "prev_kospi_change_pct": 0, "prev_kospi_trading_value": 0,
                       "prev_kosdaq_change_pct": 0, "prev_kosdaq_trading_value": 0},
        raw_responses={
            "FHPTJ04400000": {"output2": []},
            "FHPPG04600101": {"output": []},
            "FHPTJ04040000": {"output": []},
        },
    )
    result = get_market_context(ctx, "SCAN")
    assert result["program_net_buy_krw"] is None
    assert result["investor_trend_days"] == []
    assert "program_net_buy_krw" in result["missing_fields"]
    assert "investor_trend_days" in result["missing_fields"]


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
        raw_responses={
            "FHPTJ04400000": {"output2": []},
            "FHPPG04600101": {"output": []},
            "FHPTJ04040000": {"output": []},
        },
    )
    get_market_context(ctx, "SCAN")
    get_market_context(ctx, "SCAN")
    assert len(ctx.kis_api.raw_get_calls) == 3
