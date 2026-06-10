"""도구별 Circuit Breaker.

스펙 섹션 3.4: 동일 도구 연속 3회 실패 → phase 내 비활성화.
phase 경계에서 reset()을 호출해 카운터를 초기화한다.
"""
from __future__ import annotations

from collections import defaultdict


class CircuitBreaker:
    """도구별 연속 실패 횟수를 추적하고 임계치 초과 시 회로를 연다."""

    def __init__(self, failure_threshold: int = 3) -> None:
        self._threshold = failure_threshold
        self._failure_counts: dict[str, int] = defaultdict(int)
        self._open: dict[str, bool] = {}

    def record_failure(self, tool: str) -> bool:
        """실패를 기록한다. 이 호출로 회로가 새로 열렸으면 True를 반환한다."""
        self._failure_counts[tool] += 1
        if self._failure_counts[tool] >= self._threshold and not self._open.get(tool):
            self._open[tool] = True
            return True
        return False

    def record_success(self, tool: str) -> None:
        self._failure_counts[tool] = 0
        self._open[tool] = False

    def is_open(self, tool: str) -> bool:
        return self._open.get(tool, False)

    def reset(self, tool: str | None = None) -> None:
        """phase 경계에서 호출. tool 지정 시 해당 도구만, 없으면 전체 초기화."""
        if tool is not None:
            self._failure_counts[tool] = 0
            self._open[tool] = False
        else:
            self._failure_counts.clear()
            self._open.clear()
