"""
LLM 클라이언트 유틸리티
모든 Agent가 공통으로 사용하는 API 래퍼.
Agent에서만 사용 — Code에서는 절대 호출 금지.

인증 우선순위:
  1. OPENAI_API_KEY 환경변수
  2. ~/.codex/auth.json OAuth 토큰 (Hermes/Codex 로그인)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import OpenAI

from config.settings import LLM_CONFIG, ModelTier
from llm.oauth_loader import load_openai_token

logger = logging.getLogger(__name__)

# max_completion_tokens 사용 모델 (o-series + gpt-5.x)
_REASONING_MODELS = {
    "o1", "o1-mini", "o3", "o3-mini", "o4-mini", "o1-preview",
    "gpt-5.4", "gpt-5.4-mini", "gpt-5.5", "gpt-5",
}


def _resolve_api_key() -> str:
    """OPENAI_API_KEY → OAuth 토큰 순으로 인증 키를 결정."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        return key
    oauth = load_openai_token()
    if oauth:
        return oauth
    raise RuntimeError(
        "OpenAI 인증 수단 없음. OPENAI_API_KEY 설정 또는 "
        "Codex CLI 로그인(codex auth login) 후 재시도하세요."
    )


class LLMClient:
    """
    OpenAI API 클라이언트.
    tier 인자로 모델을 선택한다 — 직접 model 문자열을 넘기지 않는다.
    OPENAI_API_KEY 미설정 시 Hermes/Codex OAuth 토큰을 자동으로 사용.
    """

    def __init__(self, config=None):
        self._cfg = config or LLM_CONFIG
        self._client = OpenAI(api_key=_resolve_api_key())

    def call(
        self,
        system: str,
        user: str,
        tier: ModelTier = ModelTier.STANDARD,
        expect_json: bool = True,
    ) -> dict[str, Any] | str:
        """
        OpenAI 호출.
        - tier로 모델 자동 선택 (REASONING/STANDARD/FAST)
        - o-series는 temperature 제거, max_completion_tokens 사용
        - expect_json=True면 JSON 파싱 후 반환
        """
        model = self._cfg.model_for(tier)
        is_reasoning = model in _REASONING_MODELS

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
        }
        if is_reasoning:
            kwargs["max_completion_tokens"] = self._cfg.max_tokens
        else:
            kwargs["max_tokens"]  = self._cfg.max_tokens
            kwargs["temperature"] = self._cfg.temperature

        response = self._client.chat.completions.create(**kwargs)
        raw = response.choices[0].message.content.strip()

        # ── Anthropic (원복용 주석) ───────────────────────────────────────────
        # response = self._client.messages.create(
        #     model=self._cfg.model,
        #     max_tokens=self._cfg.max_tokens,
        #     system=system,
        #     messages=[{"role": "user", "content": user}],
        # )
        # raw = response.content[0].text.strip()

        if not expect_json:
            return raw

        # JSON 블록 추출
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"LLM이 유효한 JSON을 반환하지 않았습니다. "
                f"모델={self._cfg.model_for(tier)}, 오류={e}, "
                f"응답(앞 200자)={raw[:200]!r}"
            ) from e
