"""
Soul Loader - MQK v3 핵심 페르소나 + Agent별 프롬프트 주입 모듈

계층 구조:
  soul.md          ← 공통 철학 (모든 Agent)
  prompts/agents/  ← Agent별 역할/미션/출력 스펙

soul.md 또는 개별 MD 수정만으로 재배포 없이 프롬프트 갱신된다.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_BASE = Path(__file__).parent.parent
_SOUL_PATH = _BASE / "soul.md"
_AGENT_PROMPTS_DIR = _BASE / "prompts" / "agents"


@lru_cache(maxsize=1)
def load_soul() -> str:
    if not _SOUL_PATH.exists():
        return ""
    return _SOUL_PATH.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=16)
def load_agent_prompt(agent_name: str) -> str:
    """prompts/agents/{agent_name}.md 로드"""
    path = _AGENT_PROMPTS_DIR / f"{agent_name}.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def inject(system_prompt: str) -> str:
    """soul만 주입 (soul + system_prompt)"""
    soul = load_soul()
    if not soul:
        return system_prompt
    return f"{soul}\n\n---\n\n{system_prompt}"


def inject_agent(agent_name: str) -> str:
    """soul + 개별 Agent MD 합성 → 최종 시스템 프롬프트 반환"""
    soul = load_soul()
    agent_md = load_agent_prompt(agent_name)
    parts = [p for p in [soul, agent_md] if p]
    return "\n\n---\n\n".join(parts)
