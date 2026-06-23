"""조건검색 도구 4개: psearch_title, psearch_result, get_top_movers, get_attention_rank"""
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


def get_attention_rank(ctx: MILContext, phase: str) -> dict:
    """실시간 시장 관심 종목 순위 — 두 소스 통합.

    ① 키움 빅데이터 실시간종목조회순위(ka00198, 1분 기준):
       지금 이 순간 키움 HTS 사용자들이 가장 많이 들여다보는 종목.
       bigd_rank=1이 가장 많이 조회된 종목. rank_chg_sign=+이면 순위 상승 중.

    ② KIS HTS 조회상위20종목(HHMCM000100C0):
       KIS eFriend Plus 사용자 기준 조회 상위 20종목. 종목코드만 반환.

    키움 API 미설정 시 ①은 missing_fields에 기록하고 ②만 반환한다.
    """

    def fetch():
        result: dict = {}
        missing_fields: list[str] = []

        # ① 키움 빅데이터 실시간 조회 순위
        if ctx.kiwoom_api and ctx.kiwoom_api.available:
            try:
                raw = ctx.kiwoom_api.realtime_viewing_rank(qry_tp="1")
                rows = raw.get("item_inq_rank") or []
                result["kiwoom_viewing_rank"] = [
                    {
                        "rank": _to_int(row.get("bigd_rank")),
                        "rank_change": row.get("rank_chg_sign", ""),
                        "ticker": str(row.get("stk_cd") or "").strip(),
                        "name": row.get("stk_nm", ""),
                        "change_pct": _to_float(row.get("base_comp_chgr")),
                    }
                    for row in rows
                    if row.get("stk_cd")
                ]
            except Exception as e:
                result["kiwoom_viewing_rank"] = None
                missing_fields.append(f"kiwoom_viewing_rank({e})")
        else:
            result["kiwoom_viewing_rank"] = None
            missing_fields.append("kiwoom_viewing_rank(credentials not configured)")

        # ② KIS HTS 조회 상위 20종목
        try:
            raw_kis = ctx.kis_api.raw_get(
                "HHMCM000100C0",
                "domestic-stock/v1/ranking/hts-top-view",
                {},
            )
            result["kis_hts_top"] = [
                str(row.get("mksc_shrn_iscd") or "").strip()
                for row in raw_kis.get("output1") or []
                if row.get("mksc_shrn_iscd")
            ]
        except Exception as e:
            result["kis_hts_top"] = None
            missing_fields.append(f"kis_hts_top({e})")

        if missing_fields:
            result["missing_fields"] = missing_fields
        return result

    return ctx.cached_call("get_attention_rank", phase, {}, fetch)


def kw_psearch_title(ctx: MILContext, phase: str) -> dict:
    """키움 ka10171 조건검색 목록조회. 영웅문4에 저장된 조건식 목록 반환.

    KIS psearch_title과 동일한 역할 — KIS 불안정 시 대체 경로.
    조건식이 없으면 data=[] 반환 (오류 아님, note에 기록).
    키움 API 미설정 또는 WebSocket 실패 시 missing_fields에 기록.
    """

    def fetch():
        result: dict = {}
        missing_fields: list[str] = []

        if not (ctx.kiwoom_api and ctx.kiwoom_api.available):
            result["conditions"] = None
            missing_fields.append("kw_psearch_title(credentials not configured)")
            result["missing_fields"] = missing_fields
            return result

        try:
            raw = ctx.kiwoom_api.search_list()
            conditions = raw.get("data") or []
            result["conditions"] = [
                {"seq": str(c.get("seq") or "").strip(), "name": c.get("name", "")}
                for c in conditions
            ]
            if not result["conditions"]:
                result["note"] = "영웅문4에 저장된 조건검색식이 없습니다. 영웅문4 > 조건검색 > 조건식 저장 후 사용하세요."
        except Exception as e:
            result["conditions"] = None
            missing_fields.append(f"kw_psearch_title({e})")

        if missing_fields:
            result["missing_fields"] = missing_fields
        return result

    return ctx.cached_call("kw_psearch_title", phase, {}, fetch)


