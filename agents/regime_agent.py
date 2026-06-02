"""
Regime Agent - 시장 체제 판단 Agent
LLM 사용. 해석/판단 전담.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from config.settings import ModelTier
from llm.client import LLMClient
from llm.soul import inject_agent

_SYSTEM_PROMPT = inject_agent("regime_agent")


class MarketStatus(str, Enum):
    GREEN  = "GREEN"   # 적극 매매 가능
    YELLOW = "YELLOW"  # 선별 매매
    RED    = "RED"     # 신규 매수 제한


class Regime(str, Enum):
    UPTREND        = "UPTREND"
    DOWNTREND      = "DOWNTREND"
    SIDEWAYS       = "SIDEWAYS"
    THEME_MARKET   = "THEME_MARKET"
    POLICY_MARKET  = "POLICY_MARKET"
    EARNINGS_MARKET = "EARNINGS_MARKET"
    RISK_OFF       = "RISK_OFF"


@dataclass
class RegimeJudgment:
    status: MarketStatus
    regime: Regime
    confidence: int
    reason: str
    risk_notes: list[str] = field(default_factory=list)


class RegimeAgent:
    """
    시장 체제 판단 Agent.
    허용: 시장 해석, 판단, 확신도 산정
    금지: 리스크 한도 변경, 전략 자동 적용
    """

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm or LLMClient()

    def judge(self, market_context: dict[str, Any]) -> RegimeJudgment:
        user_msg = f"""현재 시장 데이터:
- 코스피 등락률: {market_context.get('kospi_change_pct', 0):.2f}%
- 코스닥 등락률: {market_context.get('kosdaq_change_pct', 0):.2f}%
- 시장 뉴스 요약: {market_context.get('market_news_summary', '없음')}
- 섹터 성과: {market_context.get('sector_performance', {})}

현재 시장 체제와 매매 가능 여부를 판단하고 JSON으로 출력하세요."""

        raw = self._llm.call(system=_SYSTEM_PROMPT, user=user_msg, tier=ModelTier.STANDARD)
        return RegimeJudgment(
            status=MarketStatus(raw["status"]),
            regime=Regime(raw["regime"]),
            confidence=int(raw["confidence"]),
            reason=raw["reason"],
            risk_notes=raw.get("risk_notes", []),
        )
