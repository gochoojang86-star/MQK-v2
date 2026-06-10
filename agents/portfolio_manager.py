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
        candidate = ctx.get("candidate") or {}
        reaction = ctx.get("reaction") or {}
        position = ctx.get("position") or {}
        exit_signal = ctx.get("exit_signal")

        lines = [
            f"## 종목: {ctx.get('name', '')} ({ticker})",
            f"현재가: {ctx.get('current_price', 0):,.0f}원",
            f"포트폴리오 보유: {'예' if ctx.get('is_in_portfolio') else '아니오'}",
            "",
            "### 후보 스캐너",
        ]
        if candidate:
            lines += [
                f"- 후보 순위: {candidate.get('rank', '-')}",
                f"- 전략 타입: {candidate.get('strategy_type', 'TREND')}",
                f"- 기회 모드: {candidate.get('opportunity_mode', 'NORMAL')}",
                f"- 테마/섹터 내 순위: {candidate.get('theme_rank') or '-'}",
                f"- 스캐너 점수: {candidate.get('score', 0)}",
                f"- 대장주 점수: {candidate.get('leadership_score', 0)}",
                f"- 섹터: {candidate.get('sector', '')}",
                f"- 등락률: {candidate.get('change_pct', 0):.2f}%",
                f"- 거래대금: {candidate.get('trading_value', 0) / 1e8:.1f}억원",
                f"- 거래대금 순위: {candidate.get('trading_value_rank') or '-'}",
                f"- reversal 점수: {candidate.get('reversal_score', 0)}",
                f"- 20일 이격도: {candidate.get('disparity20_pct', 0):.2f}%",
                f"- 60일 이격도: {candidate.get('disparity60_pct', 0):.2f}%",
                f"- 과매도 사유: {candidate.get('oversold_reason') or '없음'}",
                f"- 통과 필터: {', '.join(candidate.get('passed', [])) or '없음'}",
                f"- 테마 대장 후보 여부: {'예' if candidate.get('is_theme_leader') else '아니오'}",
                f"- 후발주/소외주 경고: {'예' if candidate.get('is_laggard') else '아니오'}",
            ]

        if reaction:
            lines += [
                "",
                "### 가격·거래대금 반응",
                f"- 당일 가격 반응: {reaction.get('price_reaction_pct', 0):.2f}%",
                f"- 현재 거래대금: {reaction.get('current_trading_value', 0) / 1e8:.1f}억원",
                f"- 20일 평균 거래대금 대비: {reaction.get('trading_value_ratio_20d', 0):.2f}배",
            ]

        if position or exit_signal:
            lines += [
                "",
                "### 보유/익절 관리",
                f"- 청산 신호: {exit_signal or '없음'}",
                f"- 진입가: {position.get('entry_price', 0):,.0f}원",
                f"- 현재 손절가: {position.get('stop_loss_price', 0):,.0f}원",
                f"- 수익률: {position.get('pnl_pct', 0):.2f}%",
                f"- 1차익절 여부: {'예' if position.get('target1_hit') else '아니오'}",
                "손절/트레일링 스탑은 코드가 강제한다. 익절 신호에서만 HOLD 연장을 검토하라.",
                "HOLD를 선택하면 코드는 손절가를 올려 수익을 보호한다.",
            ]

        lines += [
            "",
            "### 시장 체제",
        ]
        if regime:
            lines += [
                f"- Status: {regime.status.value}",
                f"- Regime: {regime.regime.value} (확신도 {regime.confidence}%)",
                f"- Opportunity Mode: {getattr(regime, 'opportunity_mode', 'NORMAL').value if hasattr(getattr(regime, 'opportunity_mode', 'NORMAL'), 'value') else getattr(regime, 'opportunity_mode', 'NORMAL')}",
                f"- Scanner Mode: {getattr(regime, 'scanner_mode', 'TREND').value if hasattr(getattr(regime, 'scanner_mode', 'TREND'), 'value') else getattr(regime, 'scanner_mode', 'TREND')}",
            ]

        lines += ["", "### 테마"]
        if theme and theme.best:
            b = theme.best
            lines += [
                f"- 주도 테마: {b.theme} (강도 {b.strength})",
                f"- 대장 후보: {', '.join(b.leader_candidates[:3])}",
                f"- 테마 단계: {b.theme_stage or '미상'}",
                f"- 테마 진입판단: {b.entry_verdict or '미상'}",
                f"- 후발주 후보: {', '.join(b.laggard_stocks[:5]) or '없음'}",
                f"- 잡주/과열 경고: {'예' if b.junk_warning else '아니오'}",
                f"- 테마 리스크: {b.risk or '없음'}",
            ]

        lines += ["", "### 기술적 분석"]
        if tech:
            lines += [
                f"- ATR: {tech.atr:.0f}원  RSI: {tech.rsi:.1f}",
                f"- 20일 이격도: {tech.disparity20_pct:.2f}%  60일 이격도: {tech.disparity60_pct:.2f}%",
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
                f"- 프로그램 3일: {flow.program_net_3d / 1e8:.1f}억원",
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