def kw_psearch_result(ctx: MILContext, phase: str, seq: str) -> dict:
    """키움 ka10172 조건검색 요청 일반. seq = 조건식 일련번호.

    KIS psearch_result와 동일한 역할 — KIS 불안정 시 대체 경로.
    응답 ticker는 'A' 접두사 제거 처리됨.
    키움 API 미설정 또는 WebSocket 실패 시 missing_fields에 기록.
    """

    def fetch():
        result: dict = {"seq": seq}
        missing_fields: list[str] = []

        if not (ctx.kiwoom_api and ctx.kiwoom_api.available):
            result["candidates"] = None
            missing_fields.append("kw_psearch_result(credentials not configured)")
            result["missing_fields"] = missing_fields
            return result

        try:
            raw = ctx.kiwoom_api.search_result(seq=seq)
            if raw.get("return_code", 0) != 0:
                result["candidates"] = []
                result["note"] = f"키움 응답: {raw.get('return_msg', '')} (결과 0건이거나 조건식 오류)"
                return result
            data = raw.get("data") or []
            result["candidates"] = [
                {
                    "ticker": str(row.get("9001") or "").lstrip("A").strip(),
                    "name": str(row.get("302") or "").strip(),
                    "price": _to_float(row.get("10")),
                    "change_pct": _to_float(row.get("12")),
                    "volume": _to_float(row.get("13")),
                    "open": _to_float(row.get("16")),
                    "high": _to_float(row.get("17")),
                    "low": _to_float(row.get("18")),
                }
                for row in data
                if row.get("9001")
            ]
        except Exception as e:
            result["candidates"] = None
            missing_fields.append(f"kw_psearch_result({e})")

        if missing_fields:
            result["missing_fields"] = missing_fields
        return result

    return ctx.cached_call("kw_psearch_result", phase, {"seq": seq}, fetch)


def get_sector_investor_flow(ctx: MILContext, phase: str) -> dict:
    """키움 ka10051 업종별투자자순매수.

    업종별로 외국인·기관이 오늘 얼마나 순매수했는지 (금액 기준).
    orgn_netprps = 기관계 순매수, frgnr_netprps = 외국인 순매수 (양수=매수, 음수=매도).
    두 값 모두 양수인 업종 = 외인·기관 동시 유입 중인 핵심 섹터.
    키움 API 미설정 시 missing_fields에 기록.
    """

    def fetch():
        result: dict = {}
        missing_fields: list[str] = []

        if not (ctx.kiwoom_api and ctx.kiwoom_api.available):
            result["sectors"] = None
            missing_fields.append("sector_investor_flow(credentials not configured)")
            result["missing_fields"] = missing_fields
            return result

        try:
            raw = ctx.kiwoom_api.sector_investor_flow()
            rows = raw.get("inds_netprps") or []
            sectors = [
                {
                    "sector_code": row.get("inds_cd", ""),
                    "sector_name": row.get("inds_nm", ""),
                    # flu_rt는 정수 인코딩 (예: "-13" = -0.13%)
                    "change_pct": round(_to_float(row.get("flu_rt")) / 100, 2),
                    "volume": _to_float(row.get("trde_qty")),
                    "institution_net": _to_float(row.get("orgn_netprps")),
                    "foreign_net": _to_float(row.get("frgnr_netprps")),
                    "individual_net": _to_float(row.get("ind_netprps")),
                    "fund_net": _to_float(row.get("endw_netprps")),
                    "securities_net": _to_float(row.get("sc_netprps")),
                }
                for row in rows
                if row.get("inds_nm")
            ]
            # 정렬: 외인+기관 모두 양수인 섹터 최우선, 그 안에서 합계 내림차순
            sectors.sort(
                key=lambda s: (
                    1 if s["institution_net"] > 0 and s["foreign_net"] > 0 else 0,
                    s["institution_net"] + s["foreign_net"],
                ),
                reverse=True,
            )
            result["sectors"] = sectors
            result["note"] = "institution_net + foreign_net 합계 내림차순. 두 값 모두 양수 = 외인·기관 동시 유입 섹터."
        except Exception as e:
            result["sectors"] = None
            missing_fields.append(f"sector_investor_flow({e})")

        if missing_fields:
            result["missing_fields"] = missing_fields
        return result

    return ctx.cached_call("get_sector_investor_flow", phase, {}, fetch)


