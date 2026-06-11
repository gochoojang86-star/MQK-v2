"""리스크필터 도구 2개: get_stock_status, get_event_schedule"""
from __future__ import annotations

from datetime import datetime, timedelta

from market_intelligence.base import MILContext


def get_stock_status(ctx: MILContext, phase: str, ticker: str) -> dict:
    """VI 발동 여부, 관리종목/거래정지 여부, 공매도 비중."""

    def fetch():
        vi = ctx.kis_api.raw_get(
            "FHPST01390000",
            "domestic-stock/v1/quotations/inquire-vi-status",
            {
                "FID_DIV_CLS_CODE": "0",
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                "FID_RANK_SORT_CLS_CODE": "0",
                "FID_INPUT_DATE_1": "",
            },
        )
        vi_triggered = any(
            row.get("mksc_shrn_iscd") == ticker for row in vi.get("output", [])
        )

        info = ctx.kis_api.get_stock_info(ticker)

        short = ctx.kis_api.raw_get(
            "FHPST04830000",
            "domestic-stock/v1/quotations/daily-short-sale",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                "FID_INPUT_DATE_1": "",
                "FID_INPUT_DATE_2": "",
                "FID_PERIOD_DIV_CODE": "D",
            },
        )
        short_rows = short.get("output", [])
        short_ratio = _to_float(short_rows[0].get("shnu_rate")) if short_rows else 0.0

        return {
            "ticker": ticker,
            "vi_triggered": vi_triggered,
            "trading_halted": info.get("trading_halted", False),
            "administrative_issue": info.get("administrative_issue", False),
            "short_sale_ratio_pct": short_ratio,
        }

    return ctx.cached_call("get_stock_status", phase, {"ticker": ticker}, fetch)


def get_event_schedule(ctx: MILContext, phase: str, ticker: str) -> dict:
    """권리락일/유상증자 청약기간 + 배당기준일/배당금 (향후 90일)."""

    def fetch():
        f_dt = datetime.now().strftime("%Y%m%d")
        t_dt = (datetime.now() + timedelta(days=90)).strftime("%Y%m%d")
        rights = ctx.kis_api.raw_get(
            "HHKDB669100C0",
            "domestic-stock/v1/ksdinfo/paidin-capin",
            {"CTS": "", "GB1": "1", "F_DT": f_dt, "T_DT": t_dt, "SHT_CD": ticker},
        )
        dividend = ctx.kis_api.raw_get(
            "HHKDB669102C0",
            "domestic-stock/v1/ksdinfo/dividend",
            {"CTS": "", "GB1": "0", "F_DT": f_dt, "T_DT": t_dt, "SHT_CD": ticker, "HIGH_GB": ""},
        )
        rights_events = [
            {
                "record_date": row.get("record_date"),
                "rights_ex_date": row.get("right_dt"),
                "subscription_start": row.get("sub_term_ft"),
                "subscription_period": row.get("sub_term"),
            }
            for row in rights.get("output1", [])
        ]
        dividend_events = [
            {
                "record_date": row.get("record_date"),
                "dividend_amount": _to_float(row.get("per_sto_divi_amt")),
            }
            for row in dividend.get("output1", [])
        ]
        return {"ticker": ticker, "rights_events": rights_events, "dividend_events": dividend_events}

    return ctx.cached_call("get_event_schedule", phase, {"ticker": ticker}, fetch)


def _to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0
