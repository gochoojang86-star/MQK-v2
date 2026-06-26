"""market_intelligence/screening.py 테스트"""
from market_intelligence.base import MILContext
from market_intelligence.screening import (
    get_program_netbuy_rank,
    get_top_movers,
    psearch_result,
    psearch_title,
)


class StubKisApi:
    def __init__(self, raw_responses=None):
        self._raw_responses = raw_responses or {}
        self.raw_get_calls = []

    def raw_get(self, tr_id, path, params, mode=None):
        self.raw_get_calls.append((tr_id, path, params))
        return self._raw_responses[tr_id]


class StubKiwoomApi:
    available = True

    def __init__(self, program_rows=None):
        self._program_rows = program_rows or {}

    def program_netbuy_top(self, mrkt_tp="P00101"):
        return {"prm_netprps_upper_50": self._program_rows.get(mrkt_tp, [])}


def make_ctx(**kwargs):
    kis = StubKisApi(raw_responses=kwargs.get("raw_responses"))
    kiwoom = kwargs.get("kiwoom_api")
    return MILContext(kis_api=kis, kiwoom_api=kiwoom)


def test_psearch_title_returns_conditions():
    ctx = make_ctx(
        raw_responses={
            "HHKST03900300": {
                "output2": [{"seq": "0", "condition_nm": "SEPA 1차 통과"}],
            },
        },
    )
    result = psearch_title(ctx, "SCAN", user_id="test_user")
    assert result["conditions"] == [{"seq": "0", "name": "SEPA 1차 통과"}]


def test_psearch_result_exposes_error_as_note_not_empty():
    ctx = make_ctx(
        raw_responses={
            "HHKST03900400": {"rt_cd": "1", "msg1": "종목코드 오류입니다."},
        },
    )
    result = psearch_result(ctx, "SCAN", user_id="test_user", seq="2")
    assert result["candidates"] == []
    assert "종목코드 오류" in result["note"]


def test_psearch_result_includes_52week_high_low():
    ctx = make_ctx(
        raw_responses={
            "HHKST03900400": {
                "rt_cd": "0",
                "output2": [
                    {
                        "code": "005930", "name": "삼성전자",
                        "price": "70000", "chgrate": "1.5",
                        "acml_vol": "1000000", "trade_amt": "70000000000",
                        "cttr": "132.5", "chgrate2": "210.0",
                        "high52": "85000", "low52": "60000",
                        "stotprice": "420000000000000",
                    },
                ],
            },
        },
    )
    result = psearch_result(ctx, "SCAN", user_id="test_user", seq="0")
    candidate = result["candidates"][0]
    assert candidate["ticker"] == "005930"
    assert candidate["high_52w"] == 85000.0
    assert candidate["low_52w"] == 60000.0


def test_get_top_movers_includes_overheated_warning():
    ctx = make_ctx(
        raw_responses={
            "FHPST01710000": {
                "output": [
                    {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스",
                     "stck_prpr": "180000", "prdy_ctrt": "5.0", "acml_vol": "5000000"},
                ],
            },
            "FHPST01710000#trading_value": {
                "output": [
                    {"mksc_shrn_iscd": "252670", "hts_kor_isnm": "KODEX 200선물인버스2X",
                     "stck_prpr": "64", "prdy_ctrt": "3.23", "acml_vol": "1428989481", "acml_tr_pbmn": "91488274922"},
                    {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스",
                     "stck_prpr": "180000", "prdy_ctrt": "5.0", "acml_vol": "5000000", "acml_tr_pbmn": "900000000000"},
                ],
            },
            "FHPST01680000": {
                "output": [
                    {"stck_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자",
                     "tday_rltv": "250.5", "prdy_ctrt": "2.0"},
                ],
            },
            "FHPST01700000": {
                "output": [
                    {"stck_shrn_iscd": "465770", "hts_kor_isnm": "STX그린로지스",
                     "prdy_ctrt": "30.00", "acml_vol": "375829", "stck_prpr": "3380"},
                ],
            },
        },
    )
    # 두 번째 FHPST01710000 호출(거래대금 정렬)은 별도 payload를 구분해 스텁 응답 교체
    orig_raw_get = ctx.kis_api.raw_get

    def fake_raw_get(tr_id, path, params, mode=None):
        if tr_id == "FHPST01710000" and params.get("FID_BLNG_CLS_CODE") == "3":
            return ctx.kis_api._raw_responses["FHPST01710000#trading_value"]
        return orig_raw_get(tr_id, path, params, mode)

    ctx.kis_api.raw_get = fake_raw_get
    result = get_top_movers(ctx, "SCAN")
    assert result["movers"][0]["ticker"] == "000660"
    assert result["overheated_bias_warning"] is True
    assert result["trading_value_stock_top"][0]["ticker"] == "000660"
    assert result["excluded_non_equity_count"] == 1
    assert result["volume_power_top"][0]["ticker"] == "005930"
    assert result["volume_power_top"][0]["volume_power"] == 250.5
    assert result["change_rate_top"][0]["ticker"] == "465770"
    assert result["change_rate_top"][0]["trading_value_krw"] == 375829.0 * 3380.0
    assert "missing_fields" not in result


def test_get_top_movers_marks_missing_when_rankings_empty():
    ctx = make_ctx(
        raw_responses={
            "FHPST01710000": {"output": []},
            "FHPST01680000": {"output": []},
            "FHPST01700000": {"output": []},
        },
    )
    result = get_top_movers(ctx, "SCAN")
    assert result["volume_power_top"] == []
    assert result["change_rate_top"] == []
    assert "volume_power_top" in result["missing_fields"]
    assert "change_rate_top" in result["missing_fields"]


def test_psearch_result_caches_per_seq():
    ctx = make_ctx(
        raw_responses={
            "HHKST03900400": {"output2": []},
        },
    )
    psearch_result(ctx, "SCAN", user_id="test_user", seq="0")
    psearch_result(ctx, "SCAN", user_id="test_user", seq="0")
    assert len(ctx.kis_api.raw_get_calls) == 1


def test_get_program_netbuy_rank_filters_non_equity_products_and_sorts():
    ctx = make_ctx(
        kiwoom_api=StubKiwoomApi(
            program_rows={
                "P00101": [
                    {"rank": "1", "stk_cd": "252670", "stk_nm": "KODEX 200선물인버스2X", "cur_prc": "64", "flu_rt": "3.23", "prm_sell_amt": "10", "prm_buy_amt": "12", "prm_netprps_amt": "2", "acc_trde_qty": "1000"},
                    {"rank": "2", "stk_cd": "000660", "stk_nm": "SK하이닉스", "cur_prc": "180000", "flu_rt": "5.0", "prm_sell_amt": "100", "prm_buy_amt": "450", "prm_netprps_amt": "350", "acc_trde_qty": "5000000"},
                ],
                "P10102": [
                    {"rank": "1", "stk_cd": "240810", "stk_nm": "원익IPS", "cur_prc": "33000", "flu_rt": "6.9", "prm_sell_amt": "50", "prm_buy_amt": "260", "prm_netprps_amt": "210", "acc_trde_qty": "1200000"},
                ],
            },
        )
    )
    result = get_program_netbuy_rank(ctx, "SCAN")
    assert [row["ticker"] for row in result["stocks"][:2]] == ["000660", "240810"]
    assert all("KODEX" not in row["name"] for row in result["stocks"])
