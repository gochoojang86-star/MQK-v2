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
    assert result["kospi"] == 2800.5
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


def test_get_market_context_coerces_string_index_values():
    ctx = make_ctx(
        raw_responses={
            "FHPTJ04400000": {"output2": []},
            "FHPPG04600101": {"output": []},
            "FHPTJ04040000": {"output": []},
        },
        index_status={"kospi": "8342.33", "kosdaq": "1036.59",
                       "kospi_change_pct": "7.45", "kospi_advancers": "782"},
    )
    result = get_market_context(ctx, "INTRADAY")
    assert result["kospi"] == 8342.33
    assert result["kospi_change_pct"] == 7.45
    assert result["kospi_advancers"] == 782


def test_get_sector_breadth_parses_output():
    ctx = make_ctx(
        raw_responses={
            "FHPUP02140000": {
                # 상승/하락 종목수는 output1(기준지수)에만 제공된다
                "output1": {
                    "ascn_issu_cnt": "778", "down_issu_cnt": "114",
                    "stnr_issu_cnt": "19", "uplm_issu_cnt": "2", "lslm_issu_cnt": "0",
                },
                "output2": [
                    {
                        "hts_kor_isnm": "종합", "bstp_cls_code": "0001",
                        "bstp_nmix_prdy_ctrt": "7.59", "acml_vol_rlim": "",
                    },
                    {
                        "hts_kor_isnm": "전기전자", "bstp_cls_code": "0013",
                        "bstp_nmix_prdy_ctrt": "9.57", "acml_vol_rlim": "19.31",
                    },
                ]
            },
        },
    )
    result = get_sector_breadth(ctx, "SCAN")
    assert result["market_breadth"]["advancers"] == 778
    assert result["market_breadth"]["decliners"] == 114
    assert result["market_breadth"]["upper_limit"] == 2
    # 집계 행(0001 종합)은 sectors에서 제외된다
    assert [x["sector_name"] for x in result["sectors"]] == ["전기전자"]
    assert result["sectors"][0]["trading_value_share_pct"] == 19.31


def test_get_intraday_index_candles_parses_output2():
    ctx = make_ctx(
        raw_responses={
            "FHKUP03500200": {
                "output2": [
                    # KIS 응답은 최신 분봉 우선 — 도구가 오름차순으로 정렬해야 한다
                    {"stck_cntg_hour": "100000", "bstp_nmix_oprc": "2800",
                     "bstp_nmix_hgpr": "2810", "bstp_nmix_lwpr": "2795",
                     "bstp_nmix_prpr": "2805", "cntg_vol": "12345"},
                    {"stck_cntg_hour": "090000", "bstp_nmix_oprc": "2790",
                     "bstp_nmix_hgpr": "2801", "bstp_nmix_lwpr": "2788",
                     "bstp_nmix_prpr": "2800", "cntg_vol": "54321"},
                ]
            },
        },
    )
    result = get_intraday_index_candles(ctx, "INTRADAY", index_code="0001")
    assert result["index_code"] == "0001"
    assert result["candles"][0]["time"] == "090000"  # 오름차순: [0]=개장 분봉
    assert result["candles"][0]["open"] == 2790.0
    assert result["candles"][1]["close"] == 2805.0
    assert result["candles"][1]["volume"] == 12345.0


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
