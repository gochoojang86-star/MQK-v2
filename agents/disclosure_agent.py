"""
Disclosure Agent - 공시 해석 Agent
LLM 사용. 공시의 주가 영향을 판단한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from config.settings import ModelTier
from llm.client import LLMClient


class DisclosureType(str, Enum):
    SUPPLY_CONTRACT = "SUPPLY_CONTRACT"     # 공급계약
    ORDER_RECEIVED = "ORDER_RECEIVED"       # 수주
    RIGHTS_ISSUE = "RIGHTS_ISSUE"           # 유상증자 (희석 위험)
    CONVERTIBLE_BOND = "CONVERTIBLE_BOND"   # CB (희석 위험)
    BOND_WITH_WARRANT = "BOND_WITH_WARRANT" # BW (희석 위험)
    EARNINGS_PREVIEW = "EARNINGS_PREVIEW"   # 실적 예상
    OTHER = "OTHER"


class DisclosureImpact(str, Enum):
    VERY_POSITIVE = "VERY_POSITIVE"
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"
    VERY_NEGATIVE = "VERY_NEGATIVE"


@dataclass
class DisclosureInterpretation:
    disclosure_type: DisclosureType
    impact: DisclosureImpact
    confidence: int
    dilution_risk: bool         # 지분 희석 위험
    trade_implication: str      # BUY_SIGNAL / HOLD / AVOID / SELL_SIGNAL
    reason: str


_SYSTEM_PROMPT = """당신은 한국 주식 공시를 해석하는 전문가입니다.
공시의 유형을 분류하고 주가에 미치는 영향을 판단하세요.

출력 형식 (JSON):
{
  "disclosure_type": "SUPPLY_CONTRACT|ORDER_RECEIVED|RIGHTS_ISSUE|CONVERTIBLE_BOND|BOND_WITH_WARRANT|EARNINGS_PREVIEW|OTHER",
  "impact": "VERY_POSITIVE|POSITIVE|NEUTRAL|NEGATIVE|VERY_NEGATIVE",
  "confidence": 0-100,
  "dilution_risk": true/false,
  "trade_implication": "BUY_SIGNAL|HOLD|AVOID|SELL_SIGNAL",
  "reason": "해석 근거 2문장"
}

주의사항:
- CB/BW/유상증자는 기본적으로 dilution_risk=true
- 수주/공급계약은 금액 대비 시가총액 비율이 중요
- 공급계약 금액이 시가총액의 10% 이상이면 VERY_POSITIVE 고려
"""


class DisclosureAgent:
    """공시 해석 Agent"""

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm or LLMClient()

    def interpret(self, ticker: str, disclosure: dict[str, Any]) -> DisclosureInterpretation:
        """
        공시를 해석한다.

        disclosure: {
            "title": str,
            "content": str,
            "date": str,
            "market_cap": float,  # 시가총액 (원)
        }
        """
        user_msg = f"""종목: {ticker}
시가총액: {disclosure.get('market_cap', 0) / 1e8:.0f}억원

공시 제목: {disclosure.get('title', '')}
공시 내용: {disclosure.get('content', '')[:1000]}
공시일: {disclosure.get('date', '')}

이 공시를 해석하고 JSON으로 출력하세요."""

        raw = self._llm.call(system=_SYSTEM_PROMPT, user=user_msg, tier=ModelTier.FAST)
        return DisclosureInterpretation(
            disclosure_type=DisclosureType(raw["disclosure_type"]),
            impact=DisclosureImpact(raw["impact"]),
            confidence=int(raw["confidence"]),
            dilution_risk=bool(raw["dilution_risk"]),
            trade_implication=raw["trade_implication"],
            reason=raw["reason"],
        )
