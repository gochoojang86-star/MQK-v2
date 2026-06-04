"""
Codex OAuth 토큰 로더
~/.codex/auth.json 의 OpenAI access_token을 LLMClient에 공급.
OPENAI_API_KEY 미설정 시 자동 폴백으로 사용.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_AUTH_PATH = Path.home() / ".codex" / "auth.json"


def load_openai_token() -> Optional[str]:
    """Codex auth.json에서 OpenAI access_token 반환.

    읽기 실패 또는 토큰 없으면 None 반환.
    """
    try:
        data = json.loads(_AUTH_PATH.read_text(encoding="utf-8"))
        token = data.get("tokens", {}).get("access_token", "")
        if token:
            logger.info("[OAuth] Codex auth.json에서 OpenAI 토큰 로드 완료")
            return token
    except FileNotFoundError:
        logger.debug("[OAuth] ~/.codex/auth.json 없음 — OAuth 폴백 불가")
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[OAuth] auth.json 읽기 실패: {e}")
    return None
