"""market_intelligence/portfolio.py 테스트"""
from market_intelligence.base import MILContext
from market_intelligence.portfolio import get_open_positions, get_daily_pnl


class StubKisApi:
    def __init__(self, balance=None):
        self._balance = balance or {}

    def get_balance(self):
        return self._balance


def make_ctx(**kwargs):
    return MILContext(kis_api=StubKisApi(**kwargs))


def test_get_open_positions_filters_zero_quantity():
    ctx = make_ctx(
        balance={
            "output1": [
                {"pdno": "005930", "prdt_name": "삼성전자", "hldg_qty": "10",
                 "pchs_avg_pric": "70000", "prpr": "71000",
                 "evlu_pfls_amt": "10000", "evlu_pfls_rt": "1.43"},
                {"pdno": "000660", "prdt_name": "SK하이닉스", "hldg_qty": "0",
                 "pchs_avg_pric": "0", "prpr": "0", "evlu_pfls_amt": "0", "evlu_pfls_rt": "0"},
            ],
            "output2": [],
        },
    )
    result = get_open_positions(ctx, "INTRADAY")
    assert result["position_count"] == 1
    assert result["positions"][0]["ticker"] == "005930"
    assert result["positions"][0]["quantity"] == 10


def test_get_daily_pnl_computes_realized_pct():
    ctx = make_ctx(
        balance={
            "output1": [],
            "output2": [{"tot_evlu_amt": "10000000", "rlzt_pfls": "-30000"}],
        },
    )
    result = get_daily_pnl(ctx, "INTRADAY")
    assert result["realized_pnl_krw"] == -30000.0
    assert result["realized_pnl_pct"] == -0.3


def test_get_daily_pnl_handles_empty_output2():
    ctx = make_ctx(balance={"output1": [], "output2": []})
    result = get_daily_pnl(ctx, "INTRADAY")
    assert result["realized_pnl_krw"] == 0.0
    assert result["realized_pnl_pct"] == 0.0
