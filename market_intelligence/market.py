"""시장관찰 도구 4개: get_market_context, get_sector_breadth, get_intraday_index_candles, get_news_market"""
from __future__ import annotations

from datetime import datetime

from market_intelligence.base import MILContext


def get_market_context(ctx: MILContext, phase: str) -> dict:
    """코스피/코스닥 지수, 외국인/기관 순매수, 전일 확정 등락률·거래대금,
    프로그램매매 순매수, 시장별 투자자매매동향(일별)."""

    def fetch():
        index_status = ctx.kis_api.get_index_status()
        flow = ctx.kis_api.raw_get(
            "FHPTJ04400000",
            "domestic-stock/v1/quotations/foreign-institution-total",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "16449",
                "FID_INPUT_ISCD": "0000",
                "FID_RANK_SORT_CLS_CODE": "0",
                "FID_ETC_CLS_CODE": "0",
            },
        )
        flow_rows = flow.get("output2", [])
        foreign_net = sum(_to_float(r.get("frgn_ntby_tr_pbmn")) for r in flow_rows)
        institution_net = sum(_to_float(r.get("orgn_ntby_tr_pbmn")) for r in flow_rows)

        result = {
            "kospi": index_status.get("kospi"),
            "kospi_change_pct": index_status.get("kospi_change_pct"),
            "kosdaq": index_status.get("kosdaq"),
            "kosdaq_change_pct": index_status.get("kosdaq_change_pct"),
            "kospi_advancers": index_status.get("kospi_advancers"),
            "kospi_decliners": index_status.get("kospi_decliners"),
            "foreign_net_buy_krw": foreign_net,
            "institution_net_buy_krw": institution_net,
            "prev_kospi_change_pct": index_status.get("prev_kospi_change_pct"),
            "prev_kospi_trading_value": index_status.get("prev_kospi_trading_value"),
            "prev_kosdaq_change_pct": index_status.get("prev_kosdaq_change_pct"),
            "prev_kosdaq_trading_value": index_status.get("prev_kosdaq_trading_value"),
        }

        missing_fields: list[str] = []

        # 프로그램매매 종합현황(시간) — 가장 최근 시간대(output[0])의 전체 순매수
        try:
            program = ctx.kis_api.raw_get(
                "FHPPG04600101",
                "domestic-stock/v1/quotations/comp-program-trade-today",
                {
                    # 스펙 문서의 FID_COND_MRKT_DIV_CODE1만으로는 "ERROR INPUT FIELD
                    # NOT FOUND [FID_COND_MRKT_DIV_CODE]" 오류 발생 — 라이브 프로브로
                    # FID_COND_MRKT_DIV_CODE="J"가 추가로 필요함을 확인 후 보강.
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_COND_MRKT_DIV_CODE1": "",
                    "FID_MRKT_CLS_CODE": "K",
                    "FID_SCTN_CLS_CODE": "",
                    "FID_INPUT_ISCD": "",
                    "FID_INPUT_HOUR_1": "",
                },
            )
            program_rows = program.get("output", [])
            result["program_net_buy_krw"] = (
                _to_float(program_rows[0].get("whol_smtn_ntby_tr_pbmn")) * 1_000_000
                if program_rows
                else None
            )
            if not program_rows:
                missing_fields.append("program_net_buy_krw")
        except Exception:
            result["program_net_buy_krw"] = None
            missing_fields.append("program_net_buy_krw")

        # 시장별 투자자매매동향(일별) — 최근 5거래일 (백만원 단위 → KRW 변환)
        try:
            today = datetime.now().strftime("%Y%m%d")
            investor = ctx.kis_api.raw_get(
                "FHPTJ04040000",
                "domestic-stock/v1/quotations/inquire-investor-daily-by-market",
                {
                    # 스펙에 FID_COND_MRKT_DIV_CODE 명시 없음 — 누락 시 "ERROR INPUT
                    # FIELD NOT FOUND [FID_COND_MRKT_DIV_CODE]" 발생, "J"는 INVALID,
                    # "U"(업종)로 라이브 확인.
                    "FID_COND_MRKT_DIV_CODE": "U",
                    "FID_INPUT_ISCD": "0001",
                    "FID_INPUT_DATE_1": today,
                    "FID_INPUT_ISCD_1": "KSP",
                    "FID_INPUT_DATE_2": today,
                    "FID_INPUT_ISCD_2": "0001",
                },
            )
            investor_rows = investor.get("output", [])
            result["investor_trend_days"] = [
                {
                    "date": row.get("stck_bsop_date"),
                    "foreign_net_krw": _to_float(row.get("frgn_ntby_tr_pbmn")) * 1_000_000,
                    "institution_net_krw": _to_float(row.get("orgn_ntby_tr_pbmn")) * 1_000_000,
                    "individual_net_krw": _to_float(row.get("prsn_ntby_tr_pbmn")) * 1_000_000,
                }
                for row in investor_rows[:5]
            ]
            if not investor_rows:
                missing_fields.append("investor_trend_days")
        except Exception:
            result["investor_trend_days"] = None
            missing_fields.append("investor_trend_days")

        if missing_fields:
            result["missing_fields"] = missing_fields

        return result

    return ctx.cached_call("get_market_context", phase, {}, fetch)


