"""
News Agent - 뉴스 질 평가 Agent
LLM 사용. 뉴스의 실질적 가치를 판단한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from config.settings import ModelTier
from llm.client import LLMClient
from llm.soul import inject


class NewsQuality(str, Enum):
    NEW_CATALYST = "NEW_CATALYST"       # 신규재료 (강력)
    POLICY_BENEFIT = "POLICY_BENEFIT"   # 정책수혜 (강력)
    RECYCLED = "RECYCLED"               # 재탕 (약함)
    FADED = "FADED"                     # 소멸 (주의)
    RUMOR = "RUMOR"                     # 루머 (위험)


@dataclass
class NewsEvaluation:
    quality: NewsQuality
    confidence: int         # 0-100
    trade_implication: str  # BUY_SIGNAL / HOLD / AVOID
    reason: str


_SYSTEM_PROMPT = """당신은 한국 주식 시장 뉴스의 질을 평가하는 전문가입니다.
주어진 뉴스가 실제 주가에 미치는 영향을 판단하세요.

출력 형식 (JSON):
{
  "quality": "NEW_CATALYST|POLICY_BENEFIT|RECYCLED|FADED|RUMOR",
  "confidence": 0-100,
  "trade_implication": "BUY_SIGNAL|HOLD|AVOID",
  "reason": "판단 근거 2문장"
}

판단 기준:
- NEW_CATALYST: 처음 나온 구체적 실적/계약/수주 뉴스
- POLICY_BENEFIT: 정부 정책으로 직접 수혜가 명확한 뉴스
- RECYCLED: 이전에 나온 내용의 반복, 이미 주가에 반영됨
- FADED: 한때 재료였으나 현재는 소멸된 이슈
- RUMOR: 출처 불명확, 루머성, 확인 불가
"""


class NewsAgent:
    """뉴스 질 평가 Agent"""

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm or LLMClient()

    def evaluate(self, ticker: str, news_items: list[dict[str, Any]]) -> list[NewsEvaluation]:
        """
        종목 관련 뉴스 목록을 평가한다.

        news_items: [{"title": str, "content": str, "date": str, "source": str}]
        """
        if not news_items:
            return []

        results = []
        for news in news_items[:5]:  # 비용 제어: 최대 5개
            evaluation = self._evaluate_single(ticker, news)
            results.append(evaluation)
        return results

    def _evaluate_single(self, ticker: str, news: dict) -> NewsEvaluation:
        user_msg = f"""종목: {ticker}

뉴스 제목: {news.get('title', '')}
뉴스 내용: {news.get('content', '')[:500]}
날짜: {news.get('date', '')}
출처: {news.get('source', '')}

이 뉴스의 질을 평가하고 JSON으로 출력하세요."""

        raw = self._llm.call(system=inject(_SYSTEM_PROMPT), user=user_msg, tier=ModelTier.FAST)
        return NewsEvaluation(
            quality=NewsQuality(raw["quality"]),
            confidence=int(raw["confidence"]),
            trade_implication=raw["trade_implication"],
            reason=raw["reason"],
        )
