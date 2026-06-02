"""
LLM 클라이언트 유틸리티
모든 Agent가 공통으로 사용하는 Claude API 래퍼
"""
from __future__ import annotations

import json
import os
from typing import Any

import anthropic

from config.settings import LLM_CONFIG


class LLMClient:
    """
    Anthropic Claude API 클라이언트.
    Agent에서만 사용 - Code에서는 절대 호출 금지.
    """

    def __init__(self, config=None):
        self._cfg = config or LLM_CONFIG
        self._client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY", "")
        )

    def call(
        self,
        system: str,
        user: str,
        expect_json: bool = True,
    ) -> dict[str, Any] | str:
        """
        Claude 호출. expect_json=True면 JSON 파싱 후 반환.
        비용 제어: Scanner 통과 종목에만 호출할 것.
        """
        response = self._client.messages.create(
            model=self._cfg.model,
            max_tokens=self._cfg.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        raw = response.content[0].text.strip()

        if not expect_json:
            return raw

        # JSON 블록 추출
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        return json.loads(raw)
