"""
Portfolio Manager Agent - 핵심 의사결정 Agent
LLM 사용. BUY/SELL/HOLD/WAIT 최종 판단.

철학: Jesse Livermore × William O'Neil × Mark Minervini × 한국형 테마 스윙
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from config.settings import ModelTier
from llm.client import LLMClient


class Decision(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    WAIT = "WAIT"


@dataclass
class PortfolioDecision:
    ticker: str
    decision: Decision
    confidence: int             # 0-100
    reason: str
    counter_argument: str       # 반대 논거 (자가 검증)
    entry_zone: str             # 진입 구간 설명
    exit_plan: str              # 청산 계획


_SYSTEM_PROMPT = """당신은 Jesse Livermore, William O'Neil, Mark Minervini의 철학과
한국 실전투자대회 우승자 계열의 테마 스윙 전략을 통합한 포트폴리오 매니저입니다.

투자 철학:
- 시장 → 테마 → 대장주 → 차트 → 수급 → 뉴스 → 리스크 → 진입 → 관리
- 강한 종목, 대장주, 추세, 거래량, 신고가 (O'Neil/Minervini)
- 테마 대장주, 거래대금, 눌림목, 수급 (한국형)

당신의 권한:
허용: 매수/매도/보유/대기 판단, 시장해석, 확신도 산정
금지: 수량 계산, 손절가 확정, 리스크 한도 변경 (Code가 담당)

출력 형식 (JSON):
{
  "decision": "BUY|SELL|HOLD|WAIT",
  "confidence": 0-100,
  "reason": "매수/매도 근거 3-5문장 (구체적 근거 포함)",
  "counter_argument": "반대 논거 2-3문장 (리스크/약점)",
  "entry_zone": "진입 구간 설명",
  "exit_plan": "청산 계획"
}

confidence 기준:
- 90+: 매우 강한 확신 (모든 조건 충족)
- 70-89: 강한 확신 (핵심 조건 충족)
- 50-69: 보통 확신 (조건 부분 충족, 신중 접근)
- 50 미만: WAIT 권장
"""


class PortfolioManagerAgent:
    """
    핵심 의사결정 Agent.
    모든 정보(Regime, Theme, Technical, Flow, News, Disclosure)를 종합하여
    최종 BUY/SELL/HOLD/WAIT를 판단한다.
    """

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm or LLMClient()

    def decide(self, ticker: str, context: dict[str, Any]) -> PortfolioDecision:
        """
        최종 투자 판단.

        context: {
            "ticker": str,
            "name": str,
            "regime": RegimeJudgment,
            "theme": ThemeAnalysis,
            "technical": TechnicalSignals,
            "flow": FlowSignals,
            "news_evaluations": list[NewsEvaluation],
            "disclosure": DisclosureInterpretation | None,
            "current_price": float,
            "is_in_portfolio": bool,
        }
        """
        user_msg = self._build_prompt(ticker, context)
        raw = self._llm.call(system=_SYSTEM_PROMPT, user=user_msg, tier=ModelTier.REASONING)

        return PortfolioDecision(
            ticker=ticker,
            decision=Decision(raw["decision"]),
            confidence=int(raw["confidence"]),
            reason=raw["reason"],
            counter_argument=raw["counter_argument"],
            entry_zone=raw.get("entry_zone", ""),
            exit_plan=raw.get("exit_plan", ""),
        )

    def _build_prompt(self, ticker: str, ctx: dict) -> str:
        regime = ctx.get("regime")
        theme = ctx.get("theme")
        tech = ctx.get("technical")
        flow = ctx.get("flow")
        news = ctx.get("news_evaluations", [])
        disc = ctx.get("disclosure")

        lines = [
            f"## 종목: {ctx.get('name', '')} ({ticker})",
            f"현재가: {ctx.get('current_price', 0):,.0f}원",
            f"포트폴리오 보유: {'예' if ctx.get('is_in_portfolio') else '아니오'}",
            "",
            "### 시장 체제",
            f"- Regime: {regime.regime if regime else 'N/A'} (확신도 {regime.confidence if regime else 0}%)",
            "",
            "### 테마",
        ]

        if theme:
            lines += [
                f"- 주도 테마: {theme.theme} (강도 {theme.strength})",
                f"- 대장주: {theme.leader}",
            ]

        lines += ["", "### 기술적 분석"]
        if tech:
            lines += [
                f"- ATR: {tech.atr:.0f}원",
                f"- RSI: {tech.rsi:.1f}",
                f"- 52주 신고가: {'예' if tech.new_high_52w else '아니오'}",
                f"- VCP: {'예' if tech.is_vcp else '아니오'}",
                f"- 박스돌파: {'예' if tech.is_box_breakout else '아니오'}",
                f"- 눌림목: {'예' if tech.is_pullback else '아니오'}",
                f"- MA배열: 20>{tech.ma20:.0f} / 60>{tech.ma60:.0f}" if tech.ma60 else "",
            ]

        lines += ["", "### 수급"]
        if flow:
            lines += [
                f"- 외국인 3일 순매수: {flow.foreign_net_3d / 1e8:.1f}억원",
                f"- 기관 3일 순매수: {flow.institution_net_3d / 1e8:.1f}억원",
                f"- 강한 수급: {'예' if flow.is_strong_inflow else '아니오'}",
            ]

        if news:
            lines += ["", "### 뉴스"]
            for n in news[:3]:
                lines.append(f"- {n.quality.value}: {n.reason[:80]}")

        if disc:
            lines += [
                "", "### 공시",
                f"- 유형: {disc.disclosure_type.value}",
                f"- 영향: {disc.impact.value}",
                f"- 희석위험: {'있음' if disc.dilution_risk else '없음'}",
            ]

        lines += [
            "",
            "위 데이터를 종합하여 매수/매도/보유/대기 판단을 JSON으로 출력하세요.",
            "확신도가 50 미만이면 WAIT를 선택하세요.",
        ]

        return "\n".join(lines)
