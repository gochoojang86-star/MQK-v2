#!/usr/bin/env python3
"""
MQK v3 자동 운영 진입점 (PM2 cron_restart로 각 단계별 실행)

MQK_PHASE 환경변수 (KST, ecosystem.config.cjs 기준):
  premarket_early - 08:50 장전거래 포지션 점검 (전일 종가 기준, 레짐은 참고용)
  premarket    - 09:03 장중 첫번째 레짐 판단 (시가/초반 흐름 반영) + risk_guidance/drift_triggers 생성
  scan         - 09:17 / 11:17 / 13:17 / 15:00 watchlist 생성/갱신 (각 레짐 평가 직후 14분 내)
  intraday     - 09:00~14:50 */10 드리프트 체크 + 매수/청산 proposal (당일 레짐 없거나 한가하면[watchlist 0+보유 0+STABLE] LLM 스킵)
  late_intraday - 15:08/15:13 폭락일 전용 과매도 낙주 종가 부근 진입 (지수 -3%↓ 또는 RED만, 아니면 LLM 미호출 스킵)
  close        - 15:18 정규장 내 청산 판단 (복기는 market_close가 수행)
  market_close - 17:00 장마감 분석 + 거래 복기 + 다음날 prior 생성

휴장일 가드는 v2와 동일하게 codes/market_calendar의 캐시를 사용한다.
"""
from __future__ import annotations

import fcntl
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("mqk_v3_schedule")

PHASE = os.environ.get("MQK_PHASE", "")

_LOCK_PATH = Path(__file__).parent / "data" / "mqk_v3.lock"

# phase별 운영 시간창 (KST "HH:MM"). PM2는 `pm2 start` 시점에 앱을 즉시 1회
# 실행하므로(cron_restart는 이후 재시작용), 스케줄 외 시각의 실행을 차단한다.
# 수동 강제 실행은 MQK_FORCE=1.
# 값은 tuple 또는 list[tuple] — 여러 시간 슬롯을 허용할 때 리스트로 지정한다.
_PHASE_WINDOWS: dict[str, tuple[str, str] | list[tuple[str, str]]] = {
    "premarket_early": ("08:45", "09:00"),
    "premarket": [("09:00", "09:10"), ("10:55", "11:10"), ("12:55", "13:10")],
    "scan": ("09:10", "15:05"),
    "intraday": ("08:55", "15:05"),
    "late_intraday": ("15:05", "15:20"),
    "close": ("15:15", "15:28"),
    "market_close": ("16:50", "18:00"),
}


def _within_window(phase: str, now_hhmm: str | None = None) -> bool:
    window = _PHASE_WINDOWS.get(phase)
    if window is None:
        return True
    now_hhmm = now_hhmm or __import__("datetime").datetime.now().strftime("%H:%M")
    slots = window if isinstance(window, list) else [window]
    return any(lo <= now_hhmm <= hi for lo, hi in slots)


def _guard_phase_window() -> None:
    if os.environ.get("MQK_FORCE") == "1":
        return
    if not _within_window(PHASE):
        window = _PHASE_WINDOWS.get(PHASE, ())
        slots = window if isinstance(window, list) else [window]
        window_str = " / ".join(f"{lo}~{hi}" for lo, hi in slots)
        logger.info(f"[시간창 가드] {PHASE}는 {window_str}에만 실행 — 현재 시각 스킵 (강제: MQK_FORCE=1)")
        sys.exit(0)


def _guard_trading_day() -> None:
    from codes.market_calendar import check_trading_day, read_cached_trading_day

    cached = read_cached_trading_day()
    if cached is None:
        logger.info("[휴장일 가드] 캐시 없음 — check_trading_day() 호출")
        cached = check_trading_day()

    if not cached:
        logger.info("[휴장일 가드] 오늘은 휴장일 — 작동 중단")
        sys.exit(0)


def _acquire_lock(path: Path = _LOCK_PATH):
    """5개 PM2 앱 간 mutable JSON state 동시 read-modify-write를 막기 위한
    exclusive non-blocking flock. 잠금 실패 시 None을 반환한다.

    반환된 파일 객체를 GC되지 않도록 호출부에서 보유해야 lock이 유지된다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def _make_orchestrator():
    from broker.kis_api import KISApi
    from orchestrator_v3 import MQKOrchestratorV3

    kis = KISApi()
    return MQKOrchestratorV3(kis_api=kis)


def run_premarket_early() -> None:
    _guard_trading_day()
    orch = _make_orchestrator()
    result = orch.run_premarket_early_v3()
    logger.info(f"[v3 PREMARKET_EARLY] {result.get('regime')} ({result.get('status')})")


def run_premarket() -> None:
    _guard_trading_day()
    orch = _make_orchestrator()
    result = orch.run_premarket_v3()
    logger.info(f"[v3 PREMARKET] {result['regime']} ({result['status']})")


def run_scan() -> None:
    _guard_trading_day()
    orch = _make_orchestrator()
    result = orch.run_scan_v3()
    logger.info(f"[v3 SCAN] watchlist={result.get('watchlist', [])}")


def run_intraday() -> None:
    _guard_trading_day()
    orch = _make_orchestrator()
    result = orch.run_intraday_v3()
    logger.info(f"[v3 INTRADAY] action={result.get('action')} reason={result.get('reason', '')[:80]}")


def run_late_intraday() -> None:
    _guard_trading_day()
    orch = _make_orchestrator()
    result = orch.run_late_intraday_v3()
    logger.info(f"[v3 LATE_INTRADAY] action={result.get('action')} reason={result.get('reason', '')[:60]}")


def run_close() -> None:
    _guard_trading_day()
    orch = _make_orchestrator()
    result = orch.run_close_v3()
    logger.info(f"[v3 CLOSE] sell_proposals={len(result.get('sell_proposals', []))}")


def run_market_close() -> None:
    _guard_trading_day()
    orch = _make_orchestrator()
    orch.run_market_close_v3()
    logger.info("[v3 MARKET_CLOSE] 분석 완료")


_RUNNERS = {
    "premarket_early": run_premarket_early,
    "premarket": run_premarket,
    "scan": run_scan,
    "intraday": run_intraday,
    "late_intraday": run_late_intraday,
    "close": run_close,
    "market_close": run_market_close,
}

if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        logger.info(f"[DRY RUN] MQK_PHASE={PHASE!r}")
        sys.exit(0)

    if PHASE not in _RUNNERS:
        logger.error(
            f"MQK_PHASE={PHASE!r} 미지원. "
            f"premarket | scan | intraday | close | market_close 중 하나를 설정하세요."
        )
        sys.exit(1)

    _guard_phase_window()

    _lock_file = _acquire_lock()
    if _lock_file is None:
        logger.warning("이전 인스턴스 실행 중 — 스킵")
        sys.exit(0)

    try:
        _RUNNERS[PHASE]()
    finally:
        fcntl.flock(_lock_file, fcntl.LOCK_UN)
        _lock_file.close()