def get_bid_queue_surge(ctx: MILContext, phase: str) -> dict:
    """키움 ka10021 호가잔량급증. 코스피+코스닥 전체 매수잔량 급증 종목.

    sdnin_rt(급증률%) 높을수록 갑자기 매수 호가잔량이 폭발 — 세력 진입 직전 신호.
    tot_buy_qty = 총 매수 잔량. 상위 10개만 반환.
    키움 API 미설정 시 missing_fields에 기록.
    """

    def fetch():
        result: dict = {}
        missing_fields: list[str] = []

        if not (ctx.kiwoom_api and ctx.kiwoom_api.available):
            result["stocks"] = None
            missing_fields.append("bid_queue_surge(credentials not configured)")
            result["missing_fields"] = missing_fields
            return result

        merged: dict[str, dict] = {}  # ticker → row (중복 제거)
        # 코스피("001") + 코스닥("002") 각각 조회 후 합산
        for mrkt_tp in ("001", "002"):
            try:
                raw = ctx.kiwoom_api.bid_queue_surge(mrkt_tp=mrkt_tp)
                rows = raw.get("bid_req_sdnin") or []
                for row in rows:
                    ticker = str(row.get("stk_cd") or "").strip()
                    if not ticker:
                        continue
                    entry = {
                        "ticker": ticker,
                        "name": row.get("stk_nm", ""),
                        "price": _to_float(row.get("cur_prc")),
                        "base_qty": _to_float(row.get("int")),
                        "current_qty": _to_float(row.get("now")),
                        "surge_qty": _to_float(row.get("sdnin_qty")),
                        "surge_rate_pct": _to_float(row.get("sdnin_rt")),
                        "total_bid_qty": _to_float(row.get("tot_buy_qty")),
                    }
                    # 중복 시 급증률 높은 쪽 유지
                    if ticker not in merged or entry["surge_rate_pct"] > merged[ticker]["surge_rate_pct"]:
                        merged[ticker] = entry
            except Exception as e:
                missing_fields.append(f"bid_queue_surge(mrkt_tp={mrkt_tp}: {e})")

        # 급증률 내림차순 정렬, 상위 20개
        stocks = sorted(merged.values(), key=lambda x: x["surge_rate_pct"], reverse=True)
        result["stocks"] = stocks[:20]

        if missing_fields:
            result["missing_fields"] = missing_fields
        return result

    return ctx.cached_call("get_bid_queue_surge", phase, {}, fetch)


def get_premarket_movers(ctx: MILContext, phase: str) -> dict:
    """KIS FHPST01820000 예상체결 등락률 상위.

    장 시작 전(08:30~09:00) 또는 장 중 예상 갭업/갭다운 종목 스캔.
    antc_tr_pbmn = 예상 거래대금(원). stck_prpr = 예상체결가.
    """

    def fetch():
        raw = ctx.kis_api.raw_get(
            "FHPST01820000",
            "domestic-stock/v1/ranking/exp-trans-updown",
            {
                "fid_cond_mrkt_div_code": "J",
                "fid_cond_scr_div_code": "20182",
                "fid_input_iscd": "0000",
                "fid_div_cls_code": "0",
                "fid_aply_rang_prc_1": "",
                "fid_vol_cnt": "",
                "fid_pbmn": "",
                "fid_blng_cls_code": "0",
                "fid_mkop_cls_code": "0",
                "fid_rank_sort_cls_code": "0",
            },
        )
        rows = raw.get("output", []) or []
        movers = [
            {
                "ticker": row.get("stck_shrn_iscd"),
                "name": row.get("hts_kor_isnm"),
                "exp_price": _to_float(row.get("stck_prpr")),
                "base_price": _to_float(row.get("stck_sdpr")),
                "change_pct": _to_float(row.get("prdy_ctrt")),
                "exp_volume": _to_float(row.get("cntg_vol")),
                "exp_trading_value_krw": _to_float(row.get("antc_tr_pbmn")),
                "total_ask_qty": _to_float(row.get("total_askp_rsqn")),
                "total_bid_qty": _to_float(row.get("total_bidp_rsqn")),
            }
            for row in rows[:30]
            if row.get("stck_shrn_iscd")
        ]
        return {"movers": movers}

    return ctx.cached_call("get_premarket_movers", phase, {}, fetch)


