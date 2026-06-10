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


class OpportunityMode(str, Enum):
    NORMAL = "NORMAL"
    SETUP4_PANIC = "SETUP4_PANIC"


class ScannerMode(str, Enum):
    TREND = "TREND"
    REVERSAL_ONLY = "REVERSAL_ONLY"


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
    opportunity_mode: OpportunityMode = OpportunityMode.NORMAL
    scanner_mode: ScannerMode = ScannerMode.TREND


class RegimeAgent:
    """
    시장 체제 판단 Agent.
    허용: 시장 해석, 판단, 확신도 산정
    금지: 리스크 한도 변경, 전략 자동 적용
    """

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm or LLMClient()

    def judge(self, market_context: dict[str, Any]) -> RegimeJudgment:
        prev_kospi_tv = market_context.get('prev_kospi_trading_value', 0)
        prev_kosdaq_tv = market_context.get('prev_kosdaq_trading_value', 0)
        prev_tv_note = (
            f"{prev_kospi_tv / 1e8:.0f}억 / {prev_kosdaq_tv / 1e8:.0f}억"
            if prev_kospi_tv or prev_kosdaq_tv else "데이터 없음"
        )

        user_msg = f"""장전 시장 데이터 (08:00 기준):

[전일 확정 데이터]
- 전일 코스피 등락률: {market_context.get('prev_kospi_change_pct', 0):.2f}%
- 전일 코스닥 등락률: {market_context.get('prev_kosdaq_change_pct', 0):.2f}%
- 전일 코스피/코스닥 거래대금: {prev_tv_note}

[당일 실시간 데이터 — 장전이면 0이 정상]
- 코스피 등락률: {market_context.get('kospi_change_pct', 0):.2f}%
- 코스닥 등락률: {market_context.get('kosdaq_change_pct', 0):.2f}%
- 코스피 거래대금: {market_context.get('kospi_trading_value', 0) / 1e8:.1f}억원
- 코스닥 거래대금: {market_context.get('kosdaq_trading_value', 0) / 1e8:.1f}억원
- 코스피 상승/하락 종목 수: {market_context.get('kospi_advancers', 0)} / {market_context.get('kospi_decliners', 0)}
- 코스닥 상승/하락 종목 수: {market_context.get('kosdaq_advancers', 0)} / {market_context.get('kosdaq_decliners', 0)}

[기타]
- 시장 뉴스 요약: {market_context.get('market_news_summary', '없음')}
- 섹터 성과: {market_context.get('sector_performance', {})}

전일 확정 데이터를 주요 근거로, 당일 실시간 데이터를 보조 참고로 사용하여
시장 체제와 매매 가능 여부를 판단하고 JSON으로 출력하세요."""

        raw = self._llm.call(system=_SYSTEM_PROMPT, user=user_msg, tier=ModelTier.STANDARD)
        return RegimeJudgment(
            status=MarketStatus(raw["status"]),
            regime=Regime(raw["regime"]),
            confidence=int(raw["confidence"]),
            reason=raw["reason"],
            risk_notes=raw.get("risk_notes", []),
            opportunity_mode=OpportunityMode(raw.get("opportunity_mode", "NORMAL")),
            scanner_mode=ScannerMode(raw.get("scanner_mode", "TREND")),
        )
