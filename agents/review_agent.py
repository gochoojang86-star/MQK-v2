"""
Review Agent - 거래 복기 Agent
LLM 사용. 실패/성공 원인 분석 후 journal.md 기록.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from config.settings import ModelTier
from llm.client import LLMClient
from config.settings import LOG_CONFIG


@dataclass
class TradeReview:
    ticker: str
    trade_date: str
    result: str         # WIN / LOSS / BREAKEVEN
    pnl: float
    pnl_pct: float
    failure_reasons: list[str]
    success_reasons: list[str]
    lessons: list[str]
    improvement_suggestions: list[str]


_SYSTEM_PROMPT = """당신은 한국 주식 스윙 트레이더의 거래 복기 전문가입니다.
거래 결과를 분석하고 실패/성공 원인을 파악하여 다음 거래에 교훈을 남기세요.

출력 형식 (JSON):
{
  "failure_reasons": ["실패 원인 리스트"],
  "success_reasons": ["성공 원인 리스트"],
  "lessons": ["교훈 리스트"],
  "improvement_suggestions": ["개선 제안 리스트"]
}

분석 관점:
- 진입 타이밍 (너무 이름/늦음/적절)
- 손절 실행 여부 (계획대로 실행했는가)
- 시장/테마/수급 판단의 정확성
- 뉴스/공시 해석의 정확성
- 포지션 사이즈의 적절성
"""


class ReviewAgent:
    """거래 복기 Agent - 매일 장마감 후 실행"""

    def __init__(self, llm: LLMClient | None = None, log_dir: Path | None = None):
        self._llm = llm or LLMClient()
        self._log_dir = log_dir or LOG_CONFIG.base_dir

    def analyze(self, trade_record: dict[str, Any]) -> TradeReview:
        """
        거래 기록을 분석한다.

        trade_record: {
            "ticker": str,
            "name": str,
            "entry_date": str,
            "exit_date": str,
            "entry_price": float,
            "exit_price": float,
            "quantity": int,
            "entry_reason": str,
            "exit_reason": str,
            "stop_loss_price": float,
            "planned_target": float,
        }
        """
        entry_price = trade_record.get("entry_price", 0)
        exit_price = trade_record.get("exit_price", 0)
        quantity = trade_record.get("quantity", 0)
        pnl = (exit_price - entry_price) * quantity
        pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price else 0
        result = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")

        user_msg = f"""거래 복기 요청:

종목: {trade_record.get('name', '')} ({trade_record.get('ticker', '')})
진입일: {trade_record.get('entry_date', '')} @ {entry_price:,.0f}원
청산일: {trade_record.get('exit_date', '')} @ {exit_price:,.0f}원
수량: {quantity}주
손익: {pnl:+,.0f}원 ({pnl_pct:+.2f}%)
결과: {result}

진입 근거: {trade_record.get('entry_reason', '')}
청산 근거: {trade_record.get('exit_reason', '')}
설정 손절가: {trade_record.get('stop_loss_price', 0):,.0f}원
목표가: {trade_record.get('planned_target', 0):,.0f}원

이 거래를 복기하고 JSON으로 출력하세요."""

        raw = self._llm.call(system=_SYSTEM_PROMPT, user=user_msg, tier=ModelTier.STANDARD)
        review = TradeReview(
            ticker=trade_record.get("ticker", ""),
            trade_date=trade_record.get("exit_date", datetime.now().strftime("%Y-%m-%d")),
            result=result,
            pnl=pnl,
            pnl_pct=round(pnl_pct, 2),
            failure_reasons=raw.get("failure_reasons", []),
            success_reasons=raw.get("success_reasons", []),
            lessons=raw.get("lessons", []),
            improvement_suggestions=raw.get("improvement_suggestions", []),
        )
        self._write_journal(review)
        return review

    def _write_journal(self, review: TradeReview) -> None:
        """장마감 후 journal.md에 복기 기록"""
        today = datetime.now().strftime("%Y-%m-%d")
        log_dir = self._log_dir / today
        log_dir.mkdir(parents=True, exist_ok=True)
        journal_path = log_dir / LOG_CONFIG.journal_filename

        entry = f"""
## {review.trade_date} | {review.ticker} | {review.result} ({review.pnl_pct:+.2f}%)

**손익:** {review.pnl:+,.0f}원

### 실패 원인
{chr(10).join(f'- {r}' for r in review.failure_reasons) or '- N/A'}

### 성공 원인
{chr(10).join(f'- {r}' for r in review.success_reasons) or '- N/A'}

### 교훈
{chr(10).join(f'- {l}' for l in review.lessons) or '- N/A'}

### 개선 제안
{chr(10).join(f'- {s}' for s in review.improvement_suggestions) or '- N/A'}

---
"""
        with journal_path.open("a", encoding="utf-8") as f:
            f.write(entry)
