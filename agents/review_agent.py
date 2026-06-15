"""
Review Agent - 장마감 거래 복기 Agent
LLM 사용. 매일 장마감 후 실행.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from config.settings import ModelTier, LOG_CONFIG
from llm.client import LLMClient
from llm.soul import inject_agent

_SYSTEM_PROMPT = inject_agent("review_agent")


@dataclass
class TradeReview:
    ticker: str
    trade_date: str
    result: str         # WIN / LOSS / BREAKEVEN
    pnl: float
    pnl_pct: float
    markdown: str       # LLM이 생성한 복기 마크다운


class ReviewAgent:
    """거래 복기 Agent"""

    def __init__(self, llm: LLMClient | None = None, log_dir: Path | None = None):
        self._llm = llm or LLMClient()
        self._log_dir = log_dir or LOG_CONFIG.base_dir

    def analyze(self, trade_record: dict[str, Any]) -> TradeReview:
        entry_price = trade_record.get("entry_price", 0)
        exit_price  = trade_record.get("exit_price", 0)
        quantity    = trade_record.get("quantity", 0)
        pnl         = (exit_price - entry_price) * quantity
        pnl_pct     = (exit_price - entry_price) / entry_price * 100 if entry_price else 0
        result      = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")

        user_msg = f"""거래 복기 요청:

종목: {trade_record.get('name', '')} ({trade_record.get('ticker', '')})
진입: {trade_record.get('entry_date', '')} @ {entry_price:,.0f}원
청산: {trade_record.get('exit_date', '')} @ {exit_price:,.0f}원
수량: {quantity}주
손익: {pnl:+,.0f}원 ({pnl_pct:+.2f}%)
결과: {result}

진입 근거: {trade_record.get('entry_reason', '')}
청산 근거: {trade_record.get('exit_reason', '')}
설정 손절가: {trade_record.get('stop_loss_price', 0):,.0f}원
목표가: {trade_record.get('planned_target', 0):,.0f}원

이 거래를 복기하고 마크다운 형식으로 출력하세요."""

        markdown = self._llm.call(
            system=_SYSTEM_PROMPT, user=user_msg,
            tier=ModelTier.FAST, expect_json=False
        )

        review = TradeReview(
            ticker=trade_record.get("ticker", ""),
            trade_date=trade_record.get("exit_date", datetime.now().strftime("%Y-%m-%d")),
            result=result,
            pnl=pnl,
            pnl_pct=round(pnl_pct, 2),
            markdown=markdown,
        )
        self._write_journal(review)
        return review

    def _write_journal(self, review: TradeReview) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        log_dir = self._log_dir / today
        log_dir.mkdir(parents=True, exist_ok=True)
        journal_path = log_dir / LOG_CONFIG.journal_filename
        with journal_path.open("a", encoding="utf-8") as f:
            f.write(f"\n## {review.trade_date} | {review.ticker} | {review.result} ({review.pnl_pct:+.2f}%)\n\n")
            f.write(review.markdown)
            f.write("\n\n---\n")
