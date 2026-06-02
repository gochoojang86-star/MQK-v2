"""
Self Improvement Agent - 전략 개선 제안 Agent
LLM 사용.

중요: 실전 자동 반영 금지.
개선안 → Backtest Code → Paper Trade → User Approval → Production
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config.settings import ModelTier
from llm.client import LLMClient
from llm.soul import inject


@dataclass
class ImprovementSuggestion:
    title: str
    category: str           # ENTRY / EXIT / FILTER / RISK / THEME_SELECTION
    description: str
    expected_improvement: str
    backtest_required: bool
    # 실전 자동 반영은 항상 False - 사람 승인 필수
    auto_apply: bool = False


_SYSTEM_PROMPT = """당신은 한국 주식 스윙 트레이딩 전략의 자기개선 전문가입니다.
축적된 거래 데이터와 복기 내용을 분석하여 전략 개선안을 제안하세요.

출력 형식 (JSON):
{
  "suggestions": [
    {
      "title": "개선안 제목",
      "category": "ENTRY|EXIT|FILTER|RISK|THEME_SELECTION",
      "description": "구체적인 개선 내용",
      "expected_improvement": "기대 효과",
      "backtest_required": true/false
    }
  ]
}

중요 제약사항:
- 제안은 반드시 백테스트로 검증 후 사람이 승인해야 실전 반영 가능
- 리스크 파라미터 변경 제안은 특별히 보수적으로 접근
- 몰빵/물타기 관련 제안은 절대 불가
"""


class SelfImprovementAgent:
    """
    전략 개선 제안 Agent.

    실전 자동 반영 금지. 모든 개선안은 다음 프로세스를 거쳐야 한다:
    Self Improvement Agent → Backtest Code → Paper Trade → User Approval → Production
    """

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm or LLMClient()

    def suggest(
        self,
        trade_history: list[dict[str, Any]],
        journal_summary: str,
    ) -> list[ImprovementSuggestion]:
        """
        거래 이력과 복기 요약을 바탕으로 개선안을 제안한다.
        반환된 개선안은 자동으로 실전에 반영되지 않는다.
        """
        if not trade_history:
            return []

        stats = self._compute_stats(trade_history)
        user_msg = self._build_prompt(stats, journal_summary)
        raw = self._llm.call(system=inject(_SYSTEM_PROMPT), user=user_msg, tier=ModelTier.REASONING)

        suggestions = []
        for s in raw.get("suggestions", [])[:5]:  # 최대 5개
            suggestions.append(ImprovementSuggestion(
                title=s["title"],
                category=s["category"],
                description=s["description"],
                expected_improvement=s["expected_improvement"],
                backtest_required=s.get("backtest_required", True),
                auto_apply=False,  # 절대 자동 반영 금지
            ))
        return suggestions

    def _compute_stats(self, history: list[dict]) -> dict:
        wins = [t for t in history if t.get("pnl", 0) > 0]
        losses = [t for t in history if t.get("pnl", 0) < 0]
        total = len(history)
        win_rate = len(wins) / total * 100 if total else 0
        avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
        return {
            "total_trades": total,
            "win_rate": round(win_rate, 1),
            "avg_win": round(avg_win, 0),
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
모든 제안은 백테스트 검증 후 사람의 승인이 필요함을 전제로 합니다."""
