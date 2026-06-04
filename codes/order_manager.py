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
from dataclasses import dataclass, field, asdict
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
    approval_request_id: Optional[str] = None
    entry_date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))


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

    def __init__(
        self,
        kis_api=None,
        telegram=None,
        dry_run: bool = False,
        log_dir: Optional[Path] = None,
        journal=None,
    ):
        self._kis = kis_api         # KISApi 인스턴스
        self._telegram = telegram   # TelegramApproval 인스턴스
        self._dry_run = dry_run
        self._log_dir = log_dir or LOG_CONFIG.base_dir
        self._journal = journal

    def execute_buy(self, order: OrderRequest) -> ExecutionResult:
        """매수 주문 실행"""
        if RISK.require_telegram_approval and not order.approval_request_id:
            raise PermissionError("텔레그램 승인 ID 없이 매수 불가. require_telegram_approval=True")

        self._log_order(order)

        if self._dry_run:
            logger.warning(f"[DRY RUN] BUY {order.ticker} {order.quantity}주 @ {order.price:,.0f}원")
            execution = ExecutionResult(
                success=True,
                ticker=order.ticker,
                side="BUY",
                quantity=order.quantity,
                executed_price=order.price,
                order_no="DRY_RUN",
                timestamp=datetime.now().isoformat(),
            )
            if self._journal:
                self._journal.open_trade(
                    ticker=order.ticker,
                    name=order.name,
                    entry_date=order.entry_date,
                    entry_price=order.price,
                    quantity=order.quantity,
                    stop_loss_price=order.stop_loss_price,
                    entry_reason=order.reason,
                    confidence=order.confidence,
                    order_no=execution.order_no,
                )
            return execution

        if self._kis is None:
            raise RuntimeError("KIS API is required for live buy execution. Pass dry_run=True for order dry-run.")

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
        if self._journal and execution.success:
            self._journal.open_trade(
                ticker=order.ticker,
                name=order.name,
                entry_date=order.entry_date,
                entry_price=execution.executed_price,
                quantity=order.quantity,
                stop_loss_price=order.stop_loss_price,
                entry_reason=order.reason,
                confidence=order.confidence,
                order_no=execution.order_no,
            )
        return execution

    def execute_sell(self, order: OrderRequest) -> ExecutionResult:
        """매도 주문 실행"""
        self._log_order(order)

        if self._dry_run:
            logger.warning(f"[DRY RUN] SELL {order.ticker} {order.quantity}주 @ {order.price:,.0f}원")
            execution = ExecutionResult(
                success=True,
                ticker=order.ticker,
                side="SELL",
                quantity=order.quantity,
                executed_price=order.price,
                order_no="DRY_RUN",
                timestamp=datetime.now().isoformat(),
            )
            if self._journal:
                self._journal.close_trade(
                    ticker=order.ticker,
                    exit_date=order.entry_date,
                    exit_price=order.price,
                    exit_reason=order.reason,
                    quantity=order.quantity,
                )
            return execution

        if self._kis is None:
            raise RuntimeError("KIS API is required for live sell execution. Pass dry_run=True for order dry-run.")

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
        if self._journal and execution.success:
            self._journal.close_trade(
                ticker=order.ticker,
                exit_date=order.entry_date,
                exit_price=execution.executed_price,
                exit_reason=order.reason,
                quantity=order.quantity,
            )
        return execution

    def list_open_orders(self, side: str | None = None) -> list[dict]:
        """KIS 기준 미체결/정정취소 가능 주문 목록."""
        if self._dry_run or self._kis is None or not hasattr(self._kis, "get_open_orders"):
            return []
        return self._kis.get_open_orders(side=side)

    def cancel_order(
        self,
        order_no: str,
        quantity: int = 0,
        all_quantity: bool = True,
        org_no: str = "",
        price: float = 0,
        order_type: str = "00",
    ) -> ExecutionResult:
        """미체결 주문 취소. 체결된 포지션 청산과는 별개다."""
        if self._dry_run:
            return ExecutionResult(
                success=True,
                ticker="",
                side="CANCEL",
                quantity=quantity,
                executed_price=price,
                order_no=order_no,
                timestamp=datetime.now().isoformat(),
            )
        if self._kis is None or not hasattr(self._kis, "cancel_order"):
            raise RuntimeError("KIS API with cancel_order is required for order cancellation.")

        result = self._kis.cancel_order(
            order_no=order_no,
            quantity=quantity,
            org_no=org_no,
            price=price,
            all_quantity=all_quantity,
            order_type=order_type,
        )
        execution = ExecutionResult(
            success=result.success,
            ticker=result.ticker,
            side=result.side,
            quantity=result.quantity,
            executed_price=result.price,
            order_no=result.order_no,
            timestamp=result.timestamp,
            error_msg=result.error_msg,
        )
        self._log_execution(execution)
        return execution

    def cancel_stale_orders(self, side: str | None = None) -> list[ExecutionResult]:
        """현재 취소 가능한 미체결 주문을 전량 취소한다."""
        results: list[ExecutionResult] = []
        for order in self.list_open_orders(side=side):
            order_no = order.get("order_no", "")
            if not order_no:
                continue
            results.append(
                self.cancel_order(
                    order_no=order_no,
                    quantity=int(order.get("cancelable_quantity") or 0),
                    all_quantity=True,
                    org_no=order.get("org_no", ""),
                    price=float(order.get("price") or 0),
                    order_type=order.get("order_type") or "00",
                )
            )
        return results

    def _append_log(self, record) -> None:
        log_path = self._log_dir / datetime.now().strftime("%Y-%m-%d") / "orders.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    def _log_order(self, order: OrderRequest) -> None:
        self._append_log(order)

    def _log_execution(self, result: ExecutionResult) -> None:
        self._append_log(result)
