#!/usr/bin/env python3
"""조건검색 편입 감시 워처 (PM2: mqk-v3-psearch-watcher, 09:01 기동 → 15:06 종료).

90초 간격으로 psearch_result를 폴링(무료 REST)해 신규 편입을 감지하면:
  1. Telegram 알림 (전 종류)
  2. ep/base → watchlist 병합 (flock 보호)
  3. ep/base → intraday LLM 평가 즉시 트리거 (rate-limit 300s, flock 경합 시 스킵 —
     다음 10분 정규 틱이 병합된 watchlist를 자연히 평가)
reversal(MQK3)은 알림만 — 진입은 late_intraday 전용.

처리 실패(락 경합 등) 시 seen에 기록하지 않으므로 다음 폴에서 자동 재시도된다.
"""
from __future__ import annotations

import fcntl
import logging
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mqk_v3_psearch_watcher")

POLL_INTERVAL_SEC = 90
TRIGGER_COOLDOWN_SEC = 300
END_TIME = "15:06"
_LOCK_PATH = Path(__file__).parent / "data" / "mqk_v3.lock"


def _try_lock():
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    f = open(_LOCK_PATH, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        f.close()
        return None


def main() -> None:
    from codes.market_calendar import check_trading_day, read_cached_trading_day

    cached = read_cached_trading_day()
    if cached is None:
        cached = check_trading_day()
    if not cached:
        logger.info("[psearch_watcher] 휴장일 — 종료")
        return

    from broker.kis_api import KISApi
    from codes.psearch_watcher import (
        format_alert, fresh_cache, load_seen, partition_entries, poll_new_entries, save_seen,
    )
    from orchestrator_v3 import MQKOrchestratorV3, load_watchlist, save_watchlist

    orch = MQKOrchestratorV3(kis_api=KISApi())
    hts_id = os.environ.get("KIS_HTS_ID", "")
    last_trigger = 0.0

    logger.info(f"[psearch_watcher] 감시 시작 (간격 {POLL_INTERVAL_SEC}s, 종료 {END_TIME})")
    while datetime.now().strftime("%H:%M") < END_TIME:
        try:
            seen = load_seen()
            fresh_cache(orch._mil)
            events = poll_new_entries(orch._mil, hts_id, seen)

            if events:
                merge_tickers, want_trigger = partition_entries(events)
                lock = _try_lock()
                if lock is None:
                    # 정규 틱 실행 중 — seen 미저장으로 다음 폴에서 재시도
                    logger.info("[psearch_watcher] 락 경합 — 이번 폴 보류")
                else:
                    try:
                        if merge_tickers:
                            wl = load_watchlist()
                            merged = wl + [t for t in merge_tickers if t not in wl]
                            save_watchlist(merged)
                            logger.info(f"[psearch_watcher] watchlist 병합: +{merge_tickers} → {merged}")
                        save_seen(seen)
                        try:
                            orch._telegram.notify(format_alert(events))
                        except Exception as e:
                            logger.warning(f"[psearch_watcher] 알림 실패: {e}")

                        if want_trigger and time.time() - last_trigger >= TRIGGER_COOLDOWN_SEC:
                            logger.info("[psearch_watcher] 신규 편입 → intraday LLM 평가 트리거")
                            orch.run_intraday_v3()
                            last_trigger = time.time()
                    finally:
                        fcntl.flock(lock, fcntl.LOCK_UN)
                        lock.close()
            else:
                save_seen(seen)  # 첫 폴 시드 등 상태 변화 반영
        except Exception as e:
            logger.warning(f"[psearch_watcher] 폴링 오류 — 다음 주기에 재시도: {e}")

        time.sleep(POLL_INTERVAL_SEC)

    logger.info("[psearch_watcher] 장 종료 — 감시 종료")


if __name__ == "__main__":
    main()
