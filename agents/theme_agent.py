"""
Theme Agent - 주도 테마 분석 Agent
LLM 사용.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config.settings import ModelTier
from llm.client import LLMClient
from llm.soul import inject


@dataclass
class ThemeAnalysis:
    theme: str              # 주도 테마 (예: 전력, AI, 방산)
    leader: str             # 대장주 티커 또는 종목명
    strength: int           # 테마 강도 0-100
    sub_themes: list[str]   # 연관 서브테마
    reason: str


_SYSTEM_PROMPT = """당신은 한국 주식 시장의 주도 테마를 분석하는 전문가입니다.
현재 시장에서 주도하는 테마와 대장주를 파악하세요.

한국 주요 테마: AI, 반도체, 전력/에너지, 원전, 방산, 바이오, 2차전지, 자동차, 조선, 게임

출력 형식 (JSON):
{
  "theme": "테마명",
  "leader": "대장주명",
  "strength": 0-100,
  "sub_themes": ["서브테마1", "서브테마2"],
  "reason": "분석 근거 2-3문장"
}

테마 강도 기준:
- 90+: 압도적 주도 (전 시장 관심 집중)
- 70-89: 강한 테마 (다수 종목 동반 상승)
- 50-69: 보통 테마 (일부 종목 움직임)
- 50 미만: 약한 테마 (단순 개별 이슈)
"""


class ThemeAgent:
    """
    주도 테마 분석 Agent.
    허용: 테마 해석, 대장주 판단, 강도 산정
    금지: 직접 매수 주문, 리스크 변경
    """

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm or LLMClient()

    def analyze(self, market_context: dict[str, Any]) -> ThemeAnalysis:
        """
        시장 뉴스/데이터를 받아 주도 테마를 분석한다.

        market_context: {
            "news_headlines": list[str],
            "top_gainers": list[dict],  # [{ticker, name, change_pct, sector}]
            "sector_heatmap": dict,
        }
        """
        headlines = "\n".join(f"- {h}" for h in market_context.get("news_headlines", []))
        gainers = market_context.get("top_gainers", [])[:10]

        user_msg = f"""오늘 시장 데이터:

뉴스 헤드라인:
{headlines or '없음'}

상위 상승 종목:
{self._format_gainers(gainers)}

현재 주도 테마를 분석하고 JSON으로 출력하세요."""

        raw = self._llm.call(system=inject(_SYSTEM_PROMPT), user=user_msg, tier=ModelTier.STANDARD)
        return ThemeAnalysis(
            theme=raw["theme"],
            leader=raw["leader"],
            strength=int(raw["strength"]),
            sub_themes=raw.get("sub_themes", []),
            reason=raw["reason"],
        )

    def _format_gainers(self, gainers: list[dict]) -> str:
        if not gainers:
            return "없음"
        lines = []
        for g in gainers:
            lines.append(
                f"- {g.get('name', '')} ({g.get('ticker', '')}): "
                f"+{g.get('change_pct', 0):.1f}% [{g.get('sector', '')}]"
            )
        return "\n".join(lines)
