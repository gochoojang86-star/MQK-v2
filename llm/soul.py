"""
Soul Loader - MQK-v2 핵심 페르소나 주입 모듈
soul.md를 런타임에 읽어 Agent 시스템 프롬프트 앞에 주입한다.
soul.md 수정만으로 모든 Agent 철학이 갱신된다.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_SOUL_PATH = Path(__file__).parent.parent / "soul.md"


@lru_cache(maxsize=1)
def load_soul() -> str:
    """soul.md 전문을 반환한다. 프로세스 내 1회만 읽는다."""
    if not _SOUL_PATH.exists():
        return ""
    return _SOUL_PATH.read_text(encoding="utf-8").strip()


def inject(system_prompt: str) -> str:
    """시스템 프롬프트 앞에 Soul을 주입한다."""
    soul = load_soul()
    if not soul:
        return system_prompt
    return f"{soul}\n\n---\n\n{system_prompt}"
