"""CircuitBreaker 테스트"""
from market_intelligence.circuit_breaker import CircuitBreaker


def test_circuit_closed_initially():
    cb = CircuitBreaker()
    assert cb.is_open("get_ohlcv") is False


def test_circuit_opens_after_threshold_failures():
    cb = CircuitBreaker(failure_threshold=3)
    cb.record_failure("get_ohlcv")
    cb.record_failure("get_ohlcv")
    assert cb.is_open("get_ohlcv") is False
    opened = cb.record_failure("get_ohlcv")
    assert opened is True
    assert cb.is_open("get_ohlcv") is True


def test_circuit_success_resets_failure_count():
    cb = CircuitBreaker(failure_threshold=3)
    cb.record_failure("get_ohlcv")
    cb.record_failure("get_ohlcv")
    cb.record_success("get_ohlcv")
    cb.record_failure("get_ohlcv")
    cb.record_failure("get_ohlcv")
    assert cb.is_open("get_ohlcv") is False


def test_circuit_failures_isolated_per_tool():
    cb = CircuitBreaker(failure_threshold=2)
    cb.record_failure("get_ohlcv")
    cb.record_failure("get_ohlcv")
    assert cb.is_open("get_ohlcv") is True
    assert cb.is_open("get_flow") is False


def test_reset_specific_tool():
    cb = CircuitBreaker(failure_threshold=2)
    cb.record_failure("get_ohlcv")
    cb.record_failure("get_ohlcv")
    assert cb.is_open("get_ohlcv") is True
    cb.reset("get_ohlcv")
    assert cb.is_open("get_ohlcv") is False


def test_reset_all_tools():
    cb = CircuitBreaker(failure_threshold=2)
    cb.record_failure("get_ohlcv")
    cb.record_failure("get_ohlcv")
    cb.record_failure("get_flow")
    cb.record_failure("get_flow")
    cb.reset()
    assert cb.is_open("get_ohlcv") is False
    assert cb.is_open("get_flow") is False
