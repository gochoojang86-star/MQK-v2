"""시장관찰 도구 4개: get_market_context, get_sector_breadth, get_intraday_index_candles, get_news_market"""
from __future__ import annotations

from datetime import datetime

from broker.telegram_news import get_recent_news
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

        # get_index_status는 KIS raw 문자열을 그대로 반환할 수 있다 ("8342.33" 등).
        # MIL 경계에서 숫자로 정규화 — 드리프트 스냅샷 검증과 LLM 컨텍스트 일관성 보장.
        result = {
            "kospi": _to_float(index_status.get("kospi")),
            "kospi_change_pct": _to_float(index_status.get("kospi_change_pct")),
            "kosdaq": _to_float(index_status.get("kosdaq")),
            "kosdaq_change_pct": _to_float(index_status.get("kosdaq_change_pct")),
            "kospi_advancers": _to_int(index_status.get("kospi_advancers")),
            "kospi_decliners": _to_int(index_status.get("kospi_decliners")),
            "foreign_net_buy_krw": foreign_net,
            "institution_net_buy_krw": institution_net,
            "prev_kospi_change_pct": _to_float(index_status.get("prev_kospi_change_pct")),
            "prev_kospi_trading_value": _to_float(index_status.get("prev_kospi_trading_value")),
            "prev_kosdaq_change_pct": _to_float(index_status.get("prev_kosdaq_change_pct")),
            "prev_kosdaq_trading_value": _to_float(index_status.get("prev_kosdaq_trading_value")),
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
    """시장 전체 브레드스(output1) + 업종별 등락률/거래대금 비중(output2).

    상승/하락/보합/상한/하한 종목 수는 기준지수(output1)에만 제공된다 —
    업종별 행(output2)에는 해당 필드가 없다 (D1 라이브 테스트로 확인).
    """

    # 합계/규모별 지수 행 — 산업별 행만 남긴다.
    _AGGREGATE_CODES = {
        "0001", "0002", "0003", "0004",    # 종합/대형주/중형주/소형주
        "0027",                             # 제조(전체 제조업 합산 — 업종별과 중복)
        "0163", "0164", "0165",             # 고배당50/배당성장50/우선주 (테마 인덱스)
        "0195", "0241", "0242", "0244",     # TR 계열 / 코스피200제외 지수 (중복 합산)
        "2180", "2283",                     # ESG/기후변화 지수 (섹터 아님)
        "0503",                             # VKOSPI (변동성 지수)
    }

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
        out1 = raw.get("output1", {}) or {}
        market_breadth = {
            "advancers": _to_int(out1.get("ascn_issu_cnt")),
            "decliners": _to_int(out1.get("down_issu_cnt")),
            "unchanged": _to_int(out1.get("stnr_issu_cnt")),
            "upper_limit": _to_int(out1.get("uplm_issu_cnt")),
            "lower_limit": _to_int(out1.get("lslm_issu_cnt")),
        }
        sectors = [
            {
                "sector_name": row.get("hts_kor_isnm"),
                "sector_code": row.get("bstp_cls_code"),
                "change_pct": _to_float(row.get("bstp_nmix_prdy_ctrt")),
                "trading_value_share_pct": _to_float(row.get("acml_vol_rlim")),
            }
            for row in raw.get("output2", [])
            if row.get("bstp_cls_code") not in _AGGREGATE_CODES
        ]
        return {"market_breadth": market_breadth, "sectors": sectors}

    return ctx.cached_call("get_sector_breadth", phase, {}, fetch)


def get_intraday_index_candles(ctx: MILContext, phase: str, index_code: str = "0001") -> dict:
    """업종 분봉 (VWAP 기준선 파악용)."""

    def fetch():
        raw = ctx.kis_api.raw_get(
            "FHKUP03500200",
            "domestic-stock/v1/quotations/inquire-time-indexchartprice",
            {
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_ETC_CLS_CODE": "0",
                "FID_INPUT_ISCD": index_code,
                "FID_INPUT_HOUR_1": "60",
                # N=당일만 — Y(과거 포함)는 전일 분봉이 섞여 당일 시가/저가 계산을
                # 오염시킨다 (드리프트 스냅샷의 kospi_open/kospi_low 입력).
                "FID_PW_DATA_INCU_YN": "N",
            },
        )
        candles = [
            {
                "time": row.get("stck_cntg_hour"),
                "open": _to_float(row.get("bstp_nmix_oprc")),
                "high": _to_float(row.get("bstp_nmix_hgpr")),
                "low": _to_float(row.get("bstp_nmix_lwpr")),
                "close": _to_float(row.get("bstp_nmix_prpr")),
                "volume": _to_float(row.get("cntg_vol")),
            }
            for row in raw.get("output2", [])
        ]
        # KIS 응답은 최신 분봉 우선 — 시간 오름차순으로 정렬해 candles[0]이
        # 개장 분봉(당일 시가)이 되도록 보장한다 (_collect_drift_snapshot 가정).
        candles.sort(key=lambda c: c["time"] or "")
        return {"index_code": index_code, "candles": candles}

    return ctx.cached_call("get_intraday_index_candles", phase, {"index_code": index_code}, fetch)


def get_news_market(ctx: MILContext, phase: str) -> dict:
    """전체 시황/공시 제목 목록 + 텔레그램 속보 (최근 2시간, 시장 전체)."""

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
        result: dict = {"headlines": headlines}

        # 텔레그램 속보 (수집기 mqk-telegram-news가 sqlite에 적재) — 실패 격리
        try:
            result["telegram_headlines"] = [
                {"title": n.get("title"), "ticker": n.get("ticker"),
                 "sentiment": n.get("sentiment"), "source": n.get("source"), "date": n.get("date")}
                for n in get_recent_news(ticker="", hours=2)[:15]
            ]
        except Exception:
            result["telegram_headlines"] = []
            result["missing_fields"] = ["telegram_headlines"]
        return result

    return ctx.cached_call("get_news_market", phase, {}, fetch)


def get_us_market_context(ctx: MILContext, phase: str) -> dict:
    """미국 증시 야간 등락률, VIX, 달러/원 환율.

    나스닥/S&P500/다우/환율: KIS FHKST03030100 (실전계좌 토큰 사용, 모의도 지원).
    VIX: KIS 미제공 → yfinance 유지.
    캐시 TTL 300초 — 장전 레짐 평가 1회 호출로 충분.
    """
    from datetime import timedelta

    def _fetch_kis_index(mkt_code: str, iscd: str) -> tuple[float | None, float | None]:
        """KIS FHKST03030100으로 해외지수/환율 전일 종가 + 등락률 조회."""
        today = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")
        try:
            r = ctx.kis_api.raw_get(
                "FHKST03030100",
                "overseas-price/v1/quotations/inquire-daily-chartprice",
                {
                    "FID_COND_MRKT_DIV_CODE": mkt_code,
                    "FID_INPUT_ISCD": iscd,
                    "FID_INPUT_DATE_1": start,
                    "FID_INPUT_DATE_2": today,
                    "FID_PERIOD_DIV_CODE": "D",
                },
            )
            o1 = r.get("output1", {}) or {}
            last = _to_float(o1.get("ovrs_nmix_prdy_clpr") or o1.get("ovrs_nmix_prpr"))
            chg = _to_float(o1.get("prdy_ctrt"))
            if last and last > 0:
                return round(last, 4), round(chg, 2)
            # output2에서 가장 최근 값
            rows = r.get("output2") or []
            if rows:
                last2 = _to_float(rows[0].get("ovrs_nmix_clpr"))
                prev2 = _to_float(rows[1].get("ovrs_nmix_clpr")) if len(rows) > 1 else last2
                chg2 = round((last2 - prev2) / prev2 * 100, 2) if prev2 else 0.0
                return round(last2, 4), chg2
        except Exception:
            pass
        return None, None

    def fetch() -> dict:
        result: dict = {}
        missing: list[str] = []

        # ── KIS API: 나스닥, S&P500, 다우존스, USD/KRW ──────────────────────────
        kis_targets = [
            ("nasdaq",  "N", "COMP"),    # 나스닥 Composite
            ("sp500",   "N", "SPX"),     # S&P 500
            ("dow",     "N", ".DJI"),    # 다우존스
            ("usdkrw",  "X", "FX@KRW"), # 달러/원 환율
        ]
        for key, mkt, iscd in kis_targets:
            val, chg = _fetch_kis_index(mkt, iscd)
            result[key] = val
            result[f"{key}_change_pct"] = chg
            if val is None:
                missing.append(key)

        # ── yfinance: VIX (KIS 미제공) ──────────────────────────────────────────
        try:
            import yfinance as yf  # type: ignore[import]
            hist = yf.Ticker("^VIX").history(period="2d")
            if not hist.empty:
                last_v = float(hist["Close"].iloc[-1])
                prev_v = float(hist["Close"].iloc[-2]) if len(hist) > 1 else last_v
                result["vix"] = round(last_v, 2)
                result["vix_change_pct"] = round((last_v - prev_v) / prev_v * 100, 2) if prev_v else 0.0
            else:
                result["vix"] = None
                result["vix_change_pct"] = None
                missing.append("vix(empty)")
        except Exception as e:
            result["vix"] = None
            result["vix_change_pct"] = None
            missing.append(f"vix({e})")

        if missing:
            result["missing_fields"] = missing
        return result

    return ctx.cached_call("get_us_market_context", phase, {}, fetch)


def _to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _to_int(value) -> int:
    return int(_to_float(value))
