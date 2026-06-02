"""
Regime Agent - 시장 체제 판단 Agent
LLM 사용. 해석/판단 전담.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from config.settings import ModelTier
from llm.client import LLMClient


class Regime(str, Enum):
    BULL_MARKET = "BULL_MARKET"         # 상승장
    BEAR_MARKET = "BEAR_MARKET"         # 하락장
    SIDEWAYS = "SIDEWAYS"               # 횡보장
    THEME_MARKET = "THEME_MARKET"       # 테마장
    POLICY_MARKET = "POLICY_MARKET"     # 정책장
    EARNINGS_MARKET = "EARNINGS_MARKET" # 실적장


@dataclass
class RegimeJudgment:
    regime: Regime
    confidence: int         # 0-100
    reason: str
    risk_level: str         # LOW / MEDIUM / HIGH


_SYSTEM_PROMPT = """당신은 한국 주식 시장의 시장 체제를 판단하는 전문 분석가입니다.
주어진 시장 데이터를 바탕으로 현재 시장 체제를 판단하세요.

출력 형식 (JSON):
{
  "regime": "BULL_MARKET|BEAR_MARKET|SIDEWAYS|THEME_MARKET|POLICY_MARKET|EARNINGS_MARKET",
  "confidence": 0-100,
  "reason": "판단 근거 2-3문장",
  "risk_level": "LOW|MEDIUM|HIGH"
}

판단 기준:
- BULL_MARKET: 코스피/코스닥 동반 상승, 거래대금 증가, 광범위한 상승
- BEAR_MARKET: 지수 하락, 거래대금 감소, 광범위한 하락
- SIDEWAYS: 뚜렷한 방향성 없음
- THEME_MARKET: 특정 테마 중심 상승, 지수와 무관
- POLICY_MARKET: 금리/환율/정책 이슈 주도
- EARNINGS_MARKET: 실적 발표 시즌, 실적 우수 종목 중심
"""


class RegimeAgent:
    """
    시장 체제 판단 Agent.
    허용: 시장 해석, 판단, 확신도 산정
    금지: 리스크 한도 변경, 전략 자동 적용
    """

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm or LLMClient()

    def judge(self, market_context: dict[str, Any]) -> RegimeJudgment:
        """
        시장 데이터를 받아 체제를 판단한다.

        market_context: {
            "kospi_change_pct": float,
            "kosdaq_change_pct": float,
            "market_news_summary": str,
            "sector_performance": dict,
        }
        """
        user_msg = f"""현재 시장 데이터:
- 코스피 등락률: {market_context.get('kospi_change_pct', 0):.2f}%
- 코스닥 등락률: {market_context.get('kosdaq_change_pct', 0):.2f}%
- 시장 뉴스 요약: {market_context.get('market_news_summary', '없음')}
- 섹터 성과: {market_context.get('sector_performance', {})}

현재 시장 체제를 판단하고 JSON으로 출력하세요."""

        raw = self._llm.call(system=_SYSTEM_PROMPT, user=user_msg, tier=ModelTier.STANDARD)
        return RegimeJudgment(
            regime=Regime(raw["regime"]),
            confidence=int(raw["confidence"]),
            reason=raw["reason"],
            risk_level=raw["risk_level"],
        )
