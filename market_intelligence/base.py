"""MIL 도구 공통 베이스 - 캐시, circuit breaker, KIS MCP/REST 폴백을 통합한다."""
from __future__ import annotations

import time
from typing import Any, Callable

from broker.kis_api import KISApi
# from broker.kis_mcp_client import KISMCPClient  # MCP 비활성화
from broker.kiwoom_api import KiwoomApi, KiwoomRateLimitError
from market_intelligence.cache import MILCache
from market_intelligence.circuit_breaker import CircuitBreaker


class ToolFailure(Exception):
    """MIL 도구 호출 실패. 호출부는 스펙 섹션 3.4 강등 규칙에 따라 처리한다."""


class MILContext:
    """모든 MIL 도구가 공유하는 의존성 컨테이너.

    각 도구 함수는 ctx.cached_call(tool, phase, cache_args, fetch_fn)을 통해
    캐시 → circuit breaker → 실제 fetch 순으로 호출한다.
    """

    def __init__(
        self,
        kis_api: KISApi,
        # mcp_client: KISMCPClient | None = None,  # MCP 비활성화
        kiwoom_api: KiwoomApi | None = None,
        cache: MILCache | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.kis_api = kis_api
        # self.mcp_client = mcp_client or KISMCPClient()  # MCP 비활성화
        self.kiwoom_api = kiwoom_api
        self.cache = cache or MILCache()
        self.circuit_breaker = circuit_breaker or CircuitBreaker()

    def cached_call(
        self,
        tool: str,
        phase: str,
        cache_args: dict,
        fetch_fn: Callable[[], Any],
    ) -> Any:
        cached = self.cache.get(tool, phase, cache_args)
        if cached is not None:
            return cached

        if self.circuit_breaker.is_open(tool):
            raise ToolFailure(f"{tool}: circuit breaker open")

        last_exc: Exception | None = None
        for attempt in range(1, 4):
            try:
                result = fetch_fn()
            except KiwoomRateLimitError as exc:
                last_exc = exc
                break
            except Exception as exc:
                last_exc = exc
                if attempt < 3:
                    time.sleep(1.0)
                continue

            self.circuit_breaker.record_success(tool)
            self.cache.set(tool, phase, cache_args, result)
            return result

        self.circuit_breaker.record_failure(tool)
        raise ToolFailure(f"{tool}: {last_exc}") from last_exc
