"""
Self Improvement Agent - 전략 개선 제안 Agent
LLM 사용.

중요: 실전 자동 반영 금지.
개선안 → Backtest → Paper Trade → User Approval → Production
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from config.settings import ModelTier
from llm.client import LLMClient
from llm.soul import inject_agent

_SYSTEM_PROMPT = inject_agent("self_improvement_agent")


class ChangeType(str, Enum):
    FILTER    = "FILTER"
    WEIGHT    = "WEIGHT"
    PROMPT    = "PROMPT"
    RISK_RULE = "RISK_RULE"
    SCANNER   = "SCANNER"


@dataclass
class ImprovementProposal:
    title: str
    hypothesis: str
    change_type: ChangeType
    expected_effect: str
    risk: str
    requires_backtest: bool
    settings_patch: list[dict[str, Any]] = field(default_factory=list)
    auto_apply: bool = False  # 항상 False — 예외 없음


class SelfImprovementAgent:
    """
    전략 개선 제안 Agent.
    auto_apply=False 강제 — 모든 개선안은 사람 승인 후 반영.
    """

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm or LLMClient()

    def suggest(
        self,
        trade_history: list[dict[str, Any]],
        journal_summary: str,
    ) -> list[ImprovementProposal]:
        if not trade_history:
            return []

        stats   = self._compute_stats(trade_history)
        user_msg = self._build_prompt(stats, journal_summary)
        raw     = self._llm.call(system=_SYSTEM_PROMPT, user=user_msg, tier=ModelTier.FAST)

        proposals = []
        for p in raw.get("improvement_proposals", [])[:5]:
            proposals.append(ImprovementProposal(
                title=p["title"],
                hypothesis=p.get("hypothesis", ""),
                change_type=ChangeType(p["change_type"]),
                expected_effect=p.get("expected_effect", ""),
                risk=p.get("risk", ""),
                requires_backtest=p.get("requires_backtest", True),
                settings_patch=p.get("settings_patch", []),
                auto_apply=False,  # 절대 자동 반영 금지
            ))
        return proposals

    def _compute_stats(self, history: list[dict]) -> dict:
        wins   = [t for t in history if t.get("pnl", 0) > 0]
        losses = [t for t in history if t.get("pnl", 0) < 0]
        total  = len(history)
        win_rate  = len(wins) / total * 100 if total else 0
        avg_win   = sum(t["pnl"] for t in wins)   / len(wins)   if wins   else 0
        avg_loss  = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
        return {
            "total_trades": total,
            "win_rate": round(win_rate, 1),
            "avg_win":  round(avg_win, 0),
            "avg_loss": round(avg_loss, 0),
            "profit_factor": abs(avg_win / avg_loss) if avg_loss else 0,
        }

    def _build_prompt(self, stats: dict, journal_summary: str) -> str:
        return f"""최근 거래 통계:
- 총 거래: {stats['total_trades']}건
- 승률: {stats['win_rate']}%
- 평균 수익: {stats['avg_win']:+,.0f}원
- 평균 손실: {stats['avg_loss']:+,.0f}원
- 손익비: {stats['profit_factor']:.2f}

복기 요약:
{journal_summary or '복기 데이터 없음'}

위 데이터를 분석하여 전략 개선안을 JSON으로 제안하세요.
모든 제안은 백테스트 검증 + 사용자 승인 후 반영됨을 전제로 합니다.
auto_apply는 항상 false입니다.

각 제안이 실제 설정 변경으로 연결될 수 있다면 settings_patch를 포함하세요.
형식:
"settings_patch": [
  {{"section": "RISK|SCANNER|LLM_CONFIG|EXECUTION", "key": "필드명", "value": 값}}
]

허용되지 않은 키는 넣지 마세요."""
