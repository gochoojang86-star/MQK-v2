"""market_intelligence/stock.py 테스트"""
from market_intelligence.base import MILContext
from market_intelligence.stock import (
    get_ohlcv,
    get_realtime_price,
    get_intraday_candles,
    get_flow,
    get_news_stock,
    get_fundamentals,
    get_watchlist_intraday_snapshot,
)


class StubKisApi:
    def __init__(self, raw_responses=None):
        self._raw_responses = raw_responses or {}
        self.raw_get_calls = []

    def raw_get(self, tr_id, path, params, mode=None):
        self.raw_get_calls.append((tr_id, path, params, mode))
        return self._raw_responses[tr_id]

    def get_snapshot(self, ticker):
        return {"name": f"종목{ticker}", "trading_value": "1000000000", "market_cap": "5000000000000"}


def make_ctx(**kwargs):
    return MILContext(kis_api=StubKisApi(**kwargs))


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
                "output2": [
                    {"stck_bsop_date": "20260609", "stck_clpr": "70000",
                     "frgn_ntby_qty": "-10000", "orgn_ntby_qty": "5000",
                     "prsn_ntby_qty": "5000", "ivtr_ntby_qty": "1000",
                     "pe_fund_ntby_vol": "500", "bank_ntby_qty": "100",
                     "insu_ntby_qty": "200", "fund_ntby_qty": "300"},
                ],
            },
        },
    )
    result = get_flow(ctx, "SCAN", ticker="005930")
    assert result["days"][0]["foreign_net_qty"] == -10000.0
    assert result["days"][0]["institution_net_qty"] == 5000.0


def _patch_news_sources(monkeypatch, telegram=None, naver=None):
    import market_intelligence.stock as mil_stock

    monkeypatch.setattr(mil_stock, "get_recent_news",
                         lambda ticker="", hours=2: telegram or [])

    class FakeNaver:
        def search(self, query, display=5):
            return naver or []

    monkeypatch.setattr(mil_stock, "NaverNewsFetcher", FakeNaver)


def test_get_news_stock_filters_by_ticker(monkeypatch):
    _patch_news_sources(monkeypatch)
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


def _fundamentals_responses():
    return {
        "FHKST66430300": {
            "rt_cd": "0",
            "output": [
                {"stac_yymm": "202603", "grs": "69.1600", "bsop_prfi_inrt": "756.1000",
                 "ntin_inrt": "474.3200", "roe_val": "19.16", "eps": "6993.00",
                 "sps": "57655", "bps": "71907.00", "rsrv_rate": "50140.0200",
                 "lblt_rate": "30.1500"},
            ],
        },
        "FHKST66430200": {
            "rt_cd": "0",
            "output": [
                {"stac_yymm": "202603", "sale_account": "1338734.00",
                 "sale_cost": "519602.00", "sale_totl_prfi": "819132",
                 "bsop_prti": "572328.00", "op_prfi": "588284.00",
                 "thtr_ntin": "472253.00"},
            ],
        },
        "FHKST66430100": {
            "rt_cd": "0",
            "output": [
                {"stac_yymm": "202603", "cras": "3062201.00", "fxas": "3271195.00",
                 "total_aset": "6333396.00", "flow_lblt": "1206038.00",
                 "fix_lblt": "260999.00", "total_lblt": "1467036.00",
                 "total_cptl": "4866360.00"},
            ],
        },
        "FHKST663300C0": {
            "rt_cd": "0",
            "output": [
                {"stck_bsop_date": "20260610", "invt_opnn": "BUY",
                 "mbcr_name": "현대차", "hts_goal_prc": "440000",
                 "stck_prdy_clpr": "322000"},
            ],
        },
    }


