"""market_intelligence/screening.py 테스트"""
from market_intelligence.base import MILContext
from market_intelligence.screening import psearch_title, psearch_result, get_top_movers


class StubKisApi:
    def __init__(self, raw_responses=None):
        self._raw_responses = raw_responses or {}
        self.raw_get_calls = []

    def raw_get(self, tr_id, path, params, mode=None):
        self.raw_get_calls.append((tr_id, path, params))
        return self._raw_responses[tr_id]


class StubMcpClient:
    @property
    def available(self):
        return False


def make_ctx(**kwargs):
    return MILContext(kis_api=StubKisApi(**kwargs), mcp_client=StubMcpClient())


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


def test_psearch_result_includes_52week_high_low():
    ctx = make_ctx(
        raw_responses={
            "HHKST03900400": {
                "output2": [
                    {
                        "code": "005930", "name": "삼성전자",
                        "price": "70000", "chgrate": "1.5",
                        "acml_vol": "1000000", "acml_tr_pbmn": "70000000000",
                        "stck_dryy_hgpr": "85000", "stck_dryy_lwpr": "60000",
                        "mrkt_total_amt": "420000000000000",
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
        },
    )
    result = get_top_movers(ctx, "SCAN")
    assert result["movers"][0]["ticker"] == "000660"
    assert result["overheated_bias_warning"] is True


def test_psearch_result_caches_per_seq():
    ctx = make_ctx(
        raw_responses={
            "HHKST03900400": {"output2": []},
        },
    )
    psearch_result(ctx, "SCAN", user_id="test_user", seq="0")
    psearch_result(ctx, "SCAN", user_id="test_user", seq="0")
    assert len(ctx.kis_api.raw_get_calls) == 1