def get_disparity_rank(ctx: MILContext, phase: str) -> dict:
    """KIS FHPST01780000 이격도 순위.

    d20_dsrt < 85 = 20일선 이격도 -15% 이상 → REVERSAL(낙주 스윙) 과매도 후보.
    fid_rank_sort_cls_code=1(이격도 낮은순) 으로 과매도 종목 상위 반환.
    """

    def fetch():
        raw = ctx.kis_api.raw_get(
            "FHPST01780000",
            "domestic-stock/v1/ranking/disparity",
            {
                "fid_cond_mrkt_div_code": "J",
                "fid_cond_scr_div_code": "20178",
                "fid_div_cls_code": "0",
                "fid_rank_sort_cls_code": "1",  # 이격도 낮은 순 (과매도 상위)
                "fid_hour_cls_code": "0000",
                "fid_input_iscd": "0000",
                "fid_trgt_cls_code": "0",
                "fid_trgt_exls_cls_code": "0",
                "fid_input_price_1": "",
                "fid_input_price_2": "",
                "fid_vol_cnt": "",
            },
        )
        rows = raw.get("output", []) or []
        stocks = [
            {
                "ticker": row.get("mksc_shrn_iscd"),
                "name": row.get("hts_kor_isnm"),
                "price": _to_float(row.get("stck_prpr")),
                "change_pct": _to_float(row.get("prdy_ctrt")),
                "volume": _to_float(row.get("acml_vol")),
                "d5_dsrt": _to_float(row.get("d5_dsrt")),
                "d10_dsrt": _to_float(row.get("d10_dsrt")),
                "d20_dsrt": _to_float(row.get("d20_dsrt")),
                "d60_dsrt": _to_float(row.get("d60_dsrt")),
                "d120_dsrt": _to_float(row.get("d120_dsrt")),
            }
            for row in rows[:30]
            if row.get("mksc_shrn_iscd")
        ]
        return {
            "stocks": stocks,
            "note": "d20_dsrt < 85이면 20일선 이격도 -15% 이상 — REVERSAL 낙주 스윙 과매도 후보",
        }

    return ctx.cached_call("get_disparity_rank", phase, {}, fetch)


def get_foreign_institution_rank(ctx: MILContext, phase: str) -> dict:
    """키움 ka90009 외국인기관매매상위.

    외인 순매수 상위 + 기관 순매수 상위를 분리해서 반환한다.
    두 리스트에 공통으로 등장하는 종목 = 외인·기관 동시 집중 매수 → 강한 수급 신호.
    키움 API 미설정 시 missing_fields에 기록하고 빈 결과 반환.
    """

    def fetch():
        result: dict = {}
        missing_fields: list[str] = []

        if not (ctx.kiwoom_api and ctx.kiwoom_api.available):
            result["foreign_netbuy_top"] = None
            result["institution_netbuy_top"] = None
            missing_fields.append("foreign_institution_rank(credentials not configured)")
            result["missing_fields"] = missing_fields
            return result

        try:
            raw = ctx.kiwoom_api.foreign_institution_top()
            rows = raw.get("frgnr_orgn_trde_upper") or []
            result["foreign_netbuy_top"] = [
                {
                    "ticker": str(row.get("for_netprps_stk_cd") or "").strip(),
                    "name": row.get("for_netprps_stk_nm", ""),
                    "netbuy_amount": _to_float(row.get("for_netprps_amt")),
                    "netbuy_qty": _to_float(row.get("for_netprps_qty")),
                }
                for row in rows
                if row.get("for_netprps_stk_cd")
            ]
            result["institution_netbuy_top"] = [
                {
                    "ticker": str(row.get("orgn_netprps_stk_cd") or "").strip(),
                    "name": row.get("orgn_netprps_stk_nm", ""),
                    "netbuy_amount": _to_float(row.get("orgn_netprps_amt")),
                    "netbuy_qty": _to_float(row.get("orgn_netprps_qty")),
                }
                for row in rows
                if row.get("orgn_netprps_stk_cd")
            ]
        except Exception as e:
            result["foreign_netbuy_top"] = None
            result["institution_netbuy_top"] = None
            missing_fields.append(f"foreign_institution_rank({e})")

        if missing_fields:
            result["missing_fields"] = missing_fields
        return result

    return ctx.cached_call("get_foreign_institution_rank", phase, {}, fetch)