def test_get_fundamentals_parses_all_four_sections():
    ctx = make_ctx(raw_responses=_fundamentals_responses())
    result = get_fundamentals(ctx, "SCAN", ticker="005930")

    assert result["ticker"] == "005930"

    fr = result["financial_ratios"][0]
    assert fr["period"] == "202603"
    assert fr["revenue_growth_rate_pct"] == 69.16
    assert fr["operating_profit_growth_rate_pct"] == 756.10
    assert fr["roe_pct"] == 19.16
    assert fr["eps"] == 6993.0
    assert fr["bps"] == 71907.0
    assert fr["debt_ratio_pct"] == 30.15

    inc = result["income_statements"][0]
    assert inc["period"] == "202603"
    assert inc["revenue_100mln"] == 1338734.0
    assert inc["operating_profit_100mln"] == 588284.0
    assert inc["net_income_100mln"] == 472253.0

    bs = result["balance_sheets"][0]
    assert bs["period"] == "202603"
    assert bs["total_assets_100mln"] == 6333396.0
    assert bs["total_liabilities_100mln"] == 1467036.0
    assert bs["total_equity_100mln"] == 4866360.0

    op = result["analyst_opinions"][0]
    assert op["date"] == "20260610"
    assert op["opinion"] == "BUY"
    assert op["firm"] == "현대차"
    assert op["target_price"] == 440000.0

    assert "missing_fields" not in result


def test_get_fundamentals_one_section_failure_records_missing_fields():
    responses = _fundamentals_responses()
    del responses["FHKST66430200"]  # income-statement 호출 시 KeyError 발생 -> 실패 처리

    ctx = make_ctx(raw_responses=responses)
    result = get_fundamentals(ctx, "SCAN", ticker="005930")

    assert result["income_statements"] == []
    assert "income_statements" in result["missing_fields"]
    assert result["financial_ratios"]
    assert result["balance_sheets"]
    assert result["analyst_opinions"]


def test_get_fundamentals_caches_per_ticker():
    ctx = make_ctx(raw_responses=_fundamentals_responses())
    get_fundamentals(ctx, "SCAN", ticker="005930")
    get_fundamentals(ctx, "SCAN", ticker="005930")
    assert len(ctx.kis_api.raw_get_calls) == 4

def test_get_news_stock_merges_telegram_and_naver(monkeypatch):
    from codes.news_fetcher import NewsItem
    _patch_news_sources(
        monkeypatch,
        telegram=[{"title": "삼성전자 대규모 수주", "sentiment": "positive",
                    "score": 0.8, "source": "FastStockNews", "date": "2026-06-12T10:00:00"}],
        naver=[NewsItem(title="삼성전자, 세계 최초 공정 발표", description="2나노 양산" * 30,
                         url="https://n.news", pub_date="Fri, 12 Jun 2026", source="naver")],
    )

    class SnapshotKis(StubKisApi):
        def get_snapshot(self, ticker):
            return {"name": "삼성전자"}

    from market_intelligence.cache import MILCache
    from market_intelligence.circuit_breaker import CircuitBreaker
    ctx = MILContext(
        kis_api=SnapshotKis(raw_responses={"FHKST01011800": {"output": []}}),
        cache=MILCache(), circuit_breaker=CircuitBreaker(),
    )
    result = get_news_stock(ctx, "SCAN", ticker="005930")

    assert result["telegram_headlines"][0]["title"] == "삼성전자 대규모 수주"
    assert result["naver_headlines"][0]["title"].startswith("삼성전자")
    assert len(result["naver_headlines"][0]["summary"]) <= 120
    assert "missing_fields" not in result


def test_get_news_stock_isolates_source_failures(monkeypatch):
    import market_intelligence.stock as mil_stock

    def boom(ticker="", hours=2):
        raise RuntimeError("sqlite down")

    monkeypatch.setattr(mil_stock, "get_recent_news", boom)

    class BoomNaver:
        def search(self, query, display=5):
            raise RuntimeError("network down")

    monkeypatch.setattr(mil_stock, "NaverNewsFetcher", BoomNaver)

    ctx = make_ctx(raw_responses={"FHKST01011800": {"output": [
        {"hts_pbnt_titl_cntt": "KIS 뉴스", "data_dt": "20260612", "data_tm": "100000"}]}})
    result = get_news_stock(ctx, "SCAN", ticker="005930")

    assert result["headlines"][0]["title"] == "KIS 뉴스"  # KIS는 정상
    assert result["telegram_headlines"] == []
    assert result["naver_headlines"] == []
    assert set(result["missing_fields"]) == {"telegram_headlines", "naver_headlines"}


