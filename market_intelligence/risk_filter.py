"""리스크필터 도구 2개: get_stock_status, get_event_schedule"""
from __future__ import annotations

from datetime import datetime, timedelta

from market_intelligence.base import MILContext


def _fetch_capture_uplow(ctx: MILContext, phase: str, side: str) -> list[str]:
    """상한가(side='0')/하한가(side='1') 포착 종목 티커 목록을 phase당 1회만 조회.

    시장 전체 목록이므로 종목별이 아닌 phase 단위로 캐시해 재사용한다.
    """

    def fetch():
        raw = ctx.kis_api.raw_get(
            "FHKST130000C0",
            "domestic-stock/v1/quotations/capture-uplowprice",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "11300",
                "FID_PRC_CLS_CODE": side,
                "FID_DIV_CLS_CODE": "0",
                "FID_INPUT_ISCD": "0000",
                "FID_TRGT_CLS_CODE": "",
                "FID_TRGT_EXLS_CLS_CODE": "",
                "FID_INPUT_PRICE_1": "",
                "FID_INPUT_PRICE_2": "",
                "FID_VOL_CNT": "",
            },
        )
        return [row.get("mksc_shrn_iscd") for row in raw.get("output", [])]

    return ctx.cached_call("capture_uplow", phase, {"side": side}, fetch)


def get_stock_status(ctx: MILContext, phase: str, ticker: str) -> dict:
    """VI 발동 여부, 관리종목/거래정지 여부, 공매도 비중, 상하한가 여부."""

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

        result = {
            "ticker": ticker,
            "vi_triggered": vi_triggered,
            "trading_halted": info.get("trading_halted", False),
            "administrative_issue": info.get("administrative_issue", False),
            "short_sale_ratio_pct": short_ratio,
        }

        missing_fields: list[str] = []
        try:
            limit_up_tickers = _fetch_capture_uplow(ctx, phase, "0")
            limit_down_tickers = _fetch_capture_uplow(ctx, phase, "1")
            result["is_limit_up"] = ticker in limit_up_tickers
            result["is_limit_down"] = ticker in limit_down_tickers
        except Exception:
            result["is_limit_up"] = None
            result["is_limit_down"] = None
            missing_fields.append("is_limit_up")
            missing_fields.append("is_limit_down")

        if missing_fields:
            result["missing_fields"] = missing_fields

        return result

    return ctx.cached_call("get_stock_status", phase, {"ticker": ticker}, fetch)


def get_event_schedule(ctx: MILContext, phase: str, ticker: str) -> dict:
    """권리락일/유상증자 청약기간 + 배당기준일/배당금 (향후 90일).
    추가: 무상증자일정, 합병/분할일정, 주주총회일정.

    각 ksdinfo 호출은 개별적으로 try/except 처리해 한 API 실패가 다른 결과에
    영향을 주지 않도록 한다 (결측은 missing_fields에 기록하고 0/빈값으로 해석하지 않음)."""

    def fetch():
        f_dt = datetime.now().strftime("%Y%m%d")
        t_dt = (datetime.now() + timedelta(days=90)).strftime("%Y%m%d")
        result = {"ticker": ticker}
        missing_fields: list[str] = []

        try:
            rights = ctx.kis_api.raw_get(
                "HHKDB669100C0",
                "domestic-stock/v1/ksdinfo/paidin-capin",
                {"CTS": "", "GB1": "1", "F_DT": f_dt, "T_DT": t_dt, "SHT_CD": ticker},
            )
            result["rights_events"] = [
                {
                    "record_date": row.get("record_date"),
                    "rights_ex_date": row.get("right_dt"),
                    "subscription_start": row.get("sub_term_ft"),
                    "subscription_period": row.get("sub_term"),
                }
                for row in rights.get("output1", [])
            ]
        except Exception:
            result["rights_events"] = []
            missing_fields.append("rights_events")

        try:
            dividend = ctx.kis_api.raw_get(
                "HHKDB669102C0",
                "domestic-stock/v1/ksdinfo/dividend",
                {"CTS": "", "GB1": "0", "F_DT": f_dt, "T_DT": t_dt, "SHT_CD": ticker, "HIGH_GB": ""},
            )
            result["dividend_events"] = [
                {
                    "record_date": row.get("record_date"),
                    "dividend_amount": _to_float(row.get("per_sto_divi_amt")),
                }
                for row in dividend.get("output1", [])
            ]
        except Exception:
            result["dividend_events"] = []
            missing_fields.append("dividend_events")

        # 무상증자일정
        try:
            bonus = ctx.kis_api.raw_get(
                "HHKDB669101C0",
                "domestic-stock/v1/ksdinfo/bonus-issue",
                {"CTS": "", "F_DT": f_dt, "T_DT": t_dt, "SHT_CD": ticker},
            )
            result["bonus_issue_events"] = [
                {
                    "record_date": row.get("record_date"),
                    "rights_ex_date": row.get("right_dt"),
                    "fix_rate": _to_float(row.get("fix_rate")),
                    "list_date": row.get("list_date"),
                }
                for row in bonus.get("output1", [])
            ]
        except Exception:
            result["bonus_issue_events"] = []
            missing_fields.append("bonus_issue_events")

        # 합병/분할일정
        try:
            merger = ctx.kis_api.raw_get(
                "HHKDB669104C0",
                "domestic-stock/v1/ksdinfo/merger-split",
                {"CTS": "", "F_DT": f_dt, "T_DT": t_dt, "SHT_CD": ticker},
            )
            result["merger_split_events"] = [
                {
                    "record_date": row.get("record_date"),
                    "merge_type": row.get("merge_type"),
                    "merge_rate": _to_float(row.get("merge_rate")),
                    "list_date": row.get("list_dt"),
                }
                for row in merger.get("output1", [])
            ]
        except Exception:
            result["merger_split_events"] = []
            missing_fields.append("merger_split_events")

        # 주주총회일정
        try:
            meeting = ctx.kis_api.raw_get(
                "HHKDB669111C0",
                "domestic-stock/v1/ksdinfo/sharehld-meet",
                {"CTS": "", "F_DT": f_dt, "T_DT": t_dt, "SHT_CD": ticker},
            )
            result["shareholder_meeting_events"] = [
                {
                    "meeting_date": row.get("gen_meet_dt"),
                    "meeting_type": row.get("gen_meet_type"),
                    "agenda": row.get("agenda"),
                    "record_date": row.get("record_date"),
                }
                for row in meeting.get("output1", [])
            ]
        except Exception:
            result["shareholder_meeting_events"] = []
            missing_fields.append("shareholder_meeting_events")

        if missing_fields:
            result["missing_fields"] = missing_fields

        return result

    return ctx.cached_call("get_event_schedule", phase, {"ticker": ticker}, fetch)


def _to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0