def get_foreign_continuous_rank(ctx: MILContext, phase: str) -> dict:
    """키움 ka10035 외인연속순매매상위.

    dm1/dm2/dm3 = D-1/D-2/D-3 외인 순매수량, tot = 3일 합계.
    양수가 클수록 외국인이 강하게 연속 매수 중 — TREND/REGULATION_GAP 셋업의 수급 근거.
    키움 API 미설정 시 missing_fields에 기록.
    """

    def fetch():
        result: dict = {}
        missing_fields: list[str] = []

        if not (ctx.kiwoom_api and ctx.kiwoom_api.available):
            result["stocks"] = None
            missing_fields.append("foreign_continuous_rank(credentials not configured)")
            result["missing_fields"] = missing_fields
            return result

        try:
            raw = ctx.kiwoom_api.foreign_continuous_rank()
            rows = raw.get("for_cont_nettrde_upper") or []
            result["stocks"] = [
                {
                    "ticker": str(row.get("stk_cd") or "").strip(),
                    "name": row.get("stk_nm", ""),
                    "price": _to_float(row.get("cur_prc")),
                    "change_pct": _to_float(row.get("pred_pre")),
                    "d1_qty": _to_float(row.get("dm1")),
                    "d2_qty": _to_float(row.get("dm2")),
                    "d3_qty": _to_float(row.get("dm3")),
                    "total_3d_qty": _to_float(row.get("tot")),
                    "foreign_limit_pct": _to_float(row.get("limit_exh_rt")),
                }
                for row in rows
                if row.get("stk_cd")
            ]
        except Exception as e:
            result["stocks"] = None
            missing_fields.append(f"foreign_continuous_rank({e})")

        if missing_fields:
            result["missing_fields"] = missing_fields
        return result

    return ctx.cached_call("get_foreign_continuous_rank", phase, {}, fetch)


def get_volume_surge(ctx: MILContext, phase: str) -> dict:
    """키움 ka10023 거래량급증.

    prev_trde_qty = 전일 동시간대 거래량, now_trde_qty = 현재 거래량.
    sdnin_rt(급증률 %) 높을수록 '역대급 거래대금이 들어오며 매물을 소화 중' 신호.
    키움 API 미설정 시 missing_fields에 기록.
    """

    def fetch():
        result: dict = {}
        missing_fields: list[str] = []

        if not (ctx.kiwoom_api and ctx.kiwoom_api.available):
            result["stocks"] = None
            missing_fields.append("volume_surge(credentials not configured)")
            result["missing_fields"] = missing_fields
            return result

        try:
            raw = ctx.kiwoom_api.volume_surge()
            rows = raw.get("trde_qty_sdnin") or []
            result["stocks"] = [
                {
                    "ticker": str(row.get("stk_cd") or "").strip(),
                    "name": row.get("stk_nm", ""),
                    "price": _to_float(row.get("cur_prc")),
                    "change_pct": _to_float(row.get("flu_rt")),
                    "prev_volume": _to_float(row.get("prev_trde_qty")),
                    "now_volume": _to_float(row.get("now_trde_qty")),
                    "surge_qty": _to_float(row.get("sdnin_qty")),
                    "surge_rate_pct": _to_float(row.get("sdnin_rt")),
                }
                for row in rows
                if row.get("stk_cd")
            ]
        except Exception as e:
            result["stocks"] = None
            missing_fields.append(f"volume_surge({e})")

        if missing_fields:
            result["missing_fields"] = missing_fields
        return result

    return ctx.cached_call("get_volume_surge", phase, {}, fetch)


