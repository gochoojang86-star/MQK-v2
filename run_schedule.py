#!/usr/bin/env python3
"""
MQK-v2 자동 운영 진입점
PM2 cron_restart로 각 단계별 실행.

MQK_PHASE 환경변수로 실행 단계 구분:
  premarket  - 08:00 장전 분석
  scan       - 08:30 후보 스캔
  intraday   - 09:00~15:20 장중 루프 (5분 간격)
               보유 포지션 손절/익절 점검 + 상위 후보 진입 평가
  close      - 15:30 장마감 복기
"""
from __future__ import annotations

import json
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
logger = logging.getLogger("mqk_schedule")

PHASE = os.environ.get("MQK_PHASE", "")


def run_premarket() -> None:
    """장전 분석: 시장 상황 및 레짐 판단"""
    from broker.kis_api import KISApi
    from orchestrator import MQKOrchestrator

    kis = KISApi()
    orch = MQKOrchestrator(kis_api=kis)
    result = orch.run_premarket()
    logger.info(f"장전 완료: {result['regime']} ({result['status']})")


def run_scan() -> None:
    """후보 스캔: 기술적 스캔 및 테마 분석"""
    from config.settings import LOG_CONFIG
    from broker.kis_api import KISApi
    from orchestrator import MQKOrchestrator
    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    market_status_path = LOG_CONFIG.base_dir / today / "market_status.json"
    if not market_status_path.exists():
        logger.error("market_status.json 없음. premarket을 먼저 실행하세요.")
        sys.exit(1)

    market_status = json.loads(market_status_path.read_text(encoding="utf-8"))
    kis = KISApi()
    orch = MQKOrchestrator(kis_api=kis)
    candidates = orch.run_scan(market_status)
    logger.info(f"스캔 완료: {len(candidates)}개 후보")


def run_intraday() -> None:
    """장중 루프: 포지션 STP 점검 + 상위 후보 진입 평가"""
    from config.settings import LOG_CONFIG
    from broker.kis_api import KISApi
    from orchestrator import MQKOrchestrator
    from codes.risk_officer import PortfolioState
    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    candidates_path = LOG_CONFIG.base_dir / today / "candidate_scores.jsonl"

    kis = KISApi()
    orch = MQKOrchestrator(kis_api=kis)

    # ── 1. 보유 포지션 손절/익절 점검 ──────────────────────────────────────
    exit_results = orch.run_position_exit_check()
    for r in exit_results:
        if r.get("action") != "HOLD":
            logger.info(f"[STP] {r.get('ticker')} → {r.get('action')} ({r.get('signal', '')})")

    # ── 2. 후보 종목 진입 평가 ──────────────────────────────────────────────
    if not candidates_path.exists():
        logger.warning("candidate_scores.jsonl 없음 — scan 단계를 먼저 실행하세요.")
        return

    candidates = [
        json.loads(line)
        for line in candidates_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    open_pos = orch._journal.get_open_positions()
    portfolio_state = PortfolioState(
        total_capital=float(os.environ.get("MQK_CAPITAL", "50000000")),
        daily_pnl=sum(float(p.get("pnl") or 0) for p in open_pos),
        open_positions=open_pos,
        theme_exposure={},
    )

    for cand in candidates[:5]:
        try:
            result = orch.evaluate_candidate(
                ticker=cand["ticker"],
                name=cand["name"],
                current_price=orch._market_data.get_snapshot(cand["ticker"]).current_price,
                portfolio_state=portfolio_state,
            )
            logger.info(f"[장중] {cand['name']}({cand['ticker']}) → {result.get('action')}")
        except Exception as e:
            logger.warning(f"[장중] {cand['ticker']} 평가 실패: {e}")

    logger.info("장중 루프 완료")


def run_close() -> None:
    """장마감 복기: 거래 복기 및 자기개선"""
    from broker.kis_api import KISApi
    from orchestrator import MQKOrchestrator

    kis = KISApi()
    orch = MQKOrchestrator(kis_api=kis)
    orch.run_close_review()
    logger.info("장마감 복기 완료")


_RUNNERS = {
    "premarket": run_premarket,
    "scan": run_scan,
    "intraday": run_intraday,
    "close": run_close,
}

if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        logger.info(f"[DRY RUN] PHASE={PHASE!r}")
        sys.exit(0)

    if PHASE not in _RUNNERS:
        logger.error(
            f"MQK_PHASE={PHASE!r} 미지원. "
            f"premarket | scan | close 중 하나를 설정하세요."
        )
        sys.exit(1)

    _RUNNERS[PHASE]()
