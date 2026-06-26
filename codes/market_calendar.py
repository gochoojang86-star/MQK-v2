"""
Market Calendar - 한국 주식시장 휴장일 판단
흑자봇(blacker) marketCalendar.ts 기반, Python 포팅 + 우선순위 재조정.

우선순위:
  1순위: 파일 캐시          — 당일 결과 저장돼 있으면 API 불필요
  2순위: 주말+하드코딩 공휴일 — 캐시 없을 때 로컬 즉시 판단
  3순위: KIS chk-holiday API — 결과를 파일에 저장해 당일 재호출 방지
  4순위: 공공데이터포털 API  — KIS 실패 시 fallback
  5순위: 하드코딩 폴백       — 전부 실패 시

매일 00:30 run_schedule_v3.py → run_holiday_check()에서 호출해 캐시 갱신.
이후 v3 각 phase는 캐시만 읽어 즉시 판단.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE_PATH = Path(__file__).parent.parent / "data" / "market-calendar-cache.json"

# 2026년 공휴일 — 공공데이터포털 SpcdeInfoService (isHoliday=Y) + KRX 특수 휴장
# 출처: http://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo
# 조회일: 2026-06-04
_HOLIDAYS_2026: frozenset[str] = frozenset({
    "20260101",  # 1월1일
    "20260216",  # 설날
    "20260217",  # 설날
    "20260218",  # 설날
    "20260301",  # 삼일절
    "20260302",  # 대체공휴일(삼일절)
    "20260501",  # 노동절
    "20260505",  # 어린이날
    "20260524",  # 부처님오신날
    "20260525",  # 대체공휴일(부처님오신날)
    "20260603",  # 전국동시지방선거
    "20260606",  # 현충일
    "20260717",  # 제헌절
    "20260815",  # 광복절
    "20260817",  # 대체공휴일(광복절)
    "20260924",  # 추석
    "20260925",  # 추석
    "20260926",  # 추석
    "20261003",  # 개천절
    "20261005",  # 대체공휴일(개천절)
    "20261009",  # 한글날
    "20261225",  # 기독탄신일
    "20261231",  # KRX 연말 휴장 (공공데이터 미포함, 거래소 특수 휴장)
})

KST = timezone(timedelta(hours=9))


def _today_kst() -> datetime:
    return datetime.now(KST)


def _date_str(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


def _is_weekend(dt: datetime) -> bool:
    return dt.weekday() >= 5  # 5=토, 6=일


def _hardcoded_is_trading_day(dt: datetime) -> bool:
    if _is_weekend(dt):
        return False
    return _date_str(dt) not in _HOLIDAYS_2026


# ── 캐시 ────────────────────────────────────────────────────────────────────

def _read_cache() -> Optional[dict]:
    try:
        if _CACHE_PATH.exists():
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _write_cache(date_str: str, trading_day: bool) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(
            json.dumps({"date": date_str, "tradingDay": trading_day}),
            encoding="utf-8",
        )
    except OSError:
        pass


# ── 3순위: KIS chk-holiday API ───────────────────────────────────────────────

def _check_via_kis(date_str: str) -> Optional[bool]:
    """KIS chk-holiday API 호출. 실전 계정 토큰 필요."""
    try:
        from broker.kis_api import KISApi, KISMode, KISConfig
        kis = KISApi(config=KISConfig(mode=KISMode.REAL))
        token = kis._get_token(KISMode.REAL)
        app_key = os.environ.get("KIS_REAL_APP_KEY", "")
        app_secret = os.environ.get("KIS_REAL_APP_SECRET", "")
        if not app_key or not app_secret:
            return None

        url = (
            "https://openapi.koreainvestment.com:9443"
            "/uapi/domestic-stock/v1/quotations/chk-holiday"
            f"?BASS_DT={date_str}&CTX_AREA_NK=&CTX_AREA_FK="
        )
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "appkey": app_key,
            "appsecret": app_secret,
            "tr_id": "FHKST01030000",
            "content-type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        if data.get("rt_cd") != "0":
            return None
        bzdy = (data.get("output") or [{}])[0].get("bzdy_yn")
        if bzdy not in ("Y", "N"):
            return None
        return bzdy == "Y"
    except Exception as e:
        logger.warning(f"[MarketCalendar] KIS API 실패: {e}")
        return None


# ── 4순위: 공공데이터포털 API ────────────────────────────────────────────────

def _check_via_open_data(date_str: str) -> Optional[bool]:
    """공공데이터포털 SpcdeInfoService 호출."""
    key = os.environ.get("OPEN_DATA_API_KEY", "")
    if not key:
        return None
    try:
        year = date_str[:4]
        url = (
            "http://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo"
            f"?serviceKey={key}&solYear={year}&numOfRows=100"
        )
        with urllib.request.urlopen(url, timeout=10) as resp:
            xml = resp.read().decode("utf-8")

        holidays: set[str] = set()
        for item in re.findall(r"<item>(.*?)</item>", xml, re.DOTALL):
            m_date = re.search(r"<locdate>(\d+)</locdate>", item)
            m_hol = re.search(r"<isHoliday>(Y|N)</isHoliday>", item)
            if m_date and m_hol and m_hol.group(1) == "Y":
                holidays.add(m_date.group(1))

        dt = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=KST)
        # 연말(12/31)은 공공데이터에 없어도 KRX 휴장
        is_year_end = dt.month == 12 and dt.day == 31
        trading = not _is_weekend(dt) and date_str not in holidays and not is_year_end
        return trading
    except Exception as e:
        logger.warning(f"[MarketCalendar] 공공데이터포털 실패: {e}")
        return None


# ── 메인 API ────────────────────────────────────────────────────────────────

def check_trading_day(date: Optional[datetime] = None) -> bool:
    """당일 영업일 여부 판단 (우선순위 1~5순위 적용).

    결과는 data/market-calendar-cache.json에 캐시.
    run_holiday_check()에서 매일 00:30에 호출.
    이후 단계별 실행에서는 read_cached_trading_day()로 즉시 반환.
    """
    dt = (date or _today_kst()).astimezone(KST)
    date_str = _date_str(dt)

    # ── 1순위: 파일 캐시 ──────────────────────────────────────────────────
    cache = _read_cache()
    if cache and cache.get("date") == date_str:
        result = bool(cache.get("tradingDay"))
        logger.info(f"[MarketCalendar] 1순위 파일캐시 → {'영업일' if result else '휴장일'}")
        return result

    # ── 2순위: 주말 + 하드코딩 공휴일 ────────────────────────────────────
    if not _hardcoded_is_trading_day(dt):
        _write_cache(date_str, False)
        logger.info("[MarketCalendar] 2순위 하드코딩 → 휴장일 확정, API 스킵")
        return False

    # ── 3순위: KIS chk-holiday API ────────────────────────────────────────
    result = _check_via_kis(date_str)
    if result is not None:
        _write_cache(date_str, result)
        logger.info(f"[MarketCalendar] 3순위 KIS API → {'영업일' if result else '휴장일'} (캐시 저장)")
        return result

    # ── 4순위: 공공데이터포털 API ─────────────────────────────────────────
    result = _check_via_open_data(date_str)
    if result is not None:
        _write_cache(date_str, result)
        logger.info(f"[MarketCalendar] 4순위 공공데이터 → {'영업일' if result else '휴장일'} (캐시 저장)")
        return result

    # ── 5순위: 하드코딩 폴백 ─────────────────────────────────────────────
    result = _hardcoded_is_trading_day(dt)
    _write_cache(date_str, result)
    logger.warning(f"[MarketCalendar] 5순위 하드코딩 폴백 → {'영업일' if result else '휴장일'}")
    return result


def read_cached_trading_day() -> Optional[bool]:
    """캐시에서 오늘 영업일 여부만 읽는다 (API 호출 없음).

    08:00/08:30/intraday/15:30 단계에서 호출 — 00:30 check_trading_day() 결과 재사용.
    캐시 없거나 날짜 불일치 → None 반환 (호출자가 check_trading_day()로 폴백).
    """
    date_str = _date_str(_today_kst())
    cache = _read_cache()
    if cache and cache.get("date") == date_str:
        return bool(cache.get("tradingDay"))
    return None
