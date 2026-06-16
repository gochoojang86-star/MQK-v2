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


def make_ctx(**kwargs):
    return MILContext(kis_api=StubKisApi(**kwargs))


def test_get_stock_status_detects_vi_triggered():
    ctx = make_ctx(
        raw_responses={
            "FHPST01390000": {"output": [{"mksc_shrn_iscd": "005930"}]},
            "FHPST04830000": {"output": [{"shnu_rate": "3.5"}]},
            "FHKST130000C0": {"output": [{"mksc_shrn_iscd": "005930"}]},
        },
        stock_info={"trading_halted": False, "administrative_issue": False},
    )
    result = get_stock_status(ctx, "SCAN", ticker="005930")
    assert result["vi_triggered"] is True
    assert result["short_sale_ratio_pct"] == 3.5
    assert result["trading_halted"] is False
    assert result["is_limit_up"] is True
    assert result["is_limit_down"] is True  # 같은 stub 응답을 양쪽 side에 사용


def test_get_stock_status_no_vi_for_other_ticker():
    ctx = make_ctx(
        raw_responses={
            "FHPST01390000": {"output": [{"mksc_shrn_iscd": "000660"}]},
            "FHPST04830000": {"output": []},
            "FHKST130000C0": {"output": []},
        },
        stock_info={"trading_halted": True, "administrative_issue": False},
    )
    result = get_stock_status(ctx, "SCAN", ticker="005930")
    assert result["vi_triggered"] is False
    assert result["short_sale_ratio_pct"] == 0.0
    assert result["trading_halted"] is True
    assert result["is_limit_up"] is False
    assert result["is_limit_down"] is False


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
            "HHKDB669101C0": {
                "output1": [{"record_date": "20260814", "right_dt": "20260813",
                              "fix_rate": "10.00", "list_date": ""}],
            },
            "HHKDB669104C0": {
                "output1": [{"record_date": "20260710", "merge_type": "주식교환",
                              "merge_rate": "1.00", "list_dt": ""}],
            },
            "HHKDB669111C0": {
                "output1": [{"record_date": "20260730", "gen_meet_dt": "2026/08/21",
                              "gen_meet_type": "임시총회", "agenda": "합병승인"}],
            },
        },
    )
    result = get_event_schedule(ctx, "PREMARKET", ticker="005930")
    assert result["rights_events"][0]["record_date"] == "20260620"
    assert result["rights_events"][0]["rights_ex_date"] == "20260618"
    assert result["rights_events"][0]["subscription_start"] == "20260701"
    assert result["dividend_events"][0]["dividend_amount"] == 350.0
    assert result["bonus_issue_events"][0]["record_date"] == "20260814"
    assert result["bonus_issue_events"][0]["fix_rate"] == 10.0
    assert result["merger_split_events"][0]["merge_type"] == "주식교환"
    assert result["shareholder_meeting_events"][0]["meeting_date"] == "2026/08/21"
    assert result["shareholder_meeting_events"][0]["agenda"] == "합병승인"
    assert "missing_fields" not in result


def test_get_event_schedule_isolates_failures():
    ctx = make_ctx(
        raw_responses={
            "HHKDB669100C0": {"output1": [{"record_date": "20260620", "right_dt": "20260618",
                                            "sub_term_ft": "20260701", "sub_term": ""}]},
            "HHKDB669102C0": {"output1": [{"record_date": "20260630", "per_sto_divi_amt": "350"}]},
            # bonus-issue, merger-split, sharehld-meet TR ID 누락 → KeyError 발생 → guarded
        },
    )
    result = get_event_schedule(ctx, "PREMARKET", ticker="005930")
    assert result["rights_events"][0]["record_date"] == "20260620"
    assert result["dividend_events"][0]["dividend_amount"] == 350.0
    assert result["bonus_issue_events"] == []
    assert result["merger_split_events"] == []
    assert result["shareholder_meeting_events"] == []
    assert set(result["missing_fields"]) == {
        "bonus_issue_events", "merger_split_events", "shareholder_meeting_events",
    }


def test_get_stock_status_caches_per_ticker():
    ctx = make_ctx(
        raw_responses={
            "FHPST01390000": {"output": []},
            "FHPST04830000": {"output": []},
            "FHKST130000C0": {"output": []},
        },
        stock_info={},
    )
    get_stock_status(ctx, "SCAN", ticker="005930")
    get_stock_status(ctx, "SCAN", ticker="005930")
    # VI + 공매도 + 상한가캡처 + 하한가캡처 = 4, 캐시되어 두 번째 호출은 추가 없음
    assert len(ctx.kis_api.raw_get_calls) == 4


def test_get_stock_status_capture_uplow_cached_across_tickers():
    ctx = make_ctx(
        raw_responses={
            "FHPST01390000": {"output": []},
            "FHPST04830000": {"output": []},
            "FHKST130000C0": {"output": [{"mksc_shrn_iscd": "005930"}]},
        },
        stock_info={},
    )
    get_stock_status(ctx, "SCAN", ticker="005930")
    get_stock_status(ctx, "SCAN", ticker="000660")
    capture_calls = [c for c in ctx.kis_api.raw_get_calls if c[0] == "FHKST130000C0"]
    # 상한가/하한가 각 1회만 — 두 번째 종목 조회 시 market-wide 캡처 캐시 재사용
    assert len(capture_calls) == 2