def test_get_watchlist_intraday_snapshot_bundles_price_news_and_status(monkeypatch):
    import market_intelligence.stock as mil_stock
    import market_intelligence.risk_filter as mil_risk_filter

    monkeypatch.setattr(
        mil_stock,
        "get_recent_news",
        lambda ticker="", hours=2: [{"title": f"{ticker} 속보", "source": "FastStockNews", "date": "2026-06-15T09:30:00"}],
    )
    monkeypatch.setattr(
        mil_risk_filter,
        "get_stock_status",
        lambda ctx, phase, ticker: {"ticker": ticker, "is_limit_up": False, "is_limit_down": False, "is_vi": False,
                                    "trading_halted": False, "administrative_issue": False},
    )

    class FakeNaver:
        def search(self, query, display=5):
            return []

    monkeypatch.setattr(mil_stock, "NaverNewsFetcher", FakeNaver)

    ctx = make_ctx(
        raw_responses={
            "FHKST11300006": {
                "output": [
                    {"inter_shrn_iscd": "005930", "inter2_prpr": "70000", "prdy_ctrt": "1.0", "acml_vol": "100"},
                    {"inter_shrn_iscd": "000660", "inter2_prpr": "180000", "prdy_ctrt": "5.0", "acml_vol": "200"},
                ],
            },
            "FHKST03010200": {
                "output2": [
                    {"stck_cntg_hour": "093000", "stck_oprc": "69800", "stck_hgpr": "70000",
                     "stck_lwpr": "69700", "stck_prpr": "69900", "cntg_vol": "5000"},
                    {"stck_cntg_hour": "094000", "stck_oprc": "69900", "stck_hgpr": "70600",
                     "stck_lwpr": "69800", "stck_prpr": "70550", "cntg_vol": "4000"},
                ],
            },
            "FHKST01011800": {"output": []},
        },
    )

    result = get_watchlist_intraday_snapshot(ctx, "INTRADAY", ["005930", "000660", "00ABCD"])

    assert [row["ticker"] for row in result["tickers"]] == ["005930", "000660"]
    assert result["tickers"][0]["price"] == 70000.0
    assert result["tickers"][0]["intraday_trend"] == "up"
    assert result["tickers"][0]["headline_count"] == 0
    assert result["tickers"][0]["telegram_headline_count"] == 1
    assert "005930 속보" in result["tickers"][0]["latest_headlines"][0]


def test_get_watchlist_intraday_snapshot_uses_hts_avls_for_market_cap(monkeypatch):
    import market_intelligence.stock as mil_stock
    import market_intelligence.risk_filter as mil_risk_filter

    monkeypatch.setattr(mil_stock, "get_recent_news", lambda ticker="", hours=2: [])
    monkeypatch.setattr(
        mil_risk_filter,
        "get_stock_status",
        lambda ctx, phase, ticker: {"ticker": ticker, "is_limit_up": False, "is_limit_down": False, "is_vi": False,
                                    "trading_halted": False, "administrative_issue": False},
    )

    class FakeNaver:
        def search(self, query, display=5):
            return []

    monkeypatch.setattr(mil_stock, "NaverNewsFetcher", FakeNaver)

    class HtsAvlsKisApi(StubKisApi):
        def get_snapshot(self, ticker):
            return {"name": f"종목{ticker}", "trading_value": "1000000000", "hts_avls": "42000"}

    ctx = MILContext(
        kis_api=HtsAvlsKisApi(
            raw_responses={
                "FHKST11300006": {"output": [{"inter_shrn_iscd": "005930", "inter2_prpr": "70000", "prdy_ctrt": "1.0", "acml_vol": "100"}]},
                "FHKST03010200": {"output2": []},
                "FHKST01011800": {"output": []},
            }
        )
    )

    result = get_watchlist_intraday_snapshot(ctx, "INTRADAY", ["005930"])

    assert result["tickers"][0]["market_cap"] == 4_200_000_000_000.0
