"""MILContext 테스트 - 캐시 + circuit breaker + fetch 통합"""
import pytest

from market_intelligence.base import MILContext, ToolFailure
from market_intelligence.circuit_breaker import CircuitBreaker


class StubKisApi:
    pass


def test_cached_call_returns_and_caches_fetch_result():
    ctx = MILContext(kis_api=StubKisApi())
    calls = []

    def fetch():
        calls.append(1)
        return {"x": 1}

    result1 = ctx.cached_call("get_market_context", "SCAN", {}, fetch)
    result2 = ctx.cached_call("get_market_context", "SCAN", {}, fetch)

    assert result1 == {"x": 1}
    assert result2 == {"x": 1}
    assert len(calls) == 1


def test_cached_call_raises_toolfailure_on_fetch_error(monkeypatch):
    monkeypatch.setattr("market_intelligence.base.time.sleep", lambda _: None)
    ctx = MILContext(kis_api=StubKisApi())

    def fetch():
        raise RuntimeError("boom")

    with pytest.raises(ToolFailure):
        ctx.cached_call("get_market_context", "SCAN", {}, fetch)


def test_cached_call_retries_before_succeeding(monkeypatch):
    monkeypatch.setattr("market_intelligence.base.time.sleep", lambda _: None)
    ctx = MILContext(kis_api=StubKisApi())
    fetch_calls = []

    def fetch():
        fetch_calls.append(1)
        if len(fetch_calls) < 2:
            raise RuntimeError("transient")
        return {"x": 1}

    result = ctx.cached_call("get_market_context", "SCAN", {}, fetch)

    assert result == {"x": 1}
    assert len(fetch_calls) == 2


def test_cached_call_opens_circuit_after_threshold_then_blocks_without_fetch(monkeypatch):
    monkeypatch.setattr("market_intelligence.base.time.sleep", lambda _: None)
    ctx = MILContext(
        kis_api=StubKisApi(),
        
        circuit_breaker=CircuitBreaker(failure_threshold=2),
    )
    fetch_calls = []

    def fetch():
        fetch_calls.append(1)
        raise RuntimeError("boom")

    for i in range(2):
        with pytest.raises(ToolFailure):
            ctx.cached_call("get_market_context", "SCAN", {"i": i}, fetch)

    with pytest.raises(ToolFailure, match="circuit breaker open"):
        ctx.cached_call("get_market_context", "SCAN", {"i": 99}, fetch)

    assert len(fetch_calls) == 6  # 처음 두 호출은 각 3회 재시도, 세 번째 호출은 circuit breaker가 fetch를 막음
