"""조건검색 도구 3개: psearch_title, psearch_result, get_top_movers"""
from __future__ import annotations

from market_intelligence.base import MILContext


def psearch_title(ctx: MILContext, phase: str, user_id: str) -> dict:
    """저장된 HTS 조건검색식 목록 조회."""

    def fetch():
        raw = ctx.kis_api.raw_get(
            "HHKST03900300",
            "domestic-stock/v1/quotations/psearch-title",
            {"user_id": user_id},
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
            {"user_id": user_id, "seq": seq},
        )
        # KIS는 조건검색 결과 0건일 때 rt_cd=1("종목코드 오류입니다")을 반환하기도 한다.
        # 오류를 빈 결과로 가리지 않고 note로 노출해 LLM이 0건/오류를 구분하게 한다.
        if raw.get("rt_cd") != "0":
            return {"seq": seq, "candidates": [],
                    "note": f"KIS 응답: {raw.get('msg1', '')} (결과 0건이거나 조건식 오류일 수 있음)"}
        candidates = [
            {
                "ticker": row.get("code"),
                "name": row.get("name"),
                "price": _to_float(row.get("price")),
                "change_pct": _to_float(row.get("chgrate")),
                "volume": _to_float(row.get("acml_vol")),
                "trading_value": _to_float(row.get("trade_amt")),
                "volume_power": _to_float(row.get("cttr")),
                "prev_volume_ratio_pct": _to_float(row.get("chgrate2")),
                "high_52w": _to_float(row.get("high52")),
                "low_52w": _to_float(row.get("low52")),
                "market_cap": _to_float(row.get("stotprice")),
            }
            for row in raw.get("output2", [])
        ]
        return {"seq": seq, "candidates": candidates}

    return ctx.cached_call("psearch_result", phase, {"user_id": user_id, "seq": seq}, fetch)


def get_top_movers(ctx: MILContext, phase: str) -> dict:
    """psearch 실패 시 백업: 거래량순위. 과열주 편향 경고 플래그 포함.
    추가: 체결강도 상위, 등락률 순위."""

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
        volume_rows = raw.get("output", [])
        movers = [
            {
                "ticker": row.get("mksc_shrn_iscd"),
                "name": row.get("hts_kor_isnm"),
                "price": _to_float(row.get("stck_prpr")),
                "change_pct": _to_float(row.get("prdy_ctrt")),
                "volume": _to_float(row.get("acml_vol")),
                "trading_value_krw": _to_float(row.get("acml_tr_pbmn")),
            }
            for row in volume_rows
        ]
        # 거래대금 순위 — 거래량순위 응답에 acml_tr_pbmn이 포함되어 있어 별도 API 불필요
        trading_value_top = sorted(
            [m for m in movers if m["trading_value_krw"] > 0],
            key=lambda x: x["trading_value_krw"],
            reverse=True,
        )[:20]
        result = {
            "movers": movers,
            "trading_value_top": trading_value_top,
            "overheated_bias_warning": True,
            "warning_reason": "psearch 실패로 거래량순위 백업 사용 — 단기 과열주 비중이 높을 수 있음",
        }

        missing_fields: list[str] = []

        # 체결강도 상위
        try:
            power = ctx.kis_api.raw_get(
                "FHPST01680000",
                "domestic-stock/v1/ranking/volume-power",
                {
                    "fid_cond_mrkt_div_code": "J",
                    "fid_cond_scr_div_code": "20168",
                    "fid_input_iscd": "0000",
                    "fid_div_cls_code": "0",
                    "fid_input_price_1": "",
                    "fid_input_price_2": "",
                    "fid_vol_cnt": "",
                    "fid_trgt_cls_code": "0",
                    "fid_trgt_exls_cls_code": "0",
                },
            )
            power_rows = power.get("output", [])
            result["volume_power_top"] = [
                {
                    "ticker": row.get("stck_shrn_iscd"),
                    "name": row.get("hts_kor_isnm"),
                    "volume_power": _to_float(row.get("tday_rltv")),
                    "change_pct": _to_float(row.get("prdy_ctrt")),
                }
                for row in power_rows[:20]
            ]
            if not power_rows:
                missing_fields.append("volume_power_top")
        except Exception:
            result["volume_power_top"] = None
            missing_fields.append("volume_power_top")

        # 등락률 순위 (상승율순)
        try:
            change = ctx.kis_api.raw_get(
                "FHPST01700000",
                "domestic-stock/v1/ranking/fluctuation",
                {
                    "fid_cond_mrkt_div_code": "J",
                    "fid_cond_scr_div_code": "20170",
                    "fid_input_iscd": "0000",
                    "fid_rank_sort_cls_code": "0",
                    "fid_input_cnt_1": "0",
                    "fid_prc_cls_code": "1",
                    "fid_input_price_1": "",
                    "fid_input_price_2": "",
                    "fid_vol_cnt": "",
                    "fid_trgt_cls_code": "0",
                    "fid_trgt_exls_cls_code": "0",
                    "fid_div_cls_code": "0",
                    "fid_rsfl_rate1": "",
                    "fid_rsfl_rate2": "",
                },
            )
            change_rows = change.get("output", [])
            result["change_rate_top"] = [
                {
                    "ticker": row.get("stck_shrn_iscd"),
                    "name": row.get("hts_kor_isnm"),
                    "change_pct": _to_float(row.get("prdy_ctrt")),
                    # 등락률 순위 API 응답에 거래대금 필드가 없어 거래량×현재가로 근사
                    "trading_value_krw": _to_float(row.get("acml_vol")) * _to_float(row.get("stck_prpr")),
                }
                for row in change_rows[:20]
            ]
            if not change_rows:
                missing_fields.append("change_rate_top")
        except Exception:
            result["change_rate_top"] = None
            missing_fields.append("change_rate_top")

        if missing_fields:
            result["missing_fields"] = missing_fields

        return result

    return ctx.cached_call("get_top_movers", phase, {}, fetch)


def _to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0
