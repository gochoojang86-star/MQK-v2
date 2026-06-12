"""종목분석 도구 5개: get_ohlcv, get_realtime_price, get_intraday_candles, get_flow, get_news_stock

get_snapshot은 제거되었다 — get_ohlcv의 output1이 현재가+호가+밸류에이션을 포함한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from broker.telegram_news import get_recent_news
from codes.news_fetcher import NaverNewsFetcher
from market_intelligence.base import MILContext, ToolFailure


def get_ohlcv(ctx: MILContext, phase: str, ticker: str, period: int = 60) -> dict:
    """국내주식기간별시세. output1=현재가/호가/밸류에이션, output2=OHLCV+권리락코드."""

    def fetch():
        end = datetime.now()
        start = end - timedelta(days=max(period * 3, 30))
        raw = ctx.kis_api.raw_get(
            "FHKST03010100",
            "domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            },
        )
        out1 = raw.get("output1", {})
        candles = [
            {
                "date": row.get("stck_bsop_date"),
                "open": _to_float(row.get("stck_oprc")),
                "high": _to_float(row.get("stck_hgpr")),
                "low": _to_float(row.get("stck_lwpr")),
                "close": _to_float(row.get("stck_clpr")),
                "volume": _to_float(row.get("acml_vol")),
                "trading_value": _to_float(row.get("acml_tr_pbmn")),
                "rights_event_code": row.get("flng_cls_code"),
            }
            for row in raw.get("output2", [])[:period]
        ]
        return {
            "ticker": ticker,
            "current_price": _to_float(out1.get("stck_prpr")),
            "ask_price": _to_float(out1.get("askp")),
            "bid_price": _to_float(out1.get("bidp")),
            "per": _to_float(out1.get("per")),
            "eps": _to_float(out1.get("eps")),
            "pbr": _to_float(out1.get("pbr")),
            "market_cap": _to_float(out1.get("hts_avls")),
            "upper_limit": _to_float(out1.get("stck_mxpr")),
            "lower_limit": _to_float(out1.get("stck_llam")),
            "candles": candles,
        }

    return ctx.cached_call("get_ohlcv", phase, {"ticker": ticker, "period": period}, fetch)


def get_realtime_price(ctx: MILContext, phase: str, tickers: list[str]) -> dict:
    """관심종목(멀티종목) 시세조회. 최대 30종목 배치. 모의투자 미지원 (mode=real 고정)."""

    def fetch():
        if len(tickers) > 30:
            raise ValueError("최대 30종목까지만 조회 가능")
        params = {"FID_COND_MRKT_DIV_CODE_1": "J"}
        for i, ticker in enumerate(tickers, start=1):
            params[f"FID_INPUT_ISCD_{i}"] = ticker
            params[f"FID_COND_MRKT_DIV_CODE_{i}"] = "J"
        raw = ctx.kis_api.raw_get(
            "FHKST11300006",
            "domestic-stock/v1/quotations/intstock-multprice",
            params,
            mode="real",
        )
        prices = [
            {
                "ticker": row.get("inter_shrn_iscd"),
                "price": _to_float(row.get("inter2_prpr")),
                "change_pct": _to_float(row.get("prdy_ctrt")),
                "volume": _to_float(row.get("acml_vol")),
            }
            for row in raw.get("output", [])
        ]
        return {"prices": prices}

    return ctx.cached_call("get_realtime_price", phase, {"tickers": tickers}, fetch)


def get_intraday_candles(ctx: MILContext, phase: str, ticker: str) -> dict:
    """주식당일분봉조회."""

    def fetch():
        raw = ctx.kis_api.raw_get(
            "FHKST03010200",
            "domestic-stock/v1/quotations/inquire-time-itemchartprice",
            {
                "FID_ETC_CLS_CODE": "",
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                # 기준시각(HHMMSS) 이전 최대 30건 — "60" 같은 비정상 값을 주면
                # 전일 15시대 캔들이 반환된다 (D1 라이브 테스트에서 확인).
                "FID_INPUT_HOUR_1": datetime.now().strftime("%H%M%S"),
                # N=당일만 — Y는 전일 캔들이 섞인다.
                "FID_PW_DATA_INCU_YN": "N",
            },
        )
        candles = [
            {
                "time": row.get("stck_cntg_hour"),
                "open": _to_float(row.get("stck_oprc")),
                "high": _to_float(row.get("stck_hgpr")),
                "low": _to_float(row.get("stck_lwpr")),
                "close": _to_float(row.get("stck_prpr")),
                "volume": _to_float(row.get("cntg_vol")),
            }
            for row in raw.get("output2", [])
        ]
        # 최신 우선 응답 → 시간 오름차순 정렬 (candles[0]=가장 이른 분봉)
        candles.sort(key=lambda c: c["time"] or "")
        return {"ticker": ticker, "candles": candles}

    return ctx.cached_call("get_intraday_candles", phase, {"ticker": ticker}, fetch)


def get_flow(ctx: MILContext, phase: str, ticker: str) -> dict:
    """종목별 투자자매매동향(일별) - 외국인/기관/개인/투신/사모/은행/보험/기금 순매수 수량.

    응답은 output2(일별 리스트, 최신일 우선). 당일 row는 장 종료 후 확정된다.
    """

    def fetch():
        # 당일 기준 조회는 장 종료 후에만 가능 (장중엔 rt_cd=2/빈 결과 — D1 확인).
        # 오늘부터 최대 4일 거슬러가며 데이터가 있는 기준일을 찾는다.
        raw = {}
        for days_back in range(5):
            base_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")
            raw = ctx.kis_api.raw_get(
                "FHPTJ04160001",
                "domestic-stock/v1/quotations/investor-trade-by-stock-daily",
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": ticker,
                    "FID_INPUT_DATE_1": base_date,
                    "FID_ORG_ADJ_PRC": "",
                    "FID_ETC_CLS_CODE": "1",
                },
            )
            if raw.get("output2"):
                break
        days = [
            {
                "date": row.get("stck_bsop_date"),
                "close": _to_float(row.get("stck_clpr")),
                "foreign_net_qty": _to_float(row.get("frgn_ntby_qty")),
                "institution_net_qty": _to_float(row.get("orgn_ntby_qty")),
                "individual_net_qty": _to_float(row.get("prsn_ntby_qty")),
                "trust_net_qty": _to_float(row.get("ivtr_ntby_qty")),
                "private_fund_net_qty": _to_float(row.get("pe_fund_ntby_vol")),
                "bank_net_qty": _to_float(row.get("bank_ntby_qty")),
                "insurance_net_qty": _to_float(row.get("insu_ntby_qty")),
                "pension_net_qty": _to_float(row.get("fund_ntby_qty")),
            }
            for row in raw.get("output2", [])
        ]
        return {"ticker": ticker, "days": days}

    return ctx.cached_call("get_flow", phase, {"ticker": ticker}, fetch)


def get_news_stock(ctx: MILContext, phase: str, ticker: str) -> dict:
    """ticker 필터 뉴스 3종 통합: KIS 공시/시황 + 텔레그램 속보 + 네이버 뉴스 검색.

    텔레그램(최근 2시간)은 속보성, 네이버(종목명 검색)는 촉매 맥락 보강용.
    각 소스는 개별 격리 — 실패는 missing_fields에 기록하고 0건으로 해석하지 않는다.
    """

    def fetch():
        result: dict = {"ticker": ticker}
        missing: list[str] = []

        raw = ctx.kis_api.raw_get(
            "FHKST01011800",
            "domestic-stock/v1/quotations/news-title",
            {
                "FID_NEWS_OFER_ENTP_CODE": "",
                "FID_COND_MRKT_CLS_CODE": "",
                "FID_INPUT_ISCD": ticker,
                "FID_TITL_CNTT": "",
                "FID_INPUT_DATE_1": "",
                "FID_INPUT_HOUR_1": "",
                "FID_RANK_SORT_CLS_CODE": "",
                "FID_INPUT_SRNO": "",
            },
        )
        result["headlines"] = [
            {
                "title": row.get("hts_pbnt_titl_cntt"),
                "date": row.get("data_dt"),
                "time": row.get("data_tm"),
            }
            for row in raw.get("output", [])
        ]

        # 텔레그램 속보 (수집기 mqk-telegram-news가 sqlite에 적재)
        try:
            result["telegram_headlines"] = [
                {"title": n.get("title"), "sentiment": n.get("sentiment"),
                 "source": n.get("source"), "date": n.get("date")}
                for n in get_recent_news(ticker=ticker, hours=2)[:10]
            ]
        except Exception:
            result["telegram_headlines"] = []
            missing.append("telegram_headlines")

        # 네이버 뉴스 검색 (종목명 기준 — 촉매 맥락)
        try:
            stock_name = ""
            try:
                stock_name = (ctx.kis_api.get_snapshot(ticker) or {}).get("name", "")
            except Exception:
                pass
            if stock_name:
                items = NaverNewsFetcher().search(stock_name, display=5)
                result["naver_headlines"] = [
                    {"title": n.title, "summary": n.description[:120],
                     "date": n.pub_date, "url": n.url}
                    for n in items
                ]
            else:
                result["naver_headlines"] = []
                missing.append("naver_headlines")
        except Exception:
            result["naver_headlines"] = []
            missing.append("naver_headlines")

        if missing:
            result["missing_fields"] = missing
        return result

    return ctx.cached_call("get_news_stock", phase, {"ticker": ticker}, fetch)


def get_fundamentals(ctx: MILContext, phase: str, ticker: str) -> dict:
    """SEPA 펀더멘털 스크리닝용 재무 데이터 4종 조합.

    재무비율/손익계산서/대차대조표/종목투자의견 각각 개별 try/except 처리하여
    한 API 실패가 다른 섹션에 영향을 주지 않는다 (결측은 missing_fields에 기록,
    0으로 해석하지 않는다).

    손익계산서/대차대조표 단위는 억원(100mln KRW) — 필드명에 _100mln 접미사로 명시.
    """

    def fetch():
        result = {"ticker": ticker}
        missing_fields: list[str] = []

        common_params = {
            "FID_DIV_CLS_CODE": "0",
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": ticker,
        }

        try:
            raw = ctx.kis_api.raw_get(
                "FHKST66430300",
                "domestic-stock/v1/finance/financial-ratio",
                common_params,
            )
            result["financial_ratios"] = [
                {
                    "period": row.get("stac_yymm"),
                    "revenue_growth_rate_pct": _to_float(row.get("grs")),
                    "operating_profit_growth_rate_pct": _to_float(row.get("bsop_prfi_inrt")),
                    "net_income_growth_rate_pct": _to_float(row.get("ntin_inrt")),
                    "roe_pct": _to_float(row.get("roe_val")),
                    "eps": _to_float(row.get("eps")),
                    "bps": _to_float(row.get("bps")),
                    "debt_ratio_pct": _to_float(row.get("lblt_rate")),
                }
                for row in raw.get("output", [])[:4]
            ]
        except Exception:
            result["financial_ratios"] = []
            missing_fields.append("financial_ratios")

        try:
            raw = ctx.kis_api.raw_get(
                "FHKST66430200",
                "domestic-stock/v1/finance/income-statement",
                common_params,
            )
            result["income_statements"] = [
                {
                    "period": row.get("stac_yymm"),
                    "revenue_100mln": _to_float(row.get("sale_account")),
                    "operating_profit_100mln": _to_float(row.get("op_prfi")),
                    "net_income_100mln": _to_float(row.get("thtr_ntin")),
                }
                for row in raw.get("output", [])[:4]
            ]
        except Exception:
            result["income_statements"] = []
            missing_fields.append("income_statements")

        try:
            raw = ctx.kis_api.raw_get(
                "FHKST66430100",
                "domestic-stock/v1/finance/balance-sheet",
                common_params,
            )
            result["balance_sheets"] = [
                {
                    "period": row.get("stac_yymm"),
                    "total_assets_100mln": _to_float(row.get("total_aset")),
                    "total_liabilities_100mln": _to_float(row.get("total_lblt")),
                    "total_equity_100mln": _to_float(row.get("total_cptl")),
                }
                for row in raw.get("output", [])[:4]
            ]
        except Exception:
            result["balance_sheets"] = []
            missing_fields.append("balance_sheets")

        try:
            today = datetime.now()
            six_months_ago = today - timedelta(days=180)
            raw = ctx.kis_api.raw_get(
                "FHKST663300C0",
                "domestic-stock/v1/quotations/invest-opinion",
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_COND_SCR_DIV_CODE": "16633",
                    "FID_INPUT_ISCD": ticker,
                    "FID_INPUT_DATE_1": "00" + six_months_ago.strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2": "00" + today.strftime("%Y%m%d"),
                },
            )
            result["analyst_opinions"] = [
                {
                    "date": row.get("stck_bsop_date"),
                    "opinion": row.get("invt_opnn"),
                    "firm": row.get("mbcr_name"),
                    "target_price": _to_float(row.get("hts_goal_prc")),
                }
                for row in raw.get("output", [])[:10]
            ]
        except Exception:
            result["analyst_opinions"] = []
            missing_fields.append("analyst_opinions")

        if missing_fields:
            result["missing_fields"] = missing_fields

        return result

    return ctx.cached_call("get_fundamentals", phase, {"ticker": ticker}, fetch)


def _to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0
