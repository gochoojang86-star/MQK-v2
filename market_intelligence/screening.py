"""조건검색 도구 3개: psearch_title, psearch_result, get_top_movers"""
from __future__ import annotations

from market_intelligence.base import MILContext


def psearch_title(ctx: MILContext, phase: str, user_id: str) -> dict:
    """저장된 HTS 조건검색식 목록 조회."""

    def fetch():
        raw = ctx.kis_api.raw_get(
            "HHKST03900300",
            "domestic-stock/v1/quotations/psearch-title",
            {"USER_ID": user_id},
        )
        conditions = [
            {"seq": row.get("seq"), "name": row.get("condition_nm")}
            for row in raw.get("output2", [])
        ]
        return {"conditions": conditions}

    return ctx.cached_call("psearch_title", phase, {"user_id": user_id}, fetch)


def psearch_result(ctx: MILContext, phase: str, user_id: str, seq: str) -> dict:
    """저장된 조건검색식 실행 결과. 52주 고저가/시가총액 포함."""

    def fetch():
        raw = ctx.kis_api.raw_get(
            "HHKST03900400",
            "domestic-stock/v1/quotations/psearch-result",
            {"USER_ID": user_id, "SEQ": seq},
        )
        candidates = [
            {
                "ticker": row.get("code"),
                "name": row.get("name"),
                "price": _to_float(row.get("price")),
                "change_pct": _to_float(row.get("chgrate")),
                "volume": _to_float(row.get("acml_vol")),
                "trading_value": _to_float(row.get("acml_tr_pbmn")),
                "high_52w": _to_float(row.get("stck_dryy_hgpr")),
                "low_52w": _to_float(row.get("stck_dryy_lwpr")),
                "market_cap": _to_float(row.get("mrkt_total_amt")),
            }
            for row in raw.get("output2", [])
        ]
        return {"seq": seq, "candidates": candidates}

    return ctx.cached_call("psearch_result", phase, {"user_id": user_id, "seq": seq}, fetch)


def get_top_movers(ctx: MILContext, phase: str) -> dict:
    """psearch 실패 시 백업: 거래량순위. 과열주 편향 경고 플래그 포함."""

    def fetch():
        raw = ctx.kis_api.raw_get(
            "FHPST01710000",
            "domestic-stock/v1/quotations/volume-rank",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20171",
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "0",
                "FID_BLNG_CLS_CODE": "0",
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "0000000000",
                "FID_INPUT_PRICE_1": "",
                "FID_INPUT_PRICE_2": "",
                "FID_VOL_CNT": "",
                "FID_INPUT_DATE_1": "",
            },
        )
        movers = [
            {
                "ticker": row.get("mksc_shrn_iscd"),
                "name": row.get("hts_kor_isnm"),
                "price": _to_float(row.get("stck_prpr")),
                "change_pct": _to_float(row.get("prdy_ctrt")),
                "volume": _to_float(row.get("acml_vol")),
            }
            for row in raw.get("output", [])
        ]
        return {
            "movers": movers,
            "overheated_bias_warning": True,
            "warning_reason": "psearch 실패로 거래량순위 백업 사용 — 단기 과열주 비중이 높을 수 있음",
        }

    return ctx.cached_call("get_top_movers", phase, {}, fetch)


def _to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0
