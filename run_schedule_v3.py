#!/usr/bin/env python3
"""
MQK v3 자동 운영 진입점 (PM2 cron_restart로 각 단계별 실행)

MQK_PHASE 환경변수:
  premarket    - 08:45 레짐 판단 + risk_guidance/drift_triggers 생성 + 보유종목 점검
  scan         - 09:10 / 11:00 / 14:00 watchlist 생성/갱신
  intraday     - 09:20~15:00, */5 드리프트 체크 + 매수/청산 proposal
  close        - 15:30 청산 판단 + 거래 복기
  market_close - 17:00 장마감 분석 + 다음날 prior 생성

휴장일 가드는 v2와 동일하게 codes/market_calendar의 캐시를 사용한다.
"""
from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("mqk_v3_schedule")

PHASE = os.environ.get("MQK_PHASE", "")


def _guard_trading_day() -> None:
    from codes.market_calendar import check_trading_day, read_cached_trading_day

    cached = read_cached_trading_day()
    if cached is None:
        logger.info("[휴장일 가드] 캐시 없음 — check_trading_day() 호출")
        cached = check_trading_day()

    if not cached:
        logger.info("[휴장일 가드] 오늘은 휴장일 — 작동 중단")
        sys.exit(0)


def _make_orchestrator():
    from broker.kis_api import KISApi
    from orchestrator_v3 import MQKOrchestratorV3

    kis = KISApi()
    return MQKOrchestratorV3(kis_api=kis)


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
    logger.info(f"[v3 INTRADAY] action={result.get('action')}")


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
    "premarket": run_premarket,
    "scan": run_scan,
    "intraday": run_intraday,
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

    _RUNNERS[PHASE]()
