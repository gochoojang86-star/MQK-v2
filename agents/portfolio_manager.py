"""
Portfolio Manager Agent - 핵심 의사결정 Agent
LLM 사용. BUY/SELL/HOLD/WAIT 최종 판단.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from config.settings import ModelTier
from llm.client import LLMClient
from llm.soul import inject_agent

_SYSTEM_PROMPT = inject_agent("portfolio_manager")


class Decision(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    WAIT = "WAIT"


@dataclass
class PortfolioDecision:
    ticker: str
    decision: Decision
    confidence: int
    reason: str
    counter_argument: str
    code: str = ""
    name: str = ""
    strategy: str = ""
    entry_zone: str = ""
    required_checks: list[str] = field(
        default_factory=lambda: ["risk_check", "position_sizing", "telegram_approval"]
    )


class PortfolioManagerAgent:
    """
    핵심 의사결정 Agent.
    허용: BUY/SELL/HOLD/WAIT 판단, 확신도 산정, 자기반박
    금지: 수량, 손절, 리스크 한도, 물타기, 몰빵
    """

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm or LLMClient()

    def decide(self, ticker: str, context: dict[str, Any]) -> PortfolioDecision:
        user_msg = self._build_prompt(ticker, context)
        raw = self._llm.call(system=_SYSTEM_PROMPT, user=user_msg, tier=ModelTier.REASONING)

        return PortfolioDecision(
            ticker=ticker,
            decision=Decision(raw["decision"]),
            confidence=int(raw["confidence"]),
            reason=raw["reason"],
            counter_argument=raw["counter_argument"],
            code=raw.get("code", ticker),
            name=raw.get("name", context.get("name", "")),
            strategy=raw.get("strategy", ""),
            entry_zone=raw.get("entry_zone", ""),
            required_checks=raw.get(
                "required_checks", ["risk_check", "position_sizing", "telegram_approval"]
            ),
        )

    def _build_prompt(self, ticker: str, ctx: dict) -> str:
        regime = ctx.get("regime")
        theme  = ctx.get("theme")
        tech   = ctx.get("technical")
        flow   = ctx.get("flow")
        news   = ctx.get("news_evaluations", [])
        disc   = ctx.get("disclosure")

        lines = [
            f"## 종목: {ctx.get('name', '')} ({ticker})",
            f"현재가: {ctx.get('current_price', 0):,.0f}원",
            f"포트폴리오 보유: {'예' if ctx.get('is_in_portfolio') else '아니오'}",
            "",
            "### 시장 체제",
        ]
        if regime:
            lines += [
                f"- Status: {regime.status.value}",
                f"- Regime: {regime.regime.value} (확신도 {regime.confidence}%)",
            ]

        lines += ["", "### 테마"]
        if theme and theme.best:
            b = theme.best
            lines += [
                f"- 주도 테마: {b.theme} (강도 {b.strength})",
                f"- 대장 후보: {', '.join(b.leader_candidates[:3])}",
            ]

        lines += ["", "### 기술적 분석"]
        if tech:
            lines += [
                f"- ATR: {tech.atr:.0f}원  RSI: {tech.rsi:.1f}",
                f"- 52주 신고가: {'예' if tech.new_high_52w else '아니오'}",
                f"- VCP: {'예' if tech.is_vcp else '아니오'}  "
                f"박스돌파: {'예' if tech.is_box_breakout else '아니오'}  "
                f"눌림목: {'예' if tech.is_pullback else '아니오'}",
            ]

        lines += ["", "### 수급"]
        if flow:
            lines += [
                f"- 외국인 3일: {flow.foreign_net_3d / 1e8:.1f}억원",
                f"- 기관 3일: {flow.institution_net_3d / 1e8:.1f}억원",
                f"- 강한 수급: {'예' if flow.is_strong_inflow else '아니오'}",
            ]

        if news:
            lines += ["", "### 뉴스"]
            for n in news[:3]:
                lines.append(
                    f"- [{n.quality.value}/{n.category.value}] score:{n.news_score} — {n.reason[:80]}"
                )

        if disc:
            lines += [
                "", "### 공시",
                f"- 영향: {disc.impact.value}  score:{disc.disclosure_score}",
                f"- 리스크: {', '.join(disc.risk_flags) or '없음'}",
            ]

        lines += [
            "",
            "위 데이터를 Decision Hierarchy 순서로 종합하여 판단하고 JSON으로 출력하세요.",
            "확신도 50 미만이면 반드시 WAIT를 선택하세요.",
        ]
        return "\n".join(lines)
