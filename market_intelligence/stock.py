"""종목분석 도구 5개: get_ohlcv, get_realtime_price, get_intraday_candles, get_flow, get_news_stock

get_snapshot은 제거되었다 — get_ohlcv의 output1이 현재가+호가+밸류에이션을 포함한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta

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
                "FID_INPUT_HOUR_1": "60",
                "FID_PW_DATA_INCU_YN": "Y",
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
        return {"ticker": ticker, "candles": candles}

    return ctx.cached_call("get_intraday_candles", phase, {"ticker": ticker}, fetch)


def get_flow(ctx: MILContext, phase: str, ticker: str) -> dict:
    """종목별 투자자매매동향(일별) - 외국인/기관/개인/투신/사모/은행/보험/기금 순매수 수량."""

    def fetch():
        raw = ctx.kis_api.raw_get(
            "FHPTJ04160001",
            "domestic-stock/v1/quotations/inquire-investor-time-by-stock",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
            },
        )
        days = [
            {
                "date": row.get("stck_bsop_date"),
                "close": _to_float(row.get("stck_clpr")),
                "foreign_net_qty": _to_float(row.get("frgn_ntby_qty")),
                "institution_net_qty": _to_float(row.get("orgn_ntby_qty")),
                "individual_net_qty": _to_float(row.get("prsn_ntby_qty")),
                "trust_net_qty": _to_float(row.get("invtrt_ntby_qty")),
                "private_fund_net_qty": _to_float(row.get("prvt_fund_ntby_qty")),
                "bank_net_qty": _to_float(row.get("bank_ntby_qty")),
                "insurance_net_qty": _to_float(row.get("insu_ntby_qty")),
                "pension_net_qty": _to_float(row.get("pe_fund_ntby_qty")),
            }
            for row in raw.get("output", [])
        ]
        return {"ticker": ticker, "days": days}

    return ctx.cached_call("get_flow", phase, {"ticker": ticker}, fetch)


def get_news_stock(ctx: MILContext, phase: str, ticker: str) -> dict:
    """ticker 필터 뉴스/공시 제목."""

    def fetch():
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
        headlines = [
            {
                "title": row.get("hts_pbnt_titl_cntt"),
                "date": row.get("data_dt"),
                "time": row.get("data_tm"),
            }
            for row in raw.get("output", [])
        ]
        return {"ticker": ticker, "headlines": headlines}

    return ctx.cached_call("get_news_stock", phase, {"ticker": ticker}, fetch)


def _to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0