def get_sector_breadth(ctx: MILContext, phase: str) -> dict:
    """업종별 지수·등락률 + 상승/하락/보합/상한/하한 종목 수 (브레드스 통합)."""

    # 합계/규모별 지수 행 — 산업별 행만 남기지 않으면 breadth 합산이 이중계산된다.
    _AGGREGATE_CODES = {"0001", "0002", "0003", "0004"}  # 종합/대형주/중형주/소형주

    def fetch():
        raw = ctx.kis_api.raw_get(
            "FHPUP02140000",
            "domestic-stock/v1/quotations/inquire-index-category-price",
            {
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_COND_SCR_DIV_CODE": "20214",
                "FID_INPUT_ISCD": "0001",
                "FID_MRKT_CLS_CODE": "K",
                "FID_BLNG_CLS_CODE": "0",
            },
        )
        sectors = [
            {
                "sector_name": row.get("hts_kor_isnm"),
                "sector_code": row.get("bstp_cls_code"),
                "change_pct": _to_float(row.get("bstp_nmix_prdy_ctrt")),
                "advancers": _to_int(row.get("ascn_issu_cnt")),
                "decliners": _to_int(row.get("down_issu_cnt")),
                "unchanged": _to_int(row.get("stnr_issu_cnt")),
                "upper_limit": _to_int(row.get("uplm_issu_cnt")),
                "lower_limit": _to_int(row.get("lslm_issu_cnt")),
            }
            for row in raw.get("output2", [])
            if row.get("bstp_cls_code") not in _AGGREGATE_CODES
        ]
        return {"sectors": sectors}

    return ctx.cached_call("get_sector_breadth", phase, {}, fetch)


def get_intraday_index_candles(ctx: MILContext, phase: str, index_code: str = "0001") -> dict:
    """업종 분봉 (VWAP 기준선 파악용)."""

    def fetch():
        raw = ctx.kis_api.raw_get(
            "FHKUP03500200",
            "domestic-stock/v1/quotations/inquire-time-indexchartprice",
            {
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_INPUT_ISCD": index_code,
                "FID_INPUT_HOUR_1": "60",
                "FID_PW_DATA_INCU_YN": "Y",
            },
        )
        candles = [
            {
                "time": row.get("stck_cntg_hour"),
                "open": _to_float(row.get("bstp_nmix_oprc")),
                "high": _to_float(row.get("bstp_nmix_hgpr")),
                "low": _to_float(row.get("bstp_nmix_lwpr")),
                "close": _to_float(row.get("bstp_nmix_prpr")),
                "volume": _to_float(row.get("acml_vol")),
            }
            for row in raw.get("output2", [])
        ]
        return {"index_code": index_code, "candles": candles}

    return ctx.cached_call("get_intraday_index_candles", phase, {"index_code": index_code}, fetch)


def get_news_market(ctx: MILContext, phase: str) -> dict:
    """전체 시황/공시 제목 목록."""

    def fetch():
        raw = ctx.kis_api.raw_get(
            "FHKST01011800",
            "domestic-stock/v1/quotations/news-title",
            {
                "FID_NEWS_OFER_ENTP_CODE": "",
                "FID_COND_MRKT_CLS_CODE": "",
                "FID_INPUT_ISCD": "",
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
        return {"headlines": headlines}

    return ctx.cached_call("get_news_market", phase, {}, fetch)


def _to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _to_int(value) -> int:
    return int(_to_float(value))
