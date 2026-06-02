"""
Disclosure Agent - 공시 해석 Agent
LLM 사용.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from config.settings import ModelTier
from llm.client import LLMClient
from llm.soul import inject_agent

_SYSTEM_PROMPT = inject_agent("disclosure_agent")


class DisclosureImpact(str, Enum):
    POSITIVE = "POSITIVE"
    NEUTRAL  = "NEUTRAL"
    NEGATIVE = "NEGATIVE"
    RISK     = "RISK"


@dataclass
class DisclosureResult:
    disclosure_score: int
    impact: DisclosureImpact
    summary: str
    risk_flags: list[str]
    reason: str


class DisclosureAgent:
    """공시 해석 Agent"""

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm or LLMClient()

    def interpret(self, ticker: str, disclosure: dict[str, Any]) -> DisclosureResult:
        user_msg = f"""종목: {ticker}
시가총액: {disclosure.get('market_cap', 0) / 1e8:.0f}억원

공시 제목: {disclosure.get('title', '')}
공시 내용: {disclosure.get('content', '')[:1000]}
공시일: {disclosure.get('date', '')}

이 공시를 해석하고 JSON으로 출력하세요."""

        raw = self._llm.call(system=_SYSTEM_PROMPT, user=user_msg, tier=ModelTier.FAST)
        return DisclosureResult(
            disclosure_score=int(raw.get("disclosure_score", 0)),
            impact=DisclosureImpact(raw["impact"]),
            summary=raw.get("summary", ""),
            risk_flags=raw.get("risk_flags", []),
            reason=raw.get("reason", ""),
        )
