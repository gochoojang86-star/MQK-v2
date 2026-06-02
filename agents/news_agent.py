"""
News Agent - 뉴스 품질 평가 Agent
LLM 사용.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from config.settings import ModelTier
from llm.client import LLMClient
from llm.soul import inject_agent

_SYSTEM_PROMPT = inject_agent("news_agent")


class NewsQuality(str, Enum):
    HIGH   = "HIGH"
    MEDIUM = "MEDIUM"
    LOW    = "LOW"


class NewsCategory(str, Enum):
    NEW_CATALYST   = "NEW_CATALYST"
    POLICY_BENEFIT = "POLICY_BENEFIT"
    RECYCLED       = "RECYCLED"
    FADED          = "FADED"
    RUMOR          = "RUMOR"


@dataclass
class NewsEvaluation:
    news_score: int
    quality: NewsQuality
    category: NewsCategory
    is_recycled: bool
    is_material: bool
    reason: str
    risk: str


class NewsAgent:
    """뉴스 품질 평가 Agent"""

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm or LLMClient()

    def evaluate(self, ticker: str, news_items: list[dict[str, Any]]) -> list[NewsEvaluation]:
        if not news_items:
            return []
        return [self._evaluate_single(ticker, n) for n in news_items[:5]]

    def _evaluate_single(self, ticker: str, news: dict) -> NewsEvaluation:
        user_msg = f"""종목: {ticker}

뉴스 제목: {news.get('title', '')}
뉴스 내용: {news.get('content', '')[:500]}
날짜: {news.get('date', '')}
출처: {news.get('source', '')}

이 뉴스의 품질을 평가하고 JSON으로 출력하세요."""

        raw = self._llm.call(system=_SYSTEM_PROMPT, user=user_msg, tier=ModelTier.FAST)
        return NewsEvaluation(
            news_score=int(raw.get("news_score", 0)),
            quality=NewsQuality(raw["quality"]),
            category=NewsCategory(raw.get("category", "RECYCLED")),
            is_recycled=bool(raw.get("is_recycled", False)),
            is_material=bool(raw.get("is_material", True)),
            reason=raw.get("reason", ""),
            risk=raw.get("risk", ""),
        )
