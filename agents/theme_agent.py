"""
Theme Agent - 주도 테마 분석 Agent
LLM 사용.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from config.settings import ModelTier
from llm.client import LLMClient
from llm.soul import inject_agent

_SYSTEM_PROMPT = inject_agent("theme_agent")


@dataclass
class ThemeItem:
    theme: str
    strength: int
    leader_candidates: list[str]
    reason: str
    risk: str
    theme_stage: str = ""           # 초입 / 중기 / 말기
    entry_verdict: str = ""         # 진입가능 / 주의 / 과열
    laggard_stocks: list[str] = field(default_factory=list)
    junk_warning: bool = False


@dataclass
class ThemeAnalysis:
    top_themes: list[ThemeItem]

    @property
    def best(self) -> ThemeItem | None:
        return self.top_themes[0] if self.top_themes else None


class ThemeAgent:
    """
    주도 테마 분석 Agent.
    허용: 테마 해석, 대장주 판단, 강도 산정
    금지: 직접 매수 주문, 리스크 변경
    """

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm or LLMClient()

    def analyze(self, market_context: dict[str, Any]) -> ThemeAnalysis:
        headlines = "\n".join(f"- {h}" for h in market_context.get("news_headlines", []))
        gainers = market_context.get("top_gainers", [])[:10]

        user_msg = f"""오늘 시장 데이터:

뉴스 헤드라인:
{headlines or '없음'}

상위 상승 종목:
{self._format_gainers(gainers)}

현재 주도 테마를 분석하고 JSON으로 출력하세요."""

        raw = self._llm.call(system=_SYSTEM_PROMPT, user=user_msg, tier=ModelTier.FAST)
        items = [
            ThemeItem(
                theme=t["theme"],
                strength=int(t["strength"]),
                leader_candidates=t.get("leader_candidates", []),
                reason=t["reason"],
                risk=t.get("risk", ""),
                theme_stage=t.get("theme_stage", ""),
                entry_verdict=t.get("entry_verdict", ""),
                laggard_stocks=t.get("laggard_stocks", []),
                junk_warning=bool(t.get("junk_warning", False)),
            )
            for t in raw.get("top_themes", [])
        ]
        return ThemeAnalysis(top_themes=items)

    def _format_gainers(self, gainers: list[dict]) -> str:
        if not gainers:
            return "없음"
        return "\n".join(
            f"- {g.get('name', '')} ({g.get('ticker', '')}): "
            f"+{g.get('change_pct', 0):.1f}% [{g.get('sector', '')}]"
            for g in gainers
        )
