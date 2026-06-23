#!/usr/bin/env python3
"""MQK v4 자동 운영 진입점.

MQK_PHASE 환경변수 (KST):
  premarket_sejuk - 08:45 장전 상한가 + 장전거래 복합 분석
  premarket       - 09:03 레짐 판단
  scan            - 09:17/11:17/13:17/15:00 종목 스캔
  intraday        - 09:20~14:50 */10 진입 + 세력 이탈 감시
  close           - 15:18 마감 청산
  market_close    - 17:00 복기 + 다음날 prior
"""
from __future__ import annotations
import logging
import os
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mqk_v4")

PHASE = os.environ.get("MQK_PHASE", "")

_PHASE_WINDOWS: dict[str, tuple[str, str] | list[tuple[str, str]]] = {
    "premarket_sejuk": ("08:40", "09:00"),
    "premarket":       [("09:00", "09:10"), ("10:55", "11:10"), ("12:55", "13:10")],
    "scan":            ("09:10", "15:05"),
    "intraday":        ("09:15", "15:05"),
    "close":           ("15:15", "15:28"),
    "market_close":    ("16:55", "17:30"),
}


def _guard_time_window() -> bool:
    if os.environ.get("MQK_FORCE") == "1":
        return True
    now = datetime.now().strftime("%H:%M")
    window = _PHASE_WINDOWS.get(PHASE)
    if window is None:
        return True
    if isinstance(window, list):
        for start, end in window:
            if start <= now <= end:
                return True
        logger.info(f"[시간창 가드] {PHASE} — 현재 시각 스킵")
        return False
    start, end = window
    if start <= now <= end:
        return True
    logger.info(f"[시간창 가드] {PHASE}는 {start}~{end}에만 실행 — 현재 시각 스킵")
    return False


def _guard_trading_day() -> bool:
    # v3와 동일한 휴장일 체크 재사용
    try:
        from codes.market_data import MarketData
        md = MarketData()
        if not md.is_trading_day():
            logger.info("[v4] 휴장일 — 스킵")
            return False
    except Exception:
        pass
    return True


def _make_orchestrator():
    from broker.kis_api import KISApi
    from orchestrator_v4 import MQKOrchestratorV4
    return MQKOrchestratorV4(kis_api=KISApi())


def run_premarket_sejuk():
    if not _guard_time_window() or not _guard_trading_day():
        return
    orch = _make_orchestrator()
    result = orch.run_premarket_sejuk_v4()
    logger.info(f"[v4 PREMARKET_SEJUK] 후보={len(result.get('candidates', []))}개")


def run_premarket():
    if not _guard_time_window() or not _guard_trading_day():
        return
    orch = _make_orchestrator()
    result = orch.run_premarket_v4()
    logger.info(f"[v4 PREMARKET] {result.get('regime')} ({result.get('status')})")


def run_scan():
    if not _guard_time_window() or not _guard_trading_day():
        return
    orch = _make_orchestrator()
    result = orch.run_scan_v4()
    logger.info(f"[v4 SCAN] watchlist 업데이트")


def run_intraday():
    if not _guard_time_window() or not _guard_trading_day():
        return
    orch = _make_orchestrator()
    result = orch.run_intraday_v4()
    logger.info(f"[v4 INTRADAY] action={result.get('action')}")


def run_close():
    if not _guard_time_window() or not _guard_trading_day():
        return
    orch = _make_orchestrator()
    result = orch.run_close_v4()
    logger.info(f"[v4 CLOSE] sell={len(result.get('sell_proposals', []))}")


def run_market_close():
    if not _guard_time_window() or not _guard_trading_day():
        return
    orch = _make_orchestrator()
    orch.run_market_close_v4()
    logger.info("[v4 MARKET_CLOSE] 완료")


_RUNNERS = {
    "premarket_sejuk": run_premarket_sejuk,
    "premarket":       run_premarket,
    "scan":            run_scan,
    "intraday":        run_intraday,
    "close":           run_close,
    "market_close":    run_market_close,
}

if __name__ == "__main__":
    runner = _RUNNERS.get(PHASE)
    if runner is None:
        logger.error(f"MQK_PHASE='{PHASE}' 미지원. {list(_RUNNERS)} 중 하나를 설정하세요.")
        raise SystemExit(1)
    runner()
