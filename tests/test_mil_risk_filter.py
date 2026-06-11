"""market_intelligence/risk_filter.py 테스트"""
from market_intelligence.base import MILContext
from market_intelligence.risk_filter import get_stock_status, get_event_schedule


class StubKisApi:
    def __init__(self, raw_responses=None, stock_info=None):
        self._raw_responses = raw_responses or {}
        self._stock_info = stock_info or {}
        self.raw_get_calls = []

    def raw_get(self, tr_id, path, params, mode=None):
        self.raw_get_calls.append((tr_id, path, params))
        return self._raw_responses[tr_id]

    def get_stock_info(self, ticker):
        return self._stock_info


class StubMcpClient:
    @property
    def available(self):
        return False


def make_ctx(**kwargs):
    return MILContext(kis_api=StubKisApi(**kwargs), mcp_client=StubMcpClient())


def test_get_stock_status_detects_vi_triggered():
    ctx = make_ctx(
        raw_responses={
            "FHPST01390000": {"output": [{"mksc_shrn_iscd": "005930"}]},
            "FHPST04830000": {"output": [{"shnu_rate": "3.5"}]},
        },
        stock_info={"trading_halted": False, "administrative_issue": False},
    )
    result = get_stock_status(ctx, "SCAN", ticker="005930")
    assert result["vi_triggered"] is True
    assert result["short_sale_ratio_pct"] == 3.5
    assert result["trading_halted"] is False


def test_get_stock_status_no_vi_for_other_ticker():
    ctx = make_ctx(
        raw_responses={
            "FHPST01390000": {"output": [{"mksc_shrn_iscd": "000660"}]},
            "FHPST04830000": {"output": []},
        },
        stock_info={"trading_halted": True, "administrative_issue": False},
    )
    result = get_stock_status(ctx, "SCAN", ticker="005930")
    assert result["vi_triggered"] is False
    assert result["short_sale_ratio_pct"] == 0.0
    assert result["trading_halted"] is True


def test_get_event_schedule_parses_rights_and_dividend():
    ctx = make_ctx(
        raw_responses={
            "HHKDB669100C0": {
                "output1": [{"record_date": "20260620", "right_dt": "20260618",
                              "sub_term_ft": "20260701", "sub_term": "2026/07/01 ~ 2026/07/02"}],
            },
            "HHKDB669102C0": {
                "output1": [{"record_date": "20260630", "per_sto_divi_amt": "350"}],
            },
        },
    )
    result = get_event_schedule(ctx, "PREMARKET", ticker="005930")
    assert result["rights_events"][0]["record_date"] == "20260620"
    assert result["rights_events"][0]["rights_ex_date"] == "20260618"
    assert result["rights_events"][0]["subscription_start"] == "20260701"
    assert result["dividend_events"][0]["dividend_amount"] == 350.0


def test_get_stock_status_caches_per_ticker():
    ctx = make_ctx(
        raw_responses={
            "FHPST01390000": {"output": []},
            "FHPST04830000": {"output": []},
        },
        stock_info={},
    )
    get_stock_status(ctx, "SCAN", ticker="005930")
    get_stock_status(ctx, "SCAN", ticker="005930")
    assert len(ctx.kis_api.raw_get_calls) == 2  # VI + 공매도, 캐시되어 두 번째 호출은 추가 없음
