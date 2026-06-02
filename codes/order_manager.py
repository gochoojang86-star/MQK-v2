"""
Order Manager Code - 주문 실행 최종 관문
LLM 미사용. KIS API 연동.

실행 순서:
Portfolio Manager Agent → Risk Officer Code → Position Sizer Code
→ Telegram Approval → Order Manager Code → KIS API
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import RISK, LOG_CONFIG

logger = logging.getLogger(__name__)


@dataclass
class OrderRequest:
    ticker: str
    name: str
    side: str           # BUY / SELL
    quantity: int
    price: float        # 0 = 시장가
    stop_loss_price: float
    reason: str
    confidence: int
    approved_by: str = "telegram"   # telegram / manual


@dataclass
class ExecutionResult:
    success: bool
    ticker: str
    side: str
    quantity: int
    executed_price: float
    order_no: str
    timestamp: str
    error_msg: str = ""


class OrderManager:
    """
    주문 실행 최종 관문.
    require_telegram_approval=True면 TelegramApproval 통과 없이 실행 불가.
    """

    def __init__(self, kis_api=None, telegram=None, log_dir: Optional[Path] = None):
        self._kis = kis_api         # KISApi 인스턴스
        self._telegram = telegram   # TelegramApproval 인스턴스
        self._log_dir = log_dir or LOG_CONFIG.base_dir

    def execute_buy(self, order: OrderRequest) -> ExecutionResult:
        """매수 주문 실행"""
        if RISK.require_telegram_approval and not order.approved_by == "telegram":
            raise PermissionError("텔레그램 승인 없이 매수 불가. require_telegram_approval=True")

        self._log_order(order)

        if self._kis is None:
            logger.warning(f"[DRY RUN] BUY {order.ticker} {order.quantity}주 @ {order.price:,.0f}원")
            return ExecutionResult(
                success=True,
                ticker=order.ticker,
                side="BUY",
                quantity=order.quantity,
                executed_price=order.price,
                order_no="DRY_RUN",
                timestamp=datetime.now().isoformat(),
            )

        if order.price == 0:
            result = self._kis.buy_market(order.ticker, order.quantity)
        else:
            result = self._kis.buy_limit(order.ticker, order.quantity, order.price)

        execution = ExecutionResult(
            success=result.success,
            ticker=result.ticker,
            side="BUY",
            quantity=result.quantity,
            executed_price=result.price,
            order_no=result.order_no,
            timestamp=result.timestamp,
            error_msg=result.error_msg,
        )
        self._log_execution(execution)
        return execution

    def execute_sell(self, order: OrderRequest) -> ExecutionResult:
        """매도 주문 실행"""
        self._log_order(order)

        if self._kis is None:
            logger.warning(f"[DRY RUN] SELL {order.ticker} {order.quantity}주 @ {order.price:,.0f}원")
            return ExecutionResult(
                success=True,
                ticker=order.ticker,
                side="SELL",
                quantity=order.quantity,
                executed_price=order.price,
                order_no="DRY_RUN",
                timestamp=datetime.now().isoformat(),
            )

        if order.price == 0:
            result = self._kis.sell_market(order.ticker, order.quantity)
        else:
            result = self._kis.sell_limit(order.ticker, order.quantity, order.price)

        execution = ExecutionResult(
            success=result.success,
            ticker=result.ticker,
            side="SELL",
            quantity=result.quantity,
            executed_price=result.price,
            order_no=result.order_no,
            timestamp=result.timestamp,
            error_msg=result.error_msg,
        )
        self._log_execution(execution)
        return execution

    def _log_order(self, order: OrderRequest) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        log_path = self._log_dir / today / "orders.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(order), ensure_ascii=False) + "\n")

    def _log_execution(self, result: ExecutionResult) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        log_path = self._log_dir / today / "orders.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
