"""
End-to-end test: Telegram 승인 → 매수 → close → market_close 전체 흐름 검증.

장 마감 후 실행 시 dry_run_orders=True (주문 API 미호출, 나머지 코드 경로는 실제).
장중 실행 시 --live 플래그로 실제 paper 주문 진행.

실행 방법:
    cd /mnt/c/Users/gocho/MQK-v2
    MQK_FORCE=1 .venv/bin/python tools/test_e2e_approval_flow.py
    MQK_FORCE=1 .venv/bin/python tools/test_e2e_approval_flow.py --live
"""
from __future__ import annotations

import json
import os
import sys
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

os.environ["MQK_FORCE"] = "1"   # 시간창 가드 우회

# 프로젝트 의존성 (dotenv 로드 후)
from broker.telegram import ApprovalRequest  # noqa: E402
from codes.order_manager import OrderRequest  # noqa: E402
from codes.risk_officer import PortfolioState  # noqa: E402


def _safe_build_portfolio_state(orch):
    """잔고 API 실패 시 기본 포트폴리오 상태로 폴백."""
    try:
        return orch.build_portfolio_state()
    except Exception as e:
        logger.warning(f"[포트폴리오 조회 실패] {e} — 기본값 사용")
        from codes.risk_officer import PortfolioState
        return PortfolioState(
            total_capital=50_000_000,
            daily_pnl=0.0,
            open_positions=[],
            theme_exposure={},
        )


def test_approval_and_buy(orch, ticker: str, dry_run: bool) -> dict:
    """Telegram 승인 요청 → 매수까지 직접 실행."""
    logger.info(f"[STEP 1] {ticker} 스냅샷 조회...")
    snapshot = orch._market_data.get_snapshot(ticker)
    entry_price = snapshot.current_price
    stock_name = getattr(snapshot, "name", "") or ticker
    logger.info(f"[STEP 1] {stock_name}({ticker}) 현재가: {entry_price:,.0f}원")

    if entry_price <= 0:
        logger.error(f"[STEP 1] 현재가 조회 실패 (0원) — 테스트 중단")
        return {"action": "ERROR", "reason": "현재가 0"}

    # 스탑로스: 현재가 -5% (테스트용)
    stop_loss_price = entry_price * 0.95

    logger.info("[STEP 2] 포지션 사이징...")
    atr = orch._estimate_atr(ticker)
    portfolio_state = _safe_build_portfolio_state(orch)
    sizing = orch._position_sizer.calculate_flexible_stop(
        ticker=ticker,
        entry_price=entry_price,
        atr=atr,
        total_capital=getattr(portfolio_state, "total_capital", 50_000_000),
        support_stop_price=stop_loss_price,
    )
    logger.info(f"[STEP 2] 수량={sizing.quantity}주, 스탑={sizing.stop_loss_price:,.0f}원")

    if sizing.quantity <= 0:
        logger.warning("[STEP 2] 수량 0 — 최소 1주로 강제 설정")
        from codes.position_sizer import SizingResult
        sizing = SizingResult(
            quantity=1,
            stop_loss_price=stop_loss_price,
            risk_pct=0.5,
        )

    # Telegram 승인 요청
    logger.info("[STEP 3] 텔레그램 승인 요청 발송...")
    approval_req = ApprovalRequest(
        ticker=ticker, name=stock_name, decision="BUY",
        entry_price=entry_price,
        stop_loss_price=sizing.stop_loss_price,
        quantity=sizing.quantity,
        risk_pct=sizing.risk_pct,
        confidence=55,
        reason="[E2E 테스트] KIS_USE_MCP=false 수정 후 텔레그램 승인 → 매수 → 장마감 전체 흐름 검증",
        counter_argument="",
    )
    approval = orch._telegram.request_approval(approval_req)
    logger.info(f"[STEP 3] 승인 결과: approved={approval.approved} (id={approval.request_id[:8]})")

    if not approval.approved:
        logger.warning("[STEP 3] 거부됨 — 테스트 중단")
        return {"action": "REJECTED"}

    # 주문 실행
    logger.info(f"[STEP 4] 매수 주문 실행 ({'DRY RUN' if dry_run else 'LIVE PAPER'})...")
    order = OrderRequest(
        ticker=ticker, name=stock_name, side="BUY",
        quantity=sizing.quantity,
        price=entry_price,
        stop_loss_price=sizing.stop_loss_price,
        reason="[E2E 테스트] 승인 후 매수",
        confidence=55,
        approval_request_id=approval.request_id,
        strategy_type="TREND",
    )
    result = orch._order_manager.execute_buy(order)
    logger.info(f"[STEP 4] 매수 결과: success={result.success}, "
                f"order_no={result.order_no}, err={result.error_msg or '없음'}")

    if result.success:
        try:
            label = "DRY" if dry_run else "PAPER"
            order_no_display = (result.order_no or "DRY-RUN").replace("_", "-")
            orch._telegram.notify(
                f"E2E 테스트 매수 성공 - {label}\n"
                f"종목: {stock_name} {ticker}\n"
                f"수량: {sizing.quantity}주 @ {int(entry_price):,}원\n"
                f"주문번호: {order_no_display}"
            )
        except Exception as e:
            logger.warning(f"[결과 알림] 텔레그램 발송 실패: {e}")

    return {"action": "BUY_EXECUTED", "success": result.success, "order_no": result.order_no}


def main(dry_run: bool = True) -> None:
    from broker.kis_api import KISApi
    from orchestrator_v3 import MQKOrchestratorV3

    mode_label = "DRY-RUN" if dry_run else "LIVE PAPER ORDER"
    logger.info(f"=== E2E 테스트 시작 ({mode_label}) ===")
    logger.info(f"KIS_USE_MCP={os.environ.get('KIS_USE_MCP','미설정')} (false=KISApi 직접 사용)")

    kis = KISApi()
    orch = MQKOrchestratorV3(kis_api=kis, dry_run_orders=dry_run)

    # ── Step 1~4: 승인 → 매수 ────────────────────────────────────────────────
    buy_result = test_approval_and_buy(orch, "005930", dry_run)
    logger.info(f"매수 플로우 결과: {buy_result}")

    # ── Step 5: CLOSE 단계 ────────────────────────────────────────────────────
    logger.info("\n[STEP 5] CLOSE 단계 실행 (장 마감 청산 판단)...")
    try:
        close_result = orch.run_close_v3()
        proposals = close_result.get("sell_proposals", [])
        logger.info(f"[STEP 5] close action={close_result.get('action')}, "
                    f"sell_proposals={len(proposals)}")
    except Exception as e:
        logger.warning(f"[STEP 5] close 실패: {e}")

    # ── Step 6: MARKET_CLOSE 단계 (장마감 메세지) ─────────────────────────────
    logger.info("\n[STEP 6] MARKET_CLOSE 단계 실행 (장마감 분석 + 텔레그램 메세지)...")
    try:
        mc_result = orch.run_market_close_v3()
        read = mc_result.get("close_market_read", {})
        logger.info(f"[STEP 6] market_quality={read.get('market_quality')}, "
                    f"regime_prior={read.get('regime_prior_for_tomorrow')}")
        tomorrow = mc_result.get("next_day_premarket_context", {})
        if tomorrow:
            logger.info(f"[STEP 6] 내일 편향: {tomorrow.get('tomorrow_bias', {})}")
    except Exception as e:
        logger.warning(f"[STEP 6] market_close 실패: {e}")

    logger.info("\n=== E2E 테스트 완료 ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="실제 paper 주문 (장중에만)")
    args = parser.parse_args()
    main(dry_run=not args.live)
