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
_REASONING_MODEL_PREFIXES = (
    "o1", "o3", "o4", "gpt-5",
)


def _uses_max_completion_tokens(model: str) -> bool:
    """OpenAI Chat Completions에서 max_completion_tokens를 요구하는 모델 판별."""
    return model.startswith(_REASONING_MODEL_PREFIXES)


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
        is_reasoning = _uses_max_completion_tokens(model)

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
            # 모델이 JSON 오브젝트를 줄바꿈으로 연달아 반환하는 경우("Extra data")
            # 첫 번째 완전한 오브젝트만 취한다 (D1 라이브 테스트에서 gpt-5.4가
            # call_tool JSON 두 개를 한 응답에 반환해 phase 전체가 죽은 사례).
            try:
                obj, end = json.JSONDecoder().raw_decode(raw)
                logger.warning(
                    f"LLM 응답에 여분 데이터 — 첫 JSON 오브젝트만 사용 "
                    f"(전체 {len(raw)}자 중 {end}자, 모델={self._cfg.model_for(tier)})"
                )
                return obj
            except json.JSONDecodeError:
                pass
            raise ValueError(
                f"LLM이 유효한 JSON을 반환하지 않았습니다. "
                f"모델={self._cfg.model_for(tier)}, 오류={e}, "
                f"응답(앞 200자)={raw[:200]!r}"
            ) from e