def get_intraday_investor_rank(ctx: MILContext, phase: str) -> dict:
    """키움 ka10065 장중투자자별매매상위.

    기관(trde_tp=1) + 외인(trde_tp=2) 순매수 상위를 동시에 반환한다.
    netslmt 양수 = 순매수, 음수 = 순매도.
    두 리스트에 공통으로 등장하는 종목 = 기관·외인 동시 매수 → 강력한 장중 수급 신호.
    키움 API 미설정 시 missing_fields에 기록.
    """

    def fetch():
        result: dict = {}
        missing_fields: list[str] = []

        if not (ctx.kiwoom_api and ctx.kiwoom_api.available):
            result["institution_rank"] = None
            result["foreign_rank"] = None
            missing_fields.append("intraday_investor_rank(credentials not configured)")
            result["missing_fields"] = missing_fields
            return result

        for key, trde_tp in [("institution_rank", "1"), ("foreign_rank", "2")]:
            try:
                raw = ctx.kiwoom_api.intraday_investor_rank(trde_tp=trde_tp)
                rows = raw.get("opmr_invsr_trde_upper") or []
                result[key] = [
                    {
                        "ticker": str(row.get("stk_cd") or "").strip(),
                        "name": row.get("stk_nm", ""),
                        "sell_amount": _to_float(row.get("sel_qty")),
                        "buy_amount": _to_float(row.get("buy_qty")),
                        "net_buy": _to_float(row.get("netslmt")),
                    }
                    for row in rows
                    if row.get("stk_cd")
                ]
            except Exception as e:
                result[key] = None
                missing_fields.append(f"{key}({e})")

        if missing_fields:
            result["missing_fields"] = missing_fields
        return result

    return ctx.cached_call("get_intraday_investor_rank", phase, {}, fetch)


def get_limit_up_stocks(ctx: MILContext, phase: str) -> dict:
    """당일 상한가(29.9%) 또는 상한가 근접(25%↑) 종목 리스트.

    v4 세력주 매매의 핵심 탐지 도구. 등락률 순위 API(FHPST01700000)에서
    change_pct >= 25% 종목을 추출한다. is_limit_up은 29% 이상 여부.
    """

    def fetch():
        raw = ctx.kis_api.raw_get(
            "FHPST01700000",
            "domestic-stock/v1/ranking/fluctuation",
            {
                "fid_cond_mrkt_div_code": "J",
                "fid_cond_scr_div_code": "20170",
                "fid_input_iscd": "0000",
                "fid_rank_sort_cls_code": "0",   # 등락률 높은 순
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
        rows = raw.get("output", []) or []
        stocks = []
        for row in rows:
            change_pct = _to_float(row.get("prdy_ctrt"))
            if change_pct < 25.0:
                continue
            trading_value = _to_float(row.get("acml_tr_pbmn"))
            if trading_value == 0:
                price = _to_float(row.get("stck_prpr"))
                volume = _to_float(row.get("acml_vol"))
                trading_value = price * volume
            stocks.append({
                "ticker": row.get("mksc_shrn_iscd"),
                "name": row.get("hts_kor_isnm"),
                "change_pct": change_pct,
                "trading_value_krw": trading_value,
                "is_limit_up": change_pct >= 29.0,
            })
        return {"stocks": stocks}

    return ctx.cached_call("get_limit_up_stocks", phase, {}, fetch)


def _to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _to_int(value) -> int:
    return int(_to_float(value))
