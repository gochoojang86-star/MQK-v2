# MQK v3 아젠틱 트레이딩 시스템 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** v2의 RED hard block(`orchestrator.py:296`)을 제거하고, 단일 TradingAgent(LLM)가 16개 Market Intelligence Layer 도구를 Phase별로 자율 사용하며 BUY/SELL proposal을 생성하고, v2 Safety Layer(RiskOfficer/PositionSizer/Telegram/OrderManager)가 이를 코드로 강제하는 MQK v3 아젠틱 구조를 구현한다.

**Architecture:** 기존 `RegimeAgent`를 확장해 `risk_guidance` + `drift_triggers`를 출력하게 하고, `RegimeSafetyBounds`로 코드 클램핑한다. `market_intelligence/` 패키지에 16개 래핑 도구(KIS MCP 우선, REST 폴백, TTL 캐시 + circuit breaker)를 만든다. `RegimeDriftDetector`가 5분마다 무료로 drift_triggers를 체크하고, 발동 시 Lite LLM(gpt-5.4-mini)을 호출한다. `TradingAgent`가 Phase(PREMARKET/SCAN/INTRADAY/CLOSE)별 프롬프트와 사전주입 컨텍스트로 단일 LLM 루프를 실행하고, `OrchestratorV3`가 PM2 스케줄에 맞춰 각 Phase를 호출한다.

**Tech Stack:** Python 3.12, pytest, OpenAI API (gpt-5.4 / gpt-5.4-mini via `llm/client.py`), KIS REST API (`broker/kis_api.py`) + KIS MCP SSE (`broker/kis_mcp_client.py`), SQLite (`codes/trade_journal.py`), PM2 (`ecosystem.config.cjs`)

**참조 스펙:** `docs/superpowers/specs/2026-06-09-mqk-v3-agentic-design.md`

---

## 작업 순서 개요

1. **Phase 1 — Foundation**: RegimeSafetyBounds, clamp_risk_guidance, TradeJournal.today_summary
2. **Phase 2 — MIL 인프라**: MILCache (phase-aware TTL), CircuitBreaker
3. **Phase 3 — MIL 도구 16개**: market.py(4) / screening.py(3) / stock.py(5) / risk_filter.py(2) / portfolio.py(2)
4. **Phase 4 — RegimeAgent 확장**: risk_guidance + drift_triggers 출력, last_regime.json 저장
5. **Phase 5 — RegimeDriftDetector**: 3-tier 비용 모델, CAUTION 카운터
6. **Phase 6 — TradingAgent**: Phase별 프롬프트 + 사전주입 컨텍스트 + MIL 도구 바인딩
7. **Phase 7 — OrchestratorV3 + PM2**: 스케줄 통합

---

### Task 1: RegimeSafetyBounds + clamp_risk_guidance

**Files:**
- Modify: `config/settings.py`
- Modify: `codes/risk_officer.py`
- Test: `tests/test_risk_officer.py`

- [ ] **Step 1: Write failing test for RegimeSafetyBounds 기본값**

`tests/test_risk_officer.py` 파일 끝에 추가:

```python
from config.settings import RegimeSafetyBounds
from codes.risk_officer import clamp_risk_guidance


def test_regime_safety_bounds_defaults():
    bounds = RegimeSafetyBounds()
    assert bounds.min_buy_confidence_threshold == 65.0
    assert bounds.max_buy_confidence_threshold == 95.0
    assert bounds.min_risk_per_trade_pct == 0.10
    assert bounds.max_risk_per_trade_pct == 0.50
    assert bounds.min_positions == 1
    assert bounds.max_positions == 5
    assert bounds.min_trading_value_krw == 5_000_000_000


def test_clamp_risk_guidance_within_bounds_unchanged():
    raw = {
        "buy_confidence_threshold": 75,
        "risk_per_trade_pct": 0.35,
        "max_positions": 4,
        "min_trading_value_krw": 10_000_000_000,
    }
    clamped = clamp_risk_guidance(raw)
    assert clamped == {
        "buy_confidence_threshold": 75,
        "risk_per_trade_pct": 0.35,
        "max_positions": 4,
        "min_trading_value_krw": 10_000_000_000,
    }


def test_clamp_risk_guidance_clamps_extreme_llm_values():
    raw = {
        "buy_confidence_threshold": 30,      # LLM이 너무 낮게 선언
        "risk_per_trade_pct": 2.0,           # 위험할 정도로 큰 값
        "max_positions": 20,                 # 한도 초과
        "min_trading_value_krw": 1_000_000,  # 너무 작음
    }
    clamped = clamp_risk_guidance(raw)
    assert clamped["buy_confidence_threshold"] == 65.0
    assert clamped["risk_per_trade_pct"] == 0.50
    assert clamped["max_positions"] == 5
    assert clamped["min_trading_value_krw"] == 5_000_000_000


def test_clamp_risk_guidance_fills_missing_keys_with_defaults():
    clamped = clamp_risk_guidance({})
    assert clamped["buy_confidence_threshold"] == 65.0
    assert clamped["risk_per_trade_pct"] == 0.10
    assert clamped["max_positions"] == 1
    assert clamped["min_trading_value_krw"] == 5_000_000_000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_risk_officer.py -k clamp -v`
Expected: FAIL with `ImportError: cannot import name 'RegimeSafetyBounds'`

- [ ] **Step 3: Add RegimeSafetyBounds to config/settings.py**

`config/settings.py`에서 `RiskConfig` 클래스 정의 바로 다음에 추가 (대략 33번째 줄 이후, `@dataclass(frozen=True)\nclass ScannerConfig:` 앞):

```python
@dataclass(frozen=True)
class RegimeSafetyBounds:
    """RegimeAgent가 선언한 risk_guidance 값의 코드 강제 한계.

    LLM이 risk_guidance에 어떤 값을 선언해도 이 범위를 벗어나면
    clamp_risk_guidance()가 강제로 잘라낸다. v2 RiskConfig가 천장.
    """
    min_buy_confidence_threshold: float = 65.0
    max_buy_confidence_threshold: float = 95.0
    min_risk_per_trade_pct: float = 0.10
    max_risk_per_trade_pct: float = 0.50   # RiskConfig.risk_per_trade_pct(0.5)가 천장
    min_positions: int = 1
    max_positions: int = 5                  # RiskConfig.max_positions(5)가 천장
    min_trading_value_krw: int = 5_000_000_000
```

파일 하단의 전역 인스턴스 선언부(예: `RISK = RiskConfig()` 등이 있는 곳)에 추가:

```python
REGIME_SAFETY_BOUNDS = RegimeSafetyBounds()
```

- [ ] **Step 4: Add clamp_risk_guidance to codes/risk_officer.py**

`codes/risk_officer.py` 상단 import 수정:

```python
from config.settings import RISK, RegimeSafetyBounds, REGIME_SAFETY_BOUNDS
```

파일 끝(`get_risk_summary` 메서드 다음, 클래스 바깥)에 모듈 레벨 함수 추가:

```python
def clamp_risk_guidance(raw: dict, bounds: RegimeSafetyBounds | None = None) -> dict:
    """RegimeAgent가 선언한 risk_guidance를 RegimeSafetyBounds 범위로 강제 클램핑한다.

    LLM이 risk_guidance를 누락하거나 극단값을 선언해도
    이 함수를 통과하면 항상 안전 범위 내의 값만 남는다.
    """
    b = bounds or REGIME_SAFETY_BOUNDS

    buy_confidence_threshold = raw.get("buy_confidence_threshold", b.min_buy_confidence_threshold)
    risk_per_trade_pct = raw.get("risk_per_trade_pct", b.min_risk_per_trade_pct)
    max_positions = raw.get("max_positions", b.min_positions)
    min_trading_value_krw = raw.get("min_trading_value_krw", b.min_trading_value_krw)

    return {
        "buy_confidence_threshold": min(
            max(buy_confidence_threshold, b.min_buy_confidence_threshold),
            b.max_buy_confidence_threshold,
        ),
        "risk_per_trade_pct": min(
            max(risk_per_trade_pct, b.min_risk_per_trade_pct),
            b.max_risk_per_trade_pct,
        ),
        "max_positions": int(min(
            max(max_positions, b.min_positions),
            b.max_positions,
        )),
        "min_trading_value_krw": max(
            min_trading_value_krw,
            b.min_trading_value_krw,
        ),
    }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_risk_officer.py -v`
Expected: All PASS, including the 4 new tests

- [ ] **Step 6: Commit**

```bash
cd /mnt/c/Users/gocho/MQK-v2
git add config/settings.py codes/risk_officer.py tests/test_risk_officer.py
git commit -m "feat(v3): add RegimeSafetyBounds and clamp_risk_guidance"
```

---

### Task 2: TradeJournal.today_summary()

**Files:**
- Modify: `codes/trade_journal.py`
- Test: `tests/test_trade_journal.py`

- [ ] **Step 1: Read existing test file to match fixture style**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_trade_journal.py -v --collect-only`

(이 명령으로 기존 fixture 이름과 `TradeJournal(db_path=...)` 생성 패턴을 확인한다. 아래 테스트는 임시 DB 경로를 사용하는 기존 패턴을 따른다고 가정한다 — 만약 fixture 이름이 다르면 동일한 fixture를 재사용하도록 맞춘다.)

- [ ] **Step 2: Write failing test for today_summary**

`tests/test_trade_journal.py` 파일 끝에 추가 (기존 파일에 `journal` fixture 또는 `tmp_path` 기반 헬퍼가 있다면 그것을 사용; 없다면 아래처럼 직접 생성):

```python
def test_today_summary_no_trades(tmp_path):
    journal = TradeJournal(db_path=tmp_path / "trades.db")
    summary = journal.today_summary()
    assert summary["trade_count"] == 0
    assert summary["realized_pnl_pct"] == 0.0
    assert summary["win"] == 0
    assert summary["loss"] == 0
    assert summary["last_trade"] is None


def test_today_summary_with_closed_trade(tmp_path):
    journal = TradeJournal(db_path=tmp_path / "trades.db")
    today = datetime.now().strftime("%Y-%m-%d")

    trade_id = journal.open_trade(
        ticker="005930",
        name="삼성전자",
        entry_date=today,
        entry_price=70000,
        quantity=10,
        stop_loss_price=67000,
        entry_reason="TREND",
        confidence=80,
    )
    journal.close_trade(
        trade_id=trade_id,
        exit_date=today,
        exit_price=69440,
        exit_reason="STOP_LOSS",
    )

    summary = journal.today_summary()
    assert summary["trade_count"] == 1
    assert summary["loss"] == 1
    assert summary["win"] == 0
    assert summary["last_trade"]["ticker"] == "005930"
    assert summary["last_trade"]["result"] == "LOSS"
    assert summary["last_trade"]["pct"] < 0
```

> 주의: `open_trade`/`close_trade`의 정확한 시그니처(파라미터 이름, 반환값이 trade_id인지 여부)는 `codes/trade_journal.py`의 기존 정의를 확인 후 위 호출부를 맞춰 조정한다. 시그니처가 다르면 이 스텝에서 테스트 코드를 실제 시그니처에 맞게 수정한다.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_trade_journal.py -k today_summary -v`
Expected: FAIL with `AttributeError: 'TradeJournal' object has no attribute 'today_summary'`

- [ ] **Step 4: Implement today_summary() in codes/trade_journal.py**

`get_daily_summary` 메서드 다음에 추가:

```python
    def today_summary(self) -> dict:
        """현재 날짜 기준 거래 요약. TradingAgent 사전주입 컨텍스트용."""
        today = datetime.now().strftime("%Y-%m-%d")
        summary = self.get_daily_summary(today)
        return {
            "trade_count": summary["total_trades"],
            "realized_pnl_pct": round(
                sum(t.get("pnl_pct") or 0 for t in self.get_closed_trades(days=1)), 2
            ),
            "win": summary["win_trades"],
            "loss": summary["loss_trades"],
            "open_positions": len(self.get_open_positions()),
            "last_trade": self._last_trade_summary(today),
        }

    def _last_trade_summary(self, date: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT ticker, result, pnl_pct FROM trades "
                "WHERE exit_date=? ORDER BY id DESC LIMIT 1",
                (date,),
            ).fetchone()
        if row is None:
            return None
        r = dict(row)
        return {
            "ticker": r["ticker"],
            "result": r.get("result"),
            "pct": r.get("pnl_pct") or 0,
        }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_trade_journal.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
cd /mnt/c/Users/gocho/MQK-v2
git add codes/trade_journal.py tests/test_trade_journal.py
git commit -m "feat(v3): add TradeJournal.today_summary for agent context injection"
```

---

### Task 3: MIL 인프라 — MILCache (phase-aware TTL)

**Files:**
- Create: `market_intelligence/__init__.py`
- Create: `market_intelligence/cache.py`
- Test: `tests/test_mil_cache.py`

- [ ] **Step 1: Create package init**

`market_intelligence/__init__.py`:

```python
"""Market Intelligence Layer - LLM에게 노출되는 16개 KIS API 래핑 도구."""
```

- [ ] **Step 2: Write failing test**

`tests/test_mil_cache.py`:

```python
"""MILCache 테스트"""
from datetime import datetime, timedelta

from market_intelligence.cache import MILCache


def test_cache_miss_returns_none():
    cache = MILCache()
    assert cache.get("get_ohlcv", "SCAN", {"ticker": "005930"}) is None


def test_cache_hit_returns_value():
    cache = MILCache()
    cache.set("get_ohlcv", "SCAN", {"ticker": "005930"}, {"price": 70000})
    assert cache.get("get_ohlcv", "SCAN", {"ticker": "005930"}) == {"price": 70000}


def test_cache_different_args_are_different_keys():
    cache = MILCache()
    cache.set("get_ohlcv", "SCAN", {"ticker": "005930"}, {"price": 70000})
    assert cache.get("get_ohlcv", "SCAN", {"ticker": "000660"}) is None


def test_cache_expires_after_ttl():
    cache = MILCache()
    cache.set("get_realtime_price", "INTRADAY", {"ticker": "005930"}, {"price": 70000})
    # get_realtime_price/INTRADAY TTL = 15초. 직접 내부 timestamp를 과거로 조작.
    key = cache._key("get_realtime_price", "INTRADAY", {"ticker": "005930"})
    value, _ = cache._store[key]
    cache._store[key] = (value, datetime.now() - timedelta(seconds=20))
    assert cache.get("get_realtime_price", "INTRADAY", {"ticker": "005930"}) is None


def test_cache_unknown_tool_uses_default_ttl():
    cache = MILCache()
    cache.set("get_unknown_tool", "SCAN", {}, {"x": 1})
    assert cache.get("get_unknown_tool", "SCAN", {}) == {"x": 1}


def test_invalidate_tool_clears_all_entries_for_tool():
    cache = MILCache()
    cache.set("get_ohlcv", "SCAN", {"ticker": "005930"}, {"price": 1})
    cache.set("get_ohlcv", "SCAN", {"ticker": "000660"}, {"price": 2})
    cache.set("get_flow", "SCAN", {"ticker": "005930"}, {"flow": 1})
    cache.invalidate_tool("get_ohlcv")
    assert cache.get("get_ohlcv", "SCAN", {"ticker": "005930"}) is None
    assert cache.get("get_ohlcv", "SCAN", {"ticker": "000660"}) is None
    assert cache.get("get_flow", "SCAN", {"ticker": "005930"}) == {"flow": 1}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_mil_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_intelligence'`

- [ ] **Step 4: Implement market_intelligence/cache.py**

```python
"""Phase-aware TTL 캐시.

스펙 섹션 3.5 TTL 표를 (tool, phase) → seconds 로 인코딩한다.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

# (tool_name, phase) -> TTL seconds. 스펙 섹션 3.5 기준.
TTL_TABLE: dict[tuple[str, str], int] = {
    ("get_ohlcv", "PREMARKET"): 300,
    ("get_ohlcv", "SCAN"): 120,
    ("get_ohlcv", "INTRADAY"): 120,
    ("get_ohlcv", "CLOSE"): 86400,

    ("get_realtime_price", "INTRADAY"): 15,

    ("get_intraday_candles", "INTRADAY"): 60,

    ("get_flow", "PREMARKET"): 600,
    ("get_flow", "SCAN"): 300,
    ("get_flow", "INTRADAY"): 300,
    ("get_flow", "CLOSE"): 900,

    ("get_news_stock", "PREMARKET"): 1800,
    ("get_news_stock", "SCAN"): 300,
    ("get_news_stock", "INTRADAY"): 600,
    ("get_news_stock", "CLOSE"): 900,

    ("get_news_market", "PREMARKET"): 1800,
    ("get_news_market", "SCAN"): 300,
    ("get_news_market", "INTRADAY"): 600,
    ("get_news_market", "CLOSE"): 900,

    ("get_stock_status", "PREMARKET"): 3600,
    ("get_stock_status", "SCAN"): 600,
    ("get_stock_status", "INTRADAY"): 600,
    ("get_stock_status", "CLOSE"): 3600,

    ("get_event_schedule", "PREMARKET"): 86400,
    ("get_event_schedule", "SCAN"): 86400,
    ("get_event_schedule", "INTRADAY"): 86400,
    ("get_event_schedule", "CLOSE"): 86400,

    ("get_market_context", "PREMARKET"): 300,
    ("get_market_context", "SCAN"): 120,
    ("get_market_context", "INTRADAY"): 120,
    ("get_market_context", "CLOSE"): 300,

    ("get_sector_breadth", "PREMARKET"): 300,
    ("get_sector_breadth", "SCAN"): 180,
    ("get_sector_breadth", "INTRADAY"): 180,
    ("get_sector_breadth", "CLOSE"): 300,
}

DEFAULT_TTL_SECONDS = 60


class MILCache:
    """도구 호출 결과를 (tool, phase, args) 키로 캐싱한다."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, datetime]] = {}

    def _key(self, tool: str, phase: str, args: dict) -> str:
        return f"{tool}:{phase}:{json.dumps(args, sort_keys=True, default=str)}"

    def _ttl(self, tool: str, phase: str) -> int:
        return TTL_TABLE.get((tool, phase), DEFAULT_TTL_SECONDS)

    def get(self, tool: str, phase: str, args: dict) -> Any | None:
        key = self._key(tool, phase, args)
        entry = self._store.get(key)
        if entry is None:
            return None
        value, ts = entry
        if (datetime.now() - ts).total_seconds() > self._ttl(tool, phase):
            del self._store[key]
            return None
        return value

    def set(self, tool: str, phase: str, args: dict, value: Any) -> None:
        key = self._key(tool, phase, args)
        self._store[key] = (value, datetime.now())

    def invalidate_tool(self, tool: str) -> None:
        prefix = f"{tool}:"
        for key in [k for k in self._store if k.startswith(prefix)]:
            del self._store[key]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_mil_cache.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
cd /mnt/c/Users/gocho/MQK-v2
git add market_intelligence/__init__.py market_intelligence/cache.py tests/test_mil_cache.py
git commit -m "feat(v3): add MIL phase-aware TTL cache"
```

---

### Task 4: MIL 인프라 — CircuitBreaker

**Files:**
- Create: `market_intelligence/circuit_breaker.py`
- Test: `tests/test_mil_circuit_breaker.py`

- [ ] **Step 1: Write failing test**

`tests/test_mil_circuit_breaker.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_mil_circuit_breaker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_intelligence.circuit_breaker'`

- [ ] **Step 3: Implement market_intelligence/circuit_breaker.py**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_mil_circuit_breaker.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Users/gocho/MQK-v2
git add market_intelligence/circuit_breaker.py tests/test_mil_circuit_breaker.py
git commit -m "feat(v3): add MIL circuit breaker"
```

---

### Task 5: KISApi.raw_get + MILContext (캐시/circuit breaker/MCP 통합)

**Files:**
- Modify: `broker/kis_api.py`
- Create: `market_intelligence/base.py`
- Test: `tests/test_kis_api.py`
- Test: `tests/test_mil_base.py`

- [ ] **Step 1: Write failing test for KISApi.raw_get**

`tests/test_kis_api.py` 파일 끝에 추가:

```python
def test_raw_get_calls_kis_api_with_tr_id_and_returns_json(tmp_path, monkeypatch):
    cache_path = tmp_path / "kis_token.json"
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"rt_cd": "0", "output": {"foo": "bar"}}

    def fake_get(url, headers, params, timeout):
        calls.append((url, headers, params, timeout))
        return FakeResponse()

    monkeypatch.setattr("broker.kis_api.requests.get", fake_get)
    monkeypatch.setattr(KISApi, "_get_token", lambda self, mode=None: "token")

    api = KISApi(config=FakeKISConfig(), token_cache_path=cache_path)
    result = api.raw_get(
        "FHPUP02140000",
        "domestic-stock/v1/quotations/inquire-index-category-price",
        {"FID_COND_MRKT_DIV_CODE": "U"},
    )

    assert result == {"rt_cd": "0", "output": {"foo": "bar"}}
    url, headers, params, timeout = calls[0]
    assert url.endswith("/uapi/domestic-stock/v1/quotations/inquire-index-category-price")
    assert headers["tr_id"] == "FHPUP02140000"
    assert params == {"FID_COND_MRKT_DIV_CODE": "U"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_kis_api.py -k raw_get -v`
Expected: FAIL with `AttributeError: 'KISApi' object has no attribute 'raw_get'`

- [ ] **Step 3: Implement KISApi.raw_get**

`broker/kis_api.py`의 `_get_with_retry` 메서드 바로 다음에 추가:

```python
    def raw_get(self, tr_id: str, path: str, params: dict, mode: str | None = None) -> dict:
        """MIL 도구가 사용하는 범용 KIS REST GET 호출.

        Args:
            tr_id: KIS 거래ID (e.g. "FHPUP02140000")
            path: /uapi/ 이후 경로 (e.g. "domestic-stock/v1/quotations/inquire-index-category-price")
            params: 쿼리 파라미터
            mode: "real"|"paper". 미지정 시 self._data_mode 사용
        """
        mode = mode or self._data_mode
        url = f"{self._base_url_for(mode)}/uapi/{path}"
        resp = self._get_with_retry(
            url,
            headers=self._headers(tr_id, mode=mode),
            params=params,
            timeout=10,
        )
        return resp.json()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_kis_api.py -v`
Expected: All PASS

- [ ] **Step 5: Write failing test for MILContext**

`tests/test_mil_base.py`:

```python
"""MILContext 테스트 - 캐시 + circuit breaker + fetch 통합"""
import pytest

from market_intelligence.base import MILContext, ToolFailure
from market_intelligence.circuit_breaker import CircuitBreaker


class StubKisApi:
    pass


class StubMcpClient:
    @property
    def available(self) -> bool:
        return False


def test_cached_call_returns_and_caches_fetch_result():
    ctx = MILContext(kis_api=StubKisApi(), mcp_client=StubMcpClient())
    calls = []

    def fetch():
        calls.append(1)
        return {"x": 1}

    result1 = ctx.cached_call("get_market_context", "SCAN", {}, fetch)
    result2 = ctx.cached_call("get_market_context", "SCAN", {}, fetch)

    assert result1 == {"x": 1}
    assert result2 == {"x": 1}
    assert len(calls) == 1


def test_cached_call_raises_toolfailure_on_fetch_error():
    ctx = MILContext(kis_api=StubKisApi(), mcp_client=StubMcpClient())

    def fetch():
        raise RuntimeError("boom")

    with pytest.raises(ToolFailure):
        ctx.cached_call("get_market_context", "SCAN", {}, fetch)


def test_cached_call_opens_circuit_after_threshold_then_blocks_without_fetch():
    ctx = MILContext(
        kis_api=StubKisApi(),
        mcp_client=StubMcpClient(),
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

    assert len(fetch_calls) == 2  # 세 번째 호출은 circuit breaker가 fetch를 막음
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_mil_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_intelligence.base'`

- [ ] **Step 7: Implement market_intelligence/base.py**

```python
"""MIL 도구 공통 베이스 - 캐시, circuit breaker, KIS MCP/REST 폴백을 통합한다."""
from __future__ import annotations

from typing import Any, Callable

from broker.kis_api import KISApi
from broker.kis_mcp_client import KISMCPClient
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
        mcp_client: KISMCPClient | None = None,
        cache: MILCache | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.kis_api = kis_api
        self.mcp_client = mcp_client or KISMCPClient()
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

        try:
            result = fetch_fn()
        except Exception as exc:
            self.circuit_breaker.record_failure(tool)
            raise ToolFailure(f"{tool}: {exc}") from exc

        self.circuit_breaker.record_success(tool)
        self.cache.set(tool, phase, cache_args, result)
        return result
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_mil_base.py -v`
Expected: All PASS

- [ ] **Step 9: Commit**

```bash
cd /mnt/c/Users/gocho/MQK-v2
git add broker/kis_api.py market_intelligence/base.py tests/test_kis_api.py tests/test_mil_base.py
git commit -m "feat(v3): add KISApi.raw_get and MILContext for MIL tools"
```

---

### Task 6: MIL 시장관찰 도구 4개 — market_intelligence/market.py

**Files:**
- Create: `market_intelligence/market.py`
- Test: `tests/test_mil_market.py`

- [ ] **Step 1: Write failing tests**

`tests/test_mil_market.py`:

```python
"""market_intelligence/market.py 테스트"""
from market_intelligence.base import MILContext
from market_intelligence.market import (
    get_market_context,
    get_sector_breadth,
    get_intraday_index_candles,
    get_news_market,
)


class StubKisApi:
    def __init__(self, raw_responses=None, index_status=None):
        self._raw_responses = raw_responses or {}
        self._index_status = index_status or {}
        self.raw_get_calls = []

    def get_index_status(self):
        return self._index_status

    def raw_get(self, tr_id, path, params, mode=None):
        self.raw_get_calls.append((tr_id, path, params))
        return self._raw_responses[tr_id]


class StubMcpClient:
    @property
    def available(self):
        return False


def make_ctx(**kwargs):
    return MILContext(kis_api=StubKisApi(**kwargs), mcp_client=StubMcpClient())


def test_get_market_context_combines_index_and_flow():
    ctx = make_ctx(
        index_status={
            "kospi": "2800.50", "kospi_change_pct": "0.75",
            "kosdaq": "850.10", "kosdaq_change_pct": "-0.30",
            "kospi_advancers": 500, "kospi_decliners": 350,
            "prev_kospi_change_pct": -8.29, "prev_kospi_trading_value": 48338891000000.0,
            "prev_kosdaq_change_pct": -9.08, "prev_kosdaq_trading_value": 8929291000000.0,
        },
        raw_responses={
            "FHPTJ04400000": {
                "output2": [
                    {"frgn_ntby_tr_pbmn": "-1000", "orgn_ntby_tr_pbmn": "500"},
                    {"frgn_ntby_tr_pbmn": "-2000", "orgn_ntby_tr_pbmn": "1500"},
                ]
            },
        },
    )
    result = get_market_context(ctx, "PREMARKET")
    assert result["kospi"] == "2800.50"
    assert result["foreign_net_buy_krw"] == -3000.0
    assert result["institution_net_buy_krw"] == 2000.0
    assert result["prev_kospi_change_pct"] == -8.29


def test_get_sector_breadth_parses_output():
    ctx = make_ctx(
        raw_responses={
            "FHPUP02140000": {
                "output": [
                    {
                        "hts_kor_isnm": "전기전자", "bstp_cls_code": "031",
                        "bstp_nmix_prdy_ctrt": "1.20",
                        "ascn_issu_cnt": "120", "down_issu_cnt": "60",
                        "stnr_issu_cnt": "10", "uplm_issu_cnt": "2", "lslm_issu_cnt": "0",
                    },
                ]
            },
        },
    )
    result = get_sector_breadth(ctx, "SCAN")
    assert result["sectors"][0]["sector_name"] == "전기전자"
    assert result["sectors"][0]["advancers"] == 120
    assert result["sectors"][0]["upper_limit"] == 2


def test_get_intraday_index_candles_parses_output2():
    ctx = make_ctx(
        raw_responses={
            "FHKUP03500200": {
                "output2": [
                    {"stck_cntg_hour": "100000", "bstp_nmix_oprc": "2800",
                     "bstp_nmix_hgpr": "2810", "bstp_nmix_lwpr": "2795",
                     "bstp_nmix_prpr": "2805", "acml_vol": "12345"},
                ]
            },
        },
    )
    result = get_intraday_index_candles(ctx, "INTRADAY", index_code="0001")
    assert result["index_code"] == "0001"
    assert result["candles"][0]["close"] == 2805.0


def test_get_news_market_parses_headlines():
    ctx = make_ctx(
        raw_responses={
            "FHKST01011800": {
                "output": [
                    {"hts_pbnt_titl_cntt": "코스피 급락", "data_dt": "20260609", "data_tm": "090500"},
                ]
            },
        },
    )
    result = get_news_market(ctx, "PREMARKET")
    assert result["headlines"][0]["title"] == "코스피 급락"


def test_get_market_context_caches_second_call():
    ctx = make_ctx(
        index_status={"kospi": "2800.50", "kospi_change_pct": "0.75",
                       "kosdaq": "850.10", "kosdaq_change_pct": "-0.30",
                       "kospi_advancers": 0, "kospi_decliners": 0,
                       "prev_kospi_change_pct": 0, "prev_kospi_trading_value": 0,
                       "prev_kosdaq_change_pct": 0, "prev_kosdaq_trading_value": 0},
        raw_responses={"FHPTJ04400000": {"output2": []}},
    )
    get_market_context(ctx, "SCAN")
    get_market_context(ctx, "SCAN")
    assert len(ctx.kis_api.raw_get_calls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_mil_market.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_intelligence.market'`

- [ ] **Step 3: Implement market_intelligence/market.py**

```python
"""시장관찰 도구 4개: get_market_context, get_sector_breadth, get_intraday_index_candles, get_news_market"""
from __future__ import annotations

from market_intelligence.base import MILContext


def get_market_context(ctx: MILContext, phase: str) -> dict:
    """코스피/코스닥 지수, 외국인/기관 순매수, 전일 확정 등락률·거래대금."""

    def fetch():
        index_status = ctx.kis_api.get_index_status()
        flow = ctx.kis_api.raw_get(
            "FHPTJ04400000",
            "domestic-stock/v1/quotations/foreign-institution-total",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "16449",
                "FID_INPUT_ISCD": "0000",
                "FID_RANK_SORT_CLS_CODE": "0",
                "FID_ETC_CLS_CODE": "0",
            },
        )
        flow_rows = flow.get("output2", [])
        foreign_net = sum(_to_float(r.get("frgn_ntby_tr_pbmn")) for r in flow_rows)
        institution_net = sum(_to_float(r.get("orgn_ntby_tr_pbmn")) for r in flow_rows)

        return {
            "kospi": index_status.get("kospi"),
            "kospi_change_pct": index_status.get("kospi_change_pct"),
            "kosdaq": index_status.get("kosdaq"),
            "kosdaq_change_pct": index_status.get("kosdaq_change_pct"),
            "kospi_advancers": index_status.get("kospi_advancers"),
            "kospi_decliners": index_status.get("kospi_decliners"),
            "foreign_net_buy_krw": foreign_net,
            "institution_net_buy_krw": institution_net,
            "prev_kospi_change_pct": index_status.get("prev_kospi_change_pct"),
            "prev_kospi_trading_value": index_status.get("prev_kospi_trading_value"),
            "prev_kosdaq_change_pct": index_status.get("prev_kosdaq_change_pct"),
            "prev_kosdaq_trading_value": index_status.get("prev_kosdaq_trading_value"),
        }

    return ctx.cached_call("get_market_context", phase, {}, fetch)


def get_sector_breadth(ctx: MILContext, phase: str) -> dict:
    """업종별 지수·등락률 + 상승/하락/보합/상한/하한 종목 수 (브레드스 통합)."""

    def fetch():
        raw = ctx.kis_api.raw_get(
            "FHPUP02140000",
            "domestic-stock/v1/quotations/inquire-index-category-price",
            {
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_COND_SCR_DIV_CODE": "20214",
                "FID_INPUT_ISCD": "0001",
                "FID_DIV_CLS_CODE": "0",
                "FID_BLNG_CLS_CODE": "0",
            },
        )
        sectors = [
            {
                "sector_name": row.get("hts_kor_isnm"),
                "sector_code": row.get("bstp_cls_code"),
                "change_pct": _to_float(row.get("bstp_nmix_prdy_ctrt")),
                "advancers": _to_int(row.get("ascn_issu_cnt")),
                "decliners": _to_int(row.get("down_issu_cnt")),
                "unchanged": _to_int(row.get("stnr_issu_cnt")),
                "upper_limit": _to_int(row.get("uplm_issu_cnt")),
                "lower_limit": _to_int(row.get("lslm_issu_cnt")),
            }
            for row in raw.get("output", [])
        ]
        return {"sectors": sectors}

    return ctx.cached_call("get_sector_breadth", phase, {}, fetch)


def get_intraday_index_candles(ctx: MILContext, phase: str, index_code: str = "0001") -> dict:
    """업종 분봉 (VWAP 기준선 파악용)."""

    def fetch():
        raw = ctx.kis_api.raw_get(
            "FHKUP03500200",
            "domestic-stock/v1/quotations/inquire-time-indexchartprice",
            {
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_INPUT_ISCD": index_code,
                "FID_INPUT_HOUR_1": "60",
                "FID_PW_DATA_INCU_YN": "Y",
            },
        )
        candles = [
            {
                "time": row.get("stck_cntg_hour"),
                "open": _to_float(row.get("bstp_nmix_oprc")),
                "high": _to_float(row.get("bstp_nmix_hgpr")),
                "low": _to_float(row.get("bstp_nmix_lwpr")),
                "close": _to_float(row.get("bstp_nmix_prpr")),
                "volume": _to_float(row.get("acml_vol")),
            }
            for row in raw.get("output2", [])
        ]
        return {"index_code": index_code, "candles": candles}

    return ctx.cached_call("get_intraday_index_candles", phase, {"index_code": index_code}, fetch)


def get_news_market(ctx: MILContext, phase: str) -> dict:
    """전체 시황/공시 제목 목록."""

    def fetch():
        raw = ctx.kis_api.raw_get(
            "FHKST01011800",
            "domestic-stock/v1/quotations/news-title",
            {
                "FID_NEWS_OFER_ENTP_CODE": "",
                "FID_COND_MRKT_CLS_CODE": "",
                "FID_INPUT_ISCD": "",
                "FID_TITL_CNTT": "",
                "FID_INPUT_DATE_1": "",
                "FID_INPUT_HOUR_1": "",
                "FID_RANK_SORT_CLS_CODE": "",
                "FID_INPUT_SRNO": "",
            },
        )
        headlines = [
            {
                "title": row.get("hts_pbnt_titl_cntt"),
                "date": row.get("data_dt"),
                "time": row.get("data_tm"),
            }
            for row in raw.get("output", [])
        ]
        return {"headlines": headlines}

    return ctx.cached_call("get_news_market", phase, {}, fetch)


def _to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _to_int(value) -> int:
    return int(_to_float(value))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_mil_market.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Users/gocho/MQK-v2
git add market_intelligence/market.py tests/test_mil_market.py
git commit -m "feat(v3): add MIL market observation tools (4)"
```

---

### Task 7: MIL 조건검색 도구 3개 — market_intelligence/screening.py

**Files:**
- Create: `market_intelligence/screening.py`
- Test: `tests/test_mil_screening.py`

- [ ] **Step 1: Write failing tests**

`tests/test_mil_screening.py`:

```python
"""market_intelligence/screening.py 테스트"""
from market_intelligence.base import MILContext
from market_intelligence.screening import psearch_title, psearch_result, get_top_movers


class StubKisApi:
    def __init__(self, raw_responses=None):
        self._raw_responses = raw_responses or {}
        self.raw_get_calls = []

    def raw_get(self, tr_id, path, params, mode=None):
        self.raw_get_calls.append((tr_id, path, params))
        return self._raw_responses[tr_id]


class StubMcpClient:
    @property
    def available(self):
        return False


def make_ctx(**kwargs):
    return MILContext(kis_api=StubKisApi(**kwargs), mcp_client=StubMcpClient())


def test_psearch_title_returns_conditions():
    ctx = make_ctx(
        raw_responses={
            "HHKST03900300": {
                "output2": [{"seq": "0", "condition_nm": "SEPA 1차 통과"}],
            },
        },
    )
    result = psearch_title(ctx, "SCAN", user_id="test_user")
    assert result["conditions"] == [{"seq": "0", "name": "SEPA 1차 통과"}]


def test_psearch_result_includes_52week_high_low():
    ctx = make_ctx(
        raw_responses={
            "HHKST03900400": {
                "output2": [
                    {
                        "code": "005930", "name": "삼성전자",
                        "price": "70000", "chgrate": "1.5",
                        "acml_vol": "1000000", "acml_tr_pbmn": "70000000000",
                        "stck_dryy_hgpr": "85000", "stck_dryy_lwpr": "60000",
                        "mrkt_total_amt": "420000000000000",
                    },
                ],
            },
        },
    )
    result = psearch_result(ctx, "SCAN", user_id="test_user", seq="0")
    candidate = result["candidates"][0]
    assert candidate["ticker"] == "005930"
    assert candidate["high_52w"] == 85000.0
    assert candidate["low_52w"] == 60000.0


def test_get_top_movers_includes_overheated_warning():
    ctx = make_ctx(
        raw_responses={
            "FHPST01710000": {
                "output": [
                    {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스",
                     "stck_prpr": "180000", "prdy_ctrt": "5.0", "acml_vol": "5000000"},
                ],
            },
        },
    )
    result = get_top_movers(ctx, "SCAN")
    assert result["movers"][0]["ticker"] == "000660"
    assert result["overheated_bias_warning"] is True


def test_psearch_result_caches_per_seq():
    ctx = make_ctx(
        raw_responses={
            "HHKST03900400": {"output2": []},
        },
    )
    psearch_result(ctx, "SCAN", user_id="test_user", seq="0")
    psearch_result(ctx, "SCAN", user_id="test_user", seq="0")
    assert len(ctx.kis_api.raw_get_calls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_mil_screening.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_intelligence.screening'`

- [ ] **Step 3: Implement market_intelligence/screening.py**

```python
"""조건검색 도구 3개: psearch_title, psearch_result, get_top_movers"""
from __future__ import annotations

from market_intelligence.base import MILContext


def psearch_title(ctx: MILContext, phase: str, user_id: str) -> dict:
    """저장된 HTS 조건검색식 목록 조회."""

    def fetch():
        raw = ctx.kis_api.raw_get(
            "HHKST03900300",
            "domestic-stock/v1/quotations/psearch-title",
            {"USER_ID": user_id},
        )
        conditions = [
            {"seq": row.get("seq"), "name": row.get("condition_nm")}
            for row in raw.get("output2", [])
        ]
        return {"conditions": conditions}

    return ctx.cached_call("psearch_title", phase, {"user_id": user_id}, fetch)


def psearch_result(ctx: MILContext, phase: str, user_id: str, seq: str) -> dict:
    """저장된 조건검색식 실행 결과. 52주 고저가/시가총액 포함."""

    def fetch():
        raw = ctx.kis_api.raw_get(
            "HHKST03900400",
            "domestic-stock/v1/quotations/psearch-result",
            {"USER_ID": user_id, "SEQ": seq},
        )
        candidates = [
            {
                "ticker": row.get("code"),
                "name": row.get("name"),
                "price": _to_float(row.get("price")),
                "change_pct": _to_float(row.get("chgrate")),
                "volume": _to_float(row.get("acml_vol")),
                "trading_value": _to_float(row.get("acml_tr_pbmn")),
                "high_52w": _to_float(row.get("stck_dryy_hgpr")),
                "low_52w": _to_float(row.get("stck_dryy_lwpr")),
                "market_cap": _to_float(row.get("mrkt_total_amt")),
            }
            for row in raw.get("output2", [])
        ]
        return {"seq": seq, "candidates": candidates}

    return ctx.cached_call("psearch_result", phase, {"user_id": user_id, "seq": seq}, fetch)


def get_top_movers(ctx: MILContext, phase: str) -> dict:
    """psearch 실패 시 백업: 거래량순위. 과열주 편향 경고 플래그 포함."""

    def fetch():
        raw = ctx.kis_api.raw_get(
            "FHPST01710000",
            "domestic-stock/v1/quotations/volume-rank",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20171",
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "0",
                "FID_BLNG_CLS_CODE": "0",
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "0000000000",
                "FID_INPUT_PRICE_1": "",
                "FID_INPUT_PRICE_2": "",
                "FID_VOL_CNT": "",
                "FID_INPUT_DATE_1": "",
            },
        )
        movers = [
            {
                "ticker": row.get("mksc_shrn_iscd"),
                "name": row.get("hts_kor_isnm"),
                "price": _to_float(row.get("stck_prpr")),
                "change_pct": _to_float(row.get("prdy_ctrt")),
                "volume": _to_float(row.get("acml_vol")),
            }
            for row in raw.get("output", [])
        ]
        return {
            "movers": movers,
            "overheated_bias_warning": True,
            "warning_reason": "psearch 실패로 거래량순위 백업 사용 — 단기 과열주 비중이 높을 수 있음",
        }

    return ctx.cached_call("get_top_movers", phase, {}, fetch)


def _to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_mil_screening.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Users/gocho/MQK-v2
git add market_intelligence/screening.py tests/test_mil_screening.py
git commit -m "feat(v3): add MIL screening tools (3)"
```

---

### Task 8: MIL 종목분석 도구 5개 — market_intelligence/stock.py

**Files:**
- Create: `market_intelligence/stock.py`
- Test: `tests/test_mil_stock.py`

- [ ] **Step 1: Write failing tests**

`tests/test_mil_stock.py`:

```python
"""market_intelligence/stock.py 테스트"""
from market_intelligence.base import MILContext
from market_intelligence.stock import (
    get_ohlcv,
    get_realtime_price,
    get_intraday_candles,
    get_flow,
    get_news_stock,
)


class StubKisApi:
    def __init__(self, raw_responses=None):
        self._raw_responses = raw_responses or {}
        self.raw_get_calls = []

    def raw_get(self, tr_id, path, params, mode=None):
        self.raw_get_calls.append((tr_id, path, params, mode))
        return self._raw_responses[tr_id]


class StubMcpClient:
    @property
    def available(self):
        return False


def make_ctx(**kwargs):
    return MILContext(kis_api=StubKisApi(**kwargs), mcp_client=StubMcpClient())


def test_get_ohlcv_returns_output1_valuation_and_output2_candles():
    ctx = make_ctx(
        raw_responses={
            "FHKST03010100": {
                "output1": {
                    "stck_prpr": "70000", "askp": "70100", "bidp": "69900",
                    "per": "12.5", "eps": "5600", "pbr": "1.2",
                    "hts_avls": "420000000000000",
                    "stck_mxpr": "91000", "stck_llam": "49000",
                },
                "output2": [
                    {"stck_bsop_date": "20260609", "stck_oprc": "69500", "stck_hgpr": "70200",
                     "stck_lwpr": "69300", "stck_clpr": "70000", "acml_vol": "1000000",
                     "acml_tr_pbmn": "70000000000", "flng_cls_code": "00"},
                ],
            },
        },
    )
    result = get_ohlcv(ctx, "SCAN", ticker="005930", period=60)
    assert result["current_price"] == 70000.0
    assert result["per"] == 12.5
    assert result["candles"][0]["close"] == 70000.0
    assert result["candles"][0]["rights_event_code"] == "00"


def test_get_realtime_price_batches_tickers():
    ctx = make_ctx(
        raw_responses={
            "FHKST11300006": {
                "output": [
                    {"inter_shrn_iscd": "005930", "inter2_prpr": "70000", "prdy_ctrt": "1.0", "acml_vol": "100"},
                    {"inter_shrn_iscd": "000660", "inter2_prpr": "180000", "prdy_ctrt": "5.0", "acml_vol": "200"},
                ],
            },
        },
    )
    result = get_realtime_price(ctx, "INTRADAY", tickers=["005930", "000660"])
    assert result["prices"][0]["ticker"] == "005930"
    assert result["prices"][1]["price"] == 180000.0


def test_get_realtime_price_rejects_more_than_30_tickers():
    import pytest
    from market_intelligence.base import ToolFailure

    ctx = make_ctx(raw_responses={})
    with pytest.raises(ToolFailure):
        get_realtime_price(ctx, "INTRADAY", tickers=[f"{i:06d}" for i in range(31)])


def test_get_intraday_candles_parses_minute_bars():
    ctx = make_ctx(
        raw_responses={
            "FHKST03010200": {
                "output2": [
                    {"stck_cntg_hour": "093000", "stck_oprc": "69800", "stck_hgpr": "70000",
                     "stck_lwpr": "69700", "stck_prpr": "69900", "cntg_vol": "5000"},
                ],
            },
        },
    )
    result = get_intraday_candles(ctx, "INTRADAY", ticker="005930")
    assert result["candles"][0]["close"] == 69900.0


def test_get_flow_parses_investor_breakdown():
    ctx = make_ctx(
        raw_responses={
            "FHPTJ04160001": {
                "output": [
                    {"stck_bsop_date": "20260609", "stck_clpr": "70000",
                     "frgn_ntby_qty": "-10000", "orgn_ntby_qty": "5000",
                     "prsn_ntby_qty": "5000", "invtrt_ntby_qty": "1000",
                     "prvt_fund_ntby_qty": "500", "bank_ntby_qty": "100",
                     "insu_ntby_qty": "200", "pe_fund_ntby_qty": "300"},
                ],
            },
        },
    )
    result = get_flow(ctx, "SCAN", ticker="005930")
    assert result["days"][0]["foreign_net_qty"] == -10000.0
    assert result["days"][0]["institution_net_qty"] == 5000.0


def test_get_news_stock_filters_by_ticker():
    ctx = make_ctx(
        raw_responses={
            "FHKST01011800": {
                "output": [
                    {"hts_pbnt_titl_cntt": "삼성전자 신규 수주", "data_dt": "20260609", "data_tm": "100000"},
                ],
            },
        },
    )
    result = get_news_stock(ctx, "SCAN", ticker="005930")
    assert result["headlines"][0]["title"] == "삼성전자 신규 수주"


def test_get_ohlcv_caches_per_ticker_and_period():
    ctx = make_ctx(
        raw_responses={
            "FHKST03010100": {"output1": {}, "output2": []},
        },
    )
    get_ohlcv(ctx, "SCAN", ticker="005930", period=60)
    get_ohlcv(ctx, "SCAN", ticker="005930", period=60)
    assert len(ctx.kis_api.raw_get_calls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_mil_stock.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_intelligence.stock'`

- [ ] **Step 3: Implement market_intelligence/stock.py**

```python
"""종목분석 도구 5개: get_ohlcv, get_realtime_price, get_intraday_candles, get_flow, get_news_stock

get_snapshot은 제거되었다 — get_ohlcv의 output1이 현재가+호가+밸류에이션을 포함한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from market_intelligence.base import MILContext, ToolFailure


def get_ohlcv(ctx: MILContext, phase: str, ticker: str, period: int = 60) -> dict:
    """국내주식기간별시세. output1=현재가/호가/밸류에이션, output2=OHLCV+권리락코드."""

    def fetch():
        end = datetime.now()
        start = end - timedelta(days=max(period * 3, 30))
        raw = ctx.kis_api.raw_get(
            "FHKST03010100",
            "domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            },
        )
        out1 = raw.get("output1", {})
        candles = [
            {
                "date": row.get("stck_bsop_date"),
                "open": _to_float(row.get("stck_oprc")),
                "high": _to_float(row.get("stck_hgpr")),
                "low": _to_float(row.get("stck_lwpr")),
                "close": _to_float(row.get("stck_clpr")),
                "volume": _to_float(row.get("acml_vol")),
                "trading_value": _to_float(row.get("acml_tr_pbmn")),
                "rights_event_code": row.get("flng_cls_code"),
            }
            for row in raw.get("output2", [])[:period]
        ]
        return {
            "ticker": ticker,
            "current_price": _to_float(out1.get("stck_prpr")),
            "ask_price": _to_float(out1.get("askp")),
            "bid_price": _to_float(out1.get("bidp")),
            "per": _to_float(out1.get("per")),
            "eps": _to_float(out1.get("eps")),
            "pbr": _to_float(out1.get("pbr")),
            "market_cap": _to_float(out1.get("hts_avls")),
            "upper_limit": _to_float(out1.get("stck_mxpr")),
            "lower_limit": _to_float(out1.get("stck_llam")),
            "candles": candles,
        }

    return ctx.cached_call("get_ohlcv", phase, {"ticker": ticker, "period": period}, fetch)


def get_realtime_price(ctx: MILContext, phase: str, tickers: list[str]) -> dict:
    """관심종목(멀티종목) 시세조회. 최대 30종목 배치. 모의투자 미지원 (mode=real 고정)."""

    def fetch():
        if len(tickers) > 30:
            raise ValueError("최대 30종목까지만 조회 가능")
        params = {"FID_COND_MRKT_DIV_CODE_1": "J"}
        for i, ticker in enumerate(tickers, start=1):
            params[f"FID_INPUT_ISCD_{i}"] = ticker
            params[f"FID_COND_MRKT_DIV_CODE_{i}"] = "J"
        raw = ctx.kis_api.raw_get(
            "FHKST11300006",
            "domestic-stock/v1/quotations/intstock-multprice",
            params,
            mode="real",
        )
        prices = [
            {
                "ticker": row.get("inter_shrn_iscd"),
                "price": _to_float(row.get("inter2_prpr")),
                "change_pct": _to_float(row.get("prdy_ctrt")),
                "volume": _to_float(row.get("acml_vol")),
            }
            for row in raw.get("output", [])
        ]
        return {"prices": prices}

    return ctx.cached_call("get_realtime_price", phase, {"tickers": tickers}, fetch)


def get_intraday_candles(ctx: MILContext, phase: str, ticker: str) -> dict:
    """주식당일분봉조회."""

    def fetch():
        raw = ctx.kis_api.raw_get(
            "FHKST03010200",
            "domestic-stock/v1/quotations/inquire-time-itemchartprice",
            {
                "FID_ETC_CLS_CODE": "",
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                "FID_INPUT_HOUR_1": "60",
                "FID_PW_DATA_INCU_YN": "Y",
            },
        )
        candles = [
            {
                "time": row.get("stck_cntg_hour"),
                "open": _to_float(row.get("stck_oprc")),
                "high": _to_float(row.get("stck_hgpr")),
                "low": _to_float(row.get("stck_lwpr")),
                "close": _to_float(row.get("stck_prpr")),
                "volume": _to_float(row.get("cntg_vol")),
            }
            for row in raw.get("output2", [])
        ]
        return {"ticker": ticker, "candles": candles}

    return ctx.cached_call("get_intraday_candles", phase, {"ticker": ticker}, fetch)


def get_flow(ctx: MILContext, phase: str, ticker: str) -> dict:
    """종목별 투자자매매동향(일별) - 외국인/기관/개인/투신/사모/은행/보험/기금 순매수 수량."""

    def fetch():
        raw = ctx.kis_api.raw_get(
            "FHPTJ04160001",
            "domestic-stock/v1/quotations/inquire-investor-time-by-stock",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
            },
        )
        days = [
            {
                "date": row.get("stck_bsop_date"),
                "close": _to_float(row.get("stck_clpr")),
                "foreign_net_qty": _to_float(row.get("frgn_ntby_qty")),
                "institution_net_qty": _to_float(row.get("orgn_ntby_qty")),
                "individual_net_qty": _to_float(row.get("prsn_ntby_qty")),
                "trust_net_qty": _to_float(row.get("invtrt_ntby_qty")),
                "private_fund_net_qty": _to_float(row.get("prvt_fund_ntby_qty")),
                "bank_net_qty": _to_float(row.get("bank_ntby_qty")),
                "insurance_net_qty": _to_float(row.get("insu_ntby_qty")),
                "pension_net_qty": _to_float(row.get("pe_fund_ntby_qty")),
            }
            for row in raw.get("output", [])
        ]
        return {"ticker": ticker, "days": days}

    return ctx.cached_call("get_flow", phase, {"ticker": ticker}, fetch)


def get_news_stock(ctx: MILContext, phase: str, ticker: str) -> dict:
    """ticker 필터 뉴스/공시 제목."""

    def fetch():
        raw = ctx.kis_api.raw_get(
            "FHKST01011800",
            "domestic-stock/v1/quotations/news-title",
            {
                "FID_NEWS_OFER_ENTP_CODE": "",
                "FID_COND_MRKT_CLS_CODE": "",
                "FID_INPUT_ISCD": ticker,
                "FID_TITL_CNTT": "",
                "FID_INPUT_DATE_1": "",
                "FID_INPUT_HOUR_1": "",
                "FID_RANK_SORT_CLS_CODE": "",
                "FID_INPUT_SRNO": "",
            },
        )
        headlines = [
            {
                "title": row.get("hts_pbnt_titl_cntt"),
                "date": row.get("data_dt"),
                "time": row.get("data_tm"),
            }
            for row in raw.get("output", [])
        ]
        return {"ticker": ticker, "headlines": headlines}

    return ctx.cached_call("get_news_stock", phase, {"ticker": ticker}, fetch)


def _to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_mil_stock.py -v`
Expected: All PASS

> 참고: `test_get_realtime_price_rejects_more_than_30_tickers`는 `fetch()` 내부에서 `raise ValueError`가 발생하고
> `MILContext.cached_call`이 이를 `ToolFailure`로 감싸서 다시 raise하는 것을 검증한다.

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Users/gocho/MQK-v2
git add market_intelligence/stock.py tests/test_mil_stock.py
git commit -m "feat(v3): add MIL stock analysis tools (5)"
```

---

### Task 9: MIL 리스크필터 도구 2개 — market_intelligence/risk_filter.py

**Files:**
- Create: `market_intelligence/risk_filter.py`
- Test: `tests/test_mil_risk_filter.py`

- [ ] **Step 1: Write failing tests**

`tests/test_mil_risk_filter.py`:

```python
"""market_intelligence/risk_filter.py 테스트"""
from market_intelligence.base import MILContext
from market_intelligence.risk_filter import get_stock_status, get_event_schedule


class StubKisApi:
    def __init__(self, raw_responses=None, stock_info=None):
        self._raw_responses = raw_responses or {}
        self._stock_info = stock_info or {}
        self.raw_get_calls = []

    def raw_get(self, tr_id, path, params, mode=None):
        self.raw_get_calls.append((tr_id, path, params))
        return self._raw_responses[tr_id]

    def get_stock_info(self, ticker):
        return self._stock_info


class StubMcpClient:
    @property
    def available(self):
        return False


def make_ctx(**kwargs):
    return MILContext(kis_api=StubKisApi(**kwargs), mcp_client=StubMcpClient())


def test_get_stock_status_detects_vi_triggered():
    ctx = make_ctx(
        raw_responses={
            "FHPST01390000": {"output": [{"mksc_shrn_iscd": "005930"}]},
            "FHPST04830000": {"output": [{"shnu_rate": "3.5"}]},
        },
        stock_info={"trading_halted": False, "administrative_issue": False},
    )
    result = get_stock_status(ctx, "SCAN", ticker="005930")
    assert result["vi_triggered"] is True
    assert result["short_sale_ratio_pct"] == 3.5
    assert result["trading_halted"] is False


def test_get_stock_status_no_vi_for_other_ticker():
    ctx = make_ctx(
        raw_responses={
            "FHPST01390000": {"output": [{"mksc_shrn_iscd": "000660"}]},
            "FHPST04830000": {"output": []},
        },
        stock_info={"trading_halted": True, "administrative_issue": False},
    )
    result = get_stock_status(ctx, "SCAN", ticker="005930")
    assert result["vi_triggered"] is False
    assert result["short_sale_ratio_pct"] == 0.0
    assert result["trading_halted"] is True


def test_get_event_schedule_parses_rights_and_dividend():
    ctx = make_ctx(
        raw_responses={
            "HHKDB669100C0": {
                "output1": [{"record_date": "20260620", "sbscr_strt_dt": "20260701", "sbscr_end_dt": "20260702"}],
            },
            "HHKDB669102C0": {
                "output1": [{"record_date": "20260630", "per_sto_divi_amt": "350"}],
            },
        },
    )
    result = get_event_schedule(ctx, "PREMARKET", ticker="005930")
    assert result["rights_events"][0]["record_date"] == "20260620"
    assert result["dividend_events"][0]["dividend_amount"] == 350.0


def test_get_stock_status_caches_per_ticker():
    ctx = make_ctx(
        raw_responses={
            "FHPST01390000": {"output": []},
            "FHPST04830000": {"output": []},
        },
        stock_info={},
    )
    get_stock_status(ctx, "SCAN", ticker="005930")
    get_stock_status(ctx, "SCAN", ticker="005930")
    assert len(ctx.kis_api.raw_get_calls) == 2  # VI + 공매도, 캐시되어 두 번째 호출은 추가 없음
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_mil_risk_filter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_intelligence.risk_filter'`

- [ ] **Step 3: Implement market_intelligence/risk_filter.py**

```python
"""리스크필터 도구 2개: get_stock_status, get_event_schedule"""
from __future__ import annotations

from market_intelligence.base import MILContext


def get_stock_status(ctx: MILContext, phase: str, ticker: str) -> dict:
    """VI 발동 여부, 관리종목/거래정지 여부, 공매도 비중."""

    def fetch():
        vi = ctx.kis_api.raw_get(
            "FHPST01390000",
            "domestic-stock/v1/quotations/inquire-vi-status",
            {
                "FID_DIV_CLS_CODE": "0",
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                "FID_RANK_SORT_CLS_CODE": "0",
                "FID_INPUT_DATE_1": "",
            },
        )
        vi_triggered = any(
            row.get("mksc_shrn_iscd") == ticker for row in vi.get("output", [])
        )

        info = ctx.kis_api.get_stock_info(ticker)

        short = ctx.kis_api.raw_get(
            "FHPST04830000",
            "domestic-stock/v1/quotations/daily-short-sale",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                "FID_INPUT_DATE_1": "",
                "FID_INPUT_DATE_2": "",
                "FID_PERIOD_DIV_CODE": "D",
            },
        )
        short_rows = short.get("output", [])
        short_ratio = _to_float(short_rows[0].get("shnu_rate")) if short_rows else 0.0

        return {
            "ticker": ticker,
            "vi_triggered": vi_triggered,
            "trading_halted": info.get("trading_halted", False),
            "administrative_issue": info.get("administrative_issue", False),
            "short_sale_ratio_pct": short_ratio,
        }

    return ctx.cached_call("get_stock_status", phase, {"ticker": ticker}, fetch)


def get_event_schedule(ctx: MILContext, phase: str, ticker: str) -> dict:
    """권리락일/유상증자 청약기간 + 배당기준일/배당금."""

    def fetch():
        rights = ctx.kis_api.raw_get(
            "HHKDB669100C0",
            "domestic-stock/v1/ksdinfo/paidin-capital-increase",
            {"CTS": "", "GB1": "1", "F_DT": "", "T_DT": "", "SHT_CD": ticker},
        )
        dividend = ctx.kis_api.raw_get(
            "HHKDB669102C0",
            "domestic-stock/v1/ksdinfo/dividend",
            {"CTS": "", "GB1": "0", "F_DT": "", "T_DT": "", "SHT_CD": ticker, "HIGH_GB": ""},
        )
        rights_events = [
            {
                "record_date": row.get("record_date"),
                "subscription_start": row.get("sbscr_strt_dt"),
                "subscription_end": row.get("sbscr_end_dt"),
            }
            for row in rights.get("output1", [])
        ]
        dividend_events = [
            {
                "record_date": row.get("record_date"),
                "dividend_amount": _to_float(row.get("per_sto_divi_amt")),
            }
            for row in dividend.get("output1", [])
        ]
        return {"ticker": ticker, "rights_events": rights_events, "dividend_events": dividend_events}

    return ctx.cached_call("get_event_schedule", phase, {"ticker": ticker}, fetch)


def _to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_mil_risk_filter.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Users/gocho/MQK-v2
git add market_intelligence/risk_filter.py tests/test_mil_risk_filter.py
git commit -m "feat(v3): add MIL risk filter tools (2)"
```

---

### Task 10: MIL 포트폴리오 도구 2개 — market_intelligence/portfolio.py

**Files:**
- Create: `market_intelligence/portfolio.py`
- Test: `tests/test_mil_portfolio.py`

- [ ] **Step 1: Write failing tests**

`tests/test_mil_portfolio.py`:

```python
"""market_intelligence/portfolio.py 테스트"""
from market_intelligence.base import MILContext
from market_intelligence.portfolio import get_open_positions, get_daily_pnl


class StubKisApi:
    def __init__(self, balance=None):
        self._balance = balance or {}

    def get_balance(self):
        return self._balance


class StubMcpClient:
    @property
    def available(self):
        return False


def make_ctx(**kwargs):
    return MILContext(kis_api=StubKisApi(**kwargs), mcp_client=StubMcpClient())


def test_get_open_positions_filters_zero_quantity():
    ctx = make_ctx(
        balance={
            "output1": [
                {"pdno": "005930", "prdt_name": "삼성전자", "hldg_qty": "10",
                 "pchs_avg_pric": "70000", "prpr": "71000",
                 "evlu_pfls_amt": "10000", "evlu_pfls_rt": "1.43"},
                {"pdno": "000660", "prdt_name": "SK하이닉스", "hldg_qty": "0",
                 "pchs_avg_pric": "0", "prpr": "0", "evlu_pfls_amt": "0", "evlu_pfls_rt": "0"},
            ],
            "output2": [],
        },
    )
    result = get_open_positions(ctx, "INTRADAY")
    assert result["position_count"] == 1
    assert result["positions"][0]["ticker"] == "005930"
    assert result["positions"][0]["quantity"] == 10


def test_get_daily_pnl_computes_realized_pct():
    ctx = make_ctx(
        balance={
            "output1": [],
            "output2": [{"tot_evlu_amt": "10000000", "rlzt_pfls": "-30000"}],
        },
    )
    result = get_daily_pnl(ctx, "INTRADAY")
    assert result["realized_pnl_krw"] == -30000.0
    assert result["realized_pnl_pct"] == -0.3


def test_get_daily_pnl_handles_empty_output2():
    ctx = make_ctx(balance={"output1": [], "output2": []})
    result = get_daily_pnl(ctx, "INTRADAY")
    assert result["realized_pnl_krw"] == 0.0
    assert result["realized_pnl_pct"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_mil_portfolio.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_intelligence.portfolio'`

- [ ] **Step 3: Implement market_intelligence/portfolio.py**

```python
"""포트폴리오 도구 2개: get_open_positions, get_daily_pnl"""
from __future__ import annotations

from market_intelligence.base import MILContext


def get_open_positions(ctx: MILContext, phase: str) -> dict:
    """보유 종목, 수량, 평균단가, 평가손익."""

    def fetch():
        raw = ctx.kis_api.get_balance()
        positions = []
        for row in raw.get("output1", []):
            qty = _to_float(row.get("hldg_qty"))
            if qty <= 0:
                continue
            positions.append({
                "ticker": row.get("pdno"),
                "name": row.get("prdt_name"),
                "quantity": int(qty),
                "avg_price": _to_float(row.get("pchs_avg_pric")),
                "current_price": _to_float(row.get("prpr")),
                "eval_pnl": _to_float(row.get("evlu_pfls_amt")),
                "eval_pnl_pct": _to_float(row.get("evlu_pfls_rt")),
            })
        return {"positions": positions, "position_count": len(positions)}

    return ctx.cached_call("get_open_positions", phase, {}, fetch)


def get_daily_pnl(ctx: MILContext, phase: str) -> dict:
    """당일 실현손익 (금액 + 총평가금액 대비 %)."""

    def fetch():
        raw = ctx.kis_api.get_balance()
        summary_rows = raw.get("output2", [])
        summary = summary_rows[0] if summary_rows else {}
        total_eval = _to_float(summary.get("tot_evlu_amt"))
        realized_pnl = _to_float(summary.get("rlzt_pfls"))
        realized_pct = round(realized_pnl / total_eval * 100, 2) if total_eval else 0.0
        return {
            "realized_pnl_krw": realized_pnl,
            "realized_pnl_pct": realized_pct,
            "total_eval_amt": total_eval,
        }

    return ctx.cached_call("get_daily_pnl", phase, {}, fetch)


def _to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_mil_portfolio.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Users/gocho/MQK-v2
git add market_intelligence/portfolio.py tests/test_mil_portfolio.py
git commit -m "feat(v3): add MIL portfolio tools (2)"
```

---

### Task 11: RegimeAgent 확장 — risk_guidance + drift_triggers + last_regime.json

**Files:**
- Modify: `agents/regime_agent.py`
- Modify: `prompts/agents/regime_agent.md`
- Test: `tests/test_regime_agent.py` (신규)

- [ ] **Step 1: Write failing tests**

`tests/test_regime_agent.py`:

```python
"""RegimeAgent v3 확장 테스트 - risk_guidance/drift_triggers 출력 + last_regime 캐시"""
from agents.regime_agent import (
    RegimeAgent,
    RegimeJudgment,
    MarketStatus,
    Regime,
    save_last_regime,
    load_last_regime,
)


class FakeLLMClient:
    def __init__(self, response):
        self._response = response

    def call(self, system, user, tier=None, expect_json=True):
        return self._response


def test_judge_extracts_risk_guidance_and_drift_triggers():
    raw = {
        "status": "YELLOW", "regime": "SIDEWAYS", "confidence": 44,
        "reason": "혼조세",
        "risk_guidance": {
            "buy_confidence_threshold": 75, "risk_per_trade_pct": 0.35,
            "max_positions": 4, "min_trading_value_krw": 10_000_000_000,
        },
        "drift_triggers": [
            {"id": "index_sharp_drop", "metric": "kospi_drop_from_open_pct",
             "threshold": -1.5, "direction": "below"},
        ],
        "cooldown_minutes": 60,
        "max_daily_triggers": 3,
    }
    agent = RegimeAgent(llm=FakeLLMClient(raw))
    judgment = agent.judge({})

    assert judgment.risk_guidance["buy_confidence_threshold"] == 75
    assert judgment.drift_triggers[0]["id"] == "index_sharp_drop"
    assert judgment.cooldown_minutes == 60
    assert judgment.max_daily_triggers == 3


def test_judge_clamps_extreme_risk_guidance_via_safety_bounds():
    raw = {
        "status": "RED", "regime": "RISK_OFF", "confidence": 90, "reason": "급락",
        "risk_guidance": {
            "buy_confidence_threshold": 30,   # 너무 낮음 → 65로 클램프
            "risk_per_trade_pct": 5.0,        # 너무 큼 → 0.50로 클램프
            "max_positions": 99,              # 너무 큼 → 5로 클램프
            "min_trading_value_krw": 1,       # 너무 작음 → 50억으로 클램프
        },
        "drift_triggers": [],
    }
    agent = RegimeAgent(llm=FakeLLMClient(raw))
    judgment = agent.judge({})

    assert judgment.risk_guidance["buy_confidence_threshold"] == 65.0
    assert judgment.risk_guidance["risk_per_trade_pct"] == 0.50
    assert judgment.risk_guidance["max_positions"] == 5
    assert judgment.risk_guidance["min_trading_value_krw"] == 5_000_000_000


def test_judge_defaults_when_v3_fields_missing():
    raw = {"status": "GREEN", "regime": "UPTREND", "confidence": 70, "reason": "강세"}
    agent = RegimeAgent(llm=FakeLLMClient(raw))
    judgment = agent.judge({})

    assert judgment.drift_triggers == []
    assert judgment.cooldown_minutes == 60
    assert judgment.max_daily_triggers == 3
    # risk_guidance 누락 시 RegimeSafetyBounds의 최소값으로 채워진다 (가장 보수적)
    assert judgment.risk_guidance["max_positions"] == 1


def test_save_and_load_last_regime(tmp_path):
    raw = {
        "status": "YELLOW", "regime": "SIDEWAYS", "confidence": 44, "reason": "혼조세",
        "risk_guidance": {
            "buy_confidence_threshold": 75, "risk_per_trade_pct": 0.35,
            "max_positions": 4, "min_trading_value_krw": 10_000_000_000,
        },
        "drift_triggers": [
            {"id": "recovery_signal", "metric": "kospi_recovery_from_low_pct",
             "threshold": 1.0, "direction": "above"},
        ],
    }
    agent = RegimeAgent(llm=FakeLLMClient(raw))
    judgment = agent.judge({})

    path = tmp_path / "last_regime.json"
    save_last_regime(judgment, path=path)
    loaded = load_last_regime(path=path)

    assert loaded["status"] == "YELLOW"
    assert loaded["regime"] == "SIDEWAYS"
    assert loaded["drift_triggers"][0]["id"] == "recovery_signal"
    assert loaded["risk_guidance"]["max_positions"] == 4
    assert "timestamp" in loaded


def test_load_last_regime_returns_none_if_missing(tmp_path):
    assert load_last_regime(path=tmp_path / "missing.json") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_regime_agent.py -v`
Expected: FAIL with `TypeError: RegimeJudgment.__init__() got an unexpected keyword argument` or `ImportError: cannot import name 'save_last_regime'`

- [ ] **Step 3: Extend RegimeJudgment + judge() + add save/load_last_regime**

`agents/regime_agent.py` 상단 import 수정:

```python
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from codes.risk_officer import clamp_risk_guidance
from config.settings import ModelTier
from llm.client import LLMClient
from llm.soul import inject_agent

_SYSTEM_PROMPT = inject_agent("regime_agent")
_LAST_REGIME_PATH = Path(__file__).parent.parent / "data" / "last_regime.json"
```

`RegimeJudgment` dataclass에 v3 필드 추가:

```python
@dataclass
class RegimeJudgment:
    status: MarketStatus
    regime: Regime
    confidence: int
    reason: str
    risk_notes: list[str] = field(default_factory=list)
    opportunity_mode: OpportunityMode = OpportunityMode.NORMAL
    scanner_mode: ScannerMode = ScannerMode.TREND
    # v3 확장 필드
    risk_guidance: dict = field(default_factory=dict)
    drift_triggers: list[dict] = field(default_factory=list)
    cooldown_minutes: int = 60
    max_daily_triggers: int = 3
```

`judge()` 메서드의 `return RegimeJudgment(...)` 부분을 다음으로 교체:

```python
        return RegimeJudgment(
            status=MarketStatus(raw["status"]),
            regime=Regime(raw["regime"]),
            confidence=int(raw["confidence"]),
            reason=raw["reason"],
            risk_notes=raw.get("risk_notes", []),
            opportunity_mode=OpportunityMode(raw.get("opportunity_mode", "NORMAL")),
            scanner_mode=ScannerMode(raw.get("scanner_mode", "TREND")),
            risk_guidance=clamp_risk_guidance(raw.get("risk_guidance", {})),
            drift_triggers=raw.get("drift_triggers", []),
            cooldown_minutes=int(raw.get("cooldown_minutes", 60)),
            max_daily_triggers=int(raw.get("max_daily_triggers", 3)),
        )
```

파일 끝에 모듈 레벨 함수 추가:

```python
def save_last_regime(judgment: RegimeJudgment, path: Path = _LAST_REGIME_PATH) -> None:
    """PREMARKET 판단 결과를 data/last_regime.json에 저장한다.

    RegimeDriftDetector와 LLM SPOF 폴백(24시간 캐시)이 이 파일을 사용한다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(judgment)
    payload["status"] = judgment.status.value
    payload["regime"] = judgment.regime.value
    payload["opportunity_mode"] = judgment.opportunity_mode.value
    payload["scanner_mode"] = judgment.scanner_mode.value
    payload["timestamp"] = datetime.now().isoformat()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_last_regime(path: Path = _LAST_REGIME_PATH) -> dict | None:
    """캐시된 레짐 판단 로드. 파일이 없으면 None."""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_regime_agent.py -v`
Expected: All PASS

- [ ] **Step 5: Update prompts/agents/regime_agent.md with v3 output schema**

`prompts/agents/regime_agent.md`의 `## Output JSON` 섹션을 다음으로 교체 (그 앞의 `## Special Rule`까지는 그대로 유지):

```markdown
## Output JSON
```json
{
  "status": "GREEN|YELLOW|RED",
  "regime": "UPTREND|DOWNTREND|SIDEWAYS|THEME_MARKET|POLICY_MARKET|EARNINGS_MARKET|RISK_OFF",
  "confidence": 0,
  "reason": "",
  "risk_notes": [],
  "opportunity_mode": "NORMAL|SETUP4_PANIC",
  "scanner_mode": "TREND|REVERSAL_ONLY",

  "risk_guidance": {
    "buy_confidence_threshold": 75,
    "risk_per_trade_pct": 0.35,
    "max_positions": 4,
    "min_trading_value_krw": 10000000000
  },

  "drift_triggers": [
    {
      "id": "index_sharp_drop",
      "metric": "kospi_drop_from_open_pct",
      "threshold": -1.5,
      "direction": "below",
      "description": "KOSPI 시가 대비 하락 시 RED 전환 가능성"
    },
    {
      "id": "recovery_signal",
      "metric": "kospi_recovery_from_low_pct",
      "threshold": 1.0,
      "direction": "above",
      "description": "장중 저점 대비 회복 시 GREEN 재검토"
    }
  ],
  "cooldown_minutes": 60,
  "max_daily_triggers": 3
}
```

## risk_guidance 가이드
- `buy_confidence_threshold`: 65~95 사이. RED일수록 높게 (강한 증거만 통과).
- `risk_per_trade_pct`: 0.10~0.50 사이. RED일수록 작게 (포지션 사이즈 축소).
- `max_positions`: 1~5 사이. RED일수록 작게.
- `min_trading_value_krw`: 최소 50억. RED일수록 크게 (유동성 높은 종목만).
- 위 값은 코드(`clamp_risk_guidance`)가 강제로 클램핑하므로, 범위를 벗어난 값을 선언해도 안전하게 처리된다.
  단, 의도를 명확히 전달하려면 범위 내 값으로 선언하는 것이 좋다.

## drift_triggers 가이드
- 오늘 아침 판단의 "재검토 조건"을 스스로 선언한다.
- 최소 1개는 악화 방향(`index_sharp_drop`, `foreign_heavy_sell`, `breadth_collapse` 등),
  최소 1개는 회복 방향(`recovery_signal`)을 포함하는 것을 권장한다.
  (RED 판단을 내려도 오후 회복 종목을 포착할 수 있어야 한다.)
- `metric`은 RegimeDriftDetector가 5분마다 무료로 계산하는 다음 중에서 선택한다:
  - `kospi_drop_from_open_pct`: (현재가-시가)/시가 × 100
  - `kospi_recovery_from_low_pct`: (현재가-장중저가)/장중저가 × 100
  - `foreign_net_sell_cumulative_bln`: 외국인 누적 순매도 대금 (억원, 양수=순매도)
  - `advance_decline_ratio`: 상승종목수 / (상승종목수+하락종목수)
```

- [ ] **Step 6: Run full test suite to confirm no regressions**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/ -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
cd /mnt/c/Users/gocho/MQK-v2
git add agents/regime_agent.py prompts/agents/regime_agent.md tests/test_regime_agent.py
git commit -m "feat(v3): extend RegimeAgent with risk_guidance, drift_triggers, last_regime cache"
```

---

### Task 12: RegimeDriftDetector — 3-티어 드리프트 감지 + Lite LLM

**Files:**
- Create: `agents/drift_detector.py`
- Create: `prompts/agents/drift_detector.md`
- Test: `tests/test_drift_detector.py` (신규)

- [ ] **Step 1: Write failing tests**

`tests/test_drift_detector.py`:

```python
"""RegimeDriftDetector 테스트 - Tier2(코드 감시) + Tier3(Lite LLM) + CAUTION 카운터"""
from agents.drift_detector import (
    RegimeDriftDetector,
    compute_metrics,
    evaluate_triggers,
)


DRIFT_TRIGGERS = [
    {"id": "index_sharp_drop", "metric": "kospi_drop_from_open_pct",
     "threshold": -1.5, "direction": "below", "description": "급락"},
    {"id": "foreign_heavy_sell", "metric": "foreign_net_sell_cumulative_bln",
     "threshold": 4000, "direction": "above", "description": "외인 매도"},
    {"id": "breadth_collapse", "metric": "advance_decline_ratio",
     "threshold": 0.25, "direction": "below", "description": "쏠림"},
    {"id": "recovery_signal", "metric": "kospi_recovery_from_low_pct",
     "threshold": 1.0, "direction": "above", "description": "회복"},
]


class FakeLiteLLM:
    def __init__(self, response):
        self._response = response
        self.calls = 0

    def call(self, system, user, tier=None, expect_json=True):
        self.calls += 1
        return self._response


def test_compute_metrics():
    snapshot = {
        "kospi_current": 2480.0,
        "kospi_open": 2530.0,
        "kospi_low": 2470.0,
        "foreign_net_buy_bln": -4500,
        "advance_count": 150,
        "decline_count": 700,
    }
    metrics = compute_metrics(snapshot)

    assert round(metrics["kospi_drop_from_open_pct"], 2) == round((2480.0 - 2530.0) / 2530.0 * 100, 2)
    assert round(metrics["kospi_recovery_from_low_pct"], 2) == round((2480.0 - 2470.0) / 2470.0 * 100, 2)
    assert metrics["foreign_net_sell_cumulative_bln"] == 4500
    assert round(metrics["advance_decline_ratio"], 4) == round(150 / 850, 4)


def test_evaluate_triggers_fires_on_threshold_breach():
    metrics = {
        "kospi_drop_from_open_pct": -2.0,   # < -1.5 → fires
        "kospi_recovery_from_low_pct": 0.2,  # < 1.0 → no fire
        "foreign_net_sell_cumulative_bln": 1000,  # < 4000 → no fire
        "advance_decline_ratio": 0.5,       # > 0.25 → no fire
    }
    drift_state = {"last_trigger_time": {}}
    triggered = evaluate_triggers(metrics, DRIFT_TRIGGERS, drift_state, cooldown_minutes=60)

    assert len(triggered) == 1
    assert triggered[0]["id"] == "index_sharp_drop"


def test_evaluate_triggers_respects_cooldown():
    from datetime import datetime, timedelta

    metrics = {
        "kospi_drop_from_open_pct": -2.0,
        "kospi_recovery_from_low_pct": 0.2,
        "foreign_net_sell_cumulative_bln": 1000,
        "advance_decline_ratio": 0.5,
    }
    recent = (datetime.now() - timedelta(minutes=10)).isoformat()
    drift_state = {"last_trigger_time": {"index_sharp_drop": recent}}
    triggered = evaluate_triggers(metrics, DRIFT_TRIGGERS, drift_state, cooldown_minutes=60)

    assert triggered == []


def test_check_returns_stable_when_no_trigger_fires():
    snapshot = {
        "kospi_current": 2525.0, "kospi_open": 2530.0, "kospi_low": 2520.0,
        "foreign_net_buy_bln": -100, "advance_count": 500, "decline_count": 400,
    }
    detector = RegimeDriftDetector(llm=FakeLiteLLM({}))
    drift_state = {"last_trigger_time": {}, "today_caution_count": 0, "daily_lite_llm_calls": 0}

    result = detector.check(snapshot, DRIFT_TRIGGERS, cooldown_minutes=60,
                             max_daily_triggers=3, drift_state=drift_state)

    assert result["drift_judgment"] == "STABLE"
    assert detector._llm.calls == 0


def test_check_calls_lite_llm_and_returns_caution():
    snapshot = {
        "kospi_current": 2480.0, "kospi_open": 2530.0, "kospi_low": 2470.0,
        "foreign_net_buy_bln": -4500, "advance_count": 150, "decline_count": 700,
    }
    lite_response = {
        "drift_judgment": "CAUTION",
        "reason": "외국인 매도 강도 높으나 RED 전환 기준 미달",
        "new_status": None,
        "risk_guidance_delta": {
            "buy_confidence_threshold": 82,
            "risk_per_trade_pct": 0.25,
            "max_positions": 3,
        },
        "updated_triggers": [],
    }
    detector = RegimeDriftDetector(llm=FakeLiteLLM(lite_response))
    drift_state = {"last_trigger_time": {}, "today_caution_count": 0, "daily_lite_llm_calls": 0}

    result = detector.check(snapshot, DRIFT_TRIGGERS, cooldown_minutes=60,
                             max_daily_triggers=3, drift_state=drift_state)

    assert result["drift_judgment"] == "CAUTION"
    assert result["risk_guidance_delta"]["max_positions"] == 3
    assert detector._llm.calls == 1
    assert result["drift_state"]["today_caution_count"] == 1
    assert result["drift_state"]["daily_lite_llm_calls"] == 1
    assert "index_sharp_drop" in result["drift_state"]["last_trigger_time"]


def test_check_skips_lite_llm_when_daily_limit_reached():
    snapshot = {
        "kospi_current": 2480.0, "kospi_open": 2530.0, "kospi_low": 2470.0,
        "foreign_net_buy_bln": -4500, "advance_count": 150, "decline_count": 700,
    }
    detector = RegimeDriftDetector(llm=FakeLiteLLM({}))
    drift_state = {"last_trigger_time": {}, "today_caution_count": 0, "daily_lite_llm_calls": 3}

    result = detector.check(snapshot, DRIFT_TRIGGERS, cooldown_minutes=60,
                             max_daily_triggers=3, drift_state=drift_state)

    assert result["drift_judgment"] == "STABLE"
    assert result["reason"] == "daily_limit_reached"
    assert detector._llm.calls == 0


def test_caution_counter_auto_escalates_to_regime_shift():
    snapshot = {
        "kospi_current": 2480.0, "kospi_open": 2530.0, "kospi_low": 2470.0,
        "foreign_net_buy_bln": -4500, "advance_count": 150, "decline_count": 700,
    }
    lite_response = {
        "drift_judgment": "CAUTION",
        "reason": "지속적 외인 매도",
        "new_status": None,
        "risk_guidance_delta": {"buy_confidence_threshold": 85, "risk_per_trade_pct": 0.20, "max_positions": 2},
        "updated_triggers": [],
    }
    detector = RegimeDriftDetector(llm=FakeLiteLLM(lite_response))
    # 이미 오늘 2번 CAUTION이 있었던 상태 (이번이 3번째)
    drift_state = {"last_trigger_time": {}, "today_caution_count": 2, "daily_lite_llm_calls": 2}

    result = detector.check(snapshot, DRIFT_TRIGGERS, cooldown_minutes=60,
                             max_daily_triggers=5, drift_state=drift_state,
                             current_status="YELLOW")

    assert result["drift_judgment"] == "REGIME_SHIFT"
    assert result["new_status"] == "RED"
    assert result["drift_state"]["today_caution_count"] == 3


def test_downgrade_status_progression():
    from agents.drift_detector import _downgrade_status
    assert _downgrade_status("GREEN") == "YELLOW"
    assert _downgrade_status("YELLOW") == "RED"
    assert _downgrade_status("RED") == "RED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_drift_detector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.drift_detector'`

- [ ] **Step 3: Create prompts/agents/drift_detector.md (Lite LLM 시스템 프롬프트)**

```markdown
# Drift Detector (Lite LLM)

## Role
당신은 장중 5분마다 발동된 drift_trigger를 검토하는 보조 판단자입니다.
오늘 아침 PREMARKET에서 내려진 레짐 판단을 전면 재검토하지 않습니다.
오직 "지금 발동된 트리거가 실제로 의미 있는 변화인지, 아니면 일시적 노이즈인지"만 판단합니다.

## Inputs
- `current_regime`: 오늘 아침 판단 (status, regime, confidence, risk_guidance)
- `triggered`: 지금 발동된 drift_trigger 목록 (id, metric, threshold, direction, description)
- `metrics`: 현재 시장 지표 스냅샷 (kospi_drop_from_open_pct, kospi_recovery_from_low_pct,
  foreign_net_sell_cumulative_bln, advance_decline_ratio)

## Decision: drift_judgment
- `STABLE`: 트리거가 발동했지만 일시적 변동(노이즈)으로 판단. 아침 판단 유지. risk_guidance 변경 없음.
- `CAUTION`: 시장이 다소 악화/개선되었으나 레짐 자체를 바꿀 정도는 아님.
  `risk_guidance_delta`로 임계값을 더 보수적(또는 완화)으로 조정.
- `REGIME_SHIFT`: 아침 판단이 더 이상 유효하지 않을 정도의 명확한 변화.
  `new_status`에 새 상태(GREEN/YELLOW/RED)를 명시.

## 판단 기준
- `index_sharp_drop` 또는 `foreign_heavy_sell`이 발동 + 다른 악화 지표 동반 → CAUTION 이상 검토
- `recovery_signal`이 발동 → CAUTION 이상에서 risk_guidance를 완화하는 방향 검토 (오후 회복 기회 포착)
- `breadth_collapse`만 단독 발동 + 다른 지표 정상 → STABLE 가능성 높음 (업종 쏠림일 수 있음)
- REGIME_SHIFT는 신중하게: 여러 지표가 동시에 악화/개선 방향으로 일치할 때만 선언

## Output JSON
```json
{
  "drift_judgment": "STABLE|CAUTION|REGIME_SHIFT",
  "reason": "",
  "new_status": null,
  "risk_guidance_delta": {
    "buy_confidence_threshold": 82,
    "risk_per_trade_pct": 0.25,
    "max_positions": 3
  },
  "updated_triggers": []
}
```

- `STABLE`일 때: `risk_guidance_delta`는 빈 객체 `{}`, `new_status`는 `null`
- `CAUTION`일 때: `risk_guidance_delta`에 조정값, `new_status`는 `null`
- `REGIME_SHIFT`일 때: `new_status`에 새 상태 필수, `risk_guidance_delta`도 함께 제공

## Forbidden
- 직접 주문/매수/매도 판단 금지 (TradingAgent의 역할)
- drift_triggers 자체를 새로 만들지 말 것 (단, `updated_triggers`로 쿨다운 갱신만 제안 가능)
```

- [ ] **Step 4: Implement agents/drift_detector.py**

```python
"""RegimeDriftDetector - 3-tier 비용 모델의 Tier2(코드 감시) + Tier3(Lite LLM) 구현.

Tier1(Full LLM)은 RegimeAgent가 담당. 이 모듈은 5분마다 무료로 drift_triggers를
체크하고(Tier2), 발동 시에만 Lite LLM을 호출한다(Tier3).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from config.settings import ModelTier
from llm.client import LLMClient
from llm.soul import inject_agent

_SYSTEM_PROMPT = inject_agent("drift_detector")

_STATUS_ORDER = ["GREEN", "YELLOW", "RED"]


def compute_metrics(snapshot: dict[str, Any]) -> dict[str, float]:
    """시장 스냅샷에서 drift_trigger 평가용 지표를 계산한다 (코드, 무료)."""
    kospi_current = float(snapshot["kospi_current"])
    kospi_open = float(snapshot["kospi_open"])
    kospi_low = float(snapshot["kospi_low"])
    foreign_net_buy_bln = float(snapshot["foreign_net_buy_bln"])
    advance_count = float(snapshot["advance_count"])
    decline_count = float(snapshot["decline_count"])

    kospi_drop_from_open_pct = (kospi_current - kospi_open) / kospi_open * 100
    kospi_recovery_from_low_pct = (kospi_current - kospi_low) / kospi_low * 100
    # foreign_net_buy_bln이 음수(순매도)일 때 양수 값으로 변환
    foreign_net_sell_cumulative_bln = max(-foreign_net_buy_bln, 0.0)
    total = advance_count + decline_count
    advance_decline_ratio = advance_count / total if total > 0 else 0.0

    return {
        "kospi_drop_from_open_pct": kospi_drop_from_open_pct,
        "kospi_recovery_from_low_pct": kospi_recovery_from_low_pct,
        "foreign_net_sell_cumulative_bln": foreign_net_sell_cumulative_bln,
        "advance_decline_ratio": advance_decline_ratio,
    }


def evaluate_triggers(
    metrics: dict[str, float],
    drift_triggers: list[dict],
    drift_state: dict[str, Any],
    cooldown_minutes: int,
    now: datetime | None = None,
) -> list[dict]:
    """발동된 drift_trigger 목록을 반환한다 (쿨다운 적용)."""
    now = now or datetime.now()
    last_trigger_time = drift_state.get("last_trigger_time", {})
    triggered = []

    for trigger in drift_triggers:
        metric_value = metrics.get(trigger["metric"])
        if metric_value is None:
            continue

        threshold = trigger["threshold"]
        direction = trigger["direction"]
        if direction == "above":
            fired = metric_value > threshold
        elif direction == "below":
            fired = metric_value < threshold
        else:
            continue

        if not fired:
            continue

        last_fired = last_trigger_time.get(trigger["id"])
        if last_fired is not None:
            elapsed = now - datetime.fromisoformat(last_fired)
            if elapsed < timedelta(minutes=cooldown_minutes):
                continue

        triggered.append(trigger)

    return triggered


def _downgrade_status(status: str) -> str:
    """상태를 한 단계 악화시킨다 (GREEN→YELLOW→RED, RED는 유지)."""
    idx = _STATUS_ORDER.index(status)
    return _STATUS_ORDER[min(idx + 1, len(_STATUS_ORDER) - 1)]


class RegimeDriftDetector:
    """5분마다 drift_triggers를 체크하고, 발동 시 Lite LLM을 호출한다."""

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm or LLMClient()

    def check(
        self,
        market_snapshot: dict[str, Any],
        drift_triggers: list[dict],
        cooldown_minutes: int,
        max_daily_triggers: int,
        drift_state: dict[str, Any],
        current_status: str = "YELLOW",
        current_regime: dict | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now()
        metrics = compute_metrics(market_snapshot)
        triggered = evaluate_triggers(metrics, drift_triggers, drift_state, cooldown_minutes, now)

        new_drift_state = dict(drift_state)
        new_drift_state.setdefault("last_trigger_time", dict(drift_state.get("last_trigger_time", {})))
        new_drift_state.setdefault("today_caution_count", drift_state.get("today_caution_count", 0))
        new_drift_state.setdefault("daily_lite_llm_calls", drift_state.get("daily_lite_llm_calls", 0))

        if not triggered:
            return {
                "drift_judgment": "STABLE",
                "reason": "no_trigger_fired",
                "metrics": metrics,
                "triggered": [],
                "new_status": None,
                "risk_guidance_delta": {},
                "drift_state": new_drift_state,
            }

        if new_drift_state["daily_lite_llm_calls"] >= max_daily_triggers:
            return {
                "drift_judgment": "STABLE",
                "reason": "daily_limit_reached",
                "metrics": metrics,
                "triggered": triggered,
                "new_status": None,
                "risk_guidance_delta": {},
                "drift_state": new_drift_state,
            }

        result = self._call_lite_llm(current_regime or {}, metrics, triggered)

        for trigger in triggered:
            new_drift_state["last_trigger_time"][trigger["id"]] = now.isoformat()
        new_drift_state["daily_lite_llm_calls"] += 1

        drift_judgment = result.get("drift_judgment", "STABLE")
        if drift_judgment == "CAUTION":
            new_drift_state["today_caution_count"] += 1
            if new_drift_state["today_caution_count"] >= 3:
                drift_judgment = "REGIME_SHIFT"
                result["new_status"] = _downgrade_status(current_status)

        return {
            "drift_judgment": drift_judgment,
            "reason": result.get("reason", ""),
            "metrics": metrics,
            "triggered": triggered,
            "new_status": result.get("new_status"),
            "risk_guidance_delta": result.get("risk_guidance_delta", {}),
            "updated_triggers": result.get("updated_triggers", []),
            "drift_state": new_drift_state,
        }

    def _call_lite_llm(
        self, current_regime: dict, metrics: dict[str, float], triggered: list[dict]
    ) -> dict[str, Any]:
        import json

        user_msg = json.dumps(
            {
                "current_regime": current_regime,
                "triggered": triggered,
                "metrics": metrics,
            },
            ensure_ascii=False,
        )
        return self._llm.call(system=_SYSTEM_PROMPT, user=user_msg, tier=ModelTier.FAST, expect_json=True)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/test_drift_detector.py -v`
Expected: All PASS

- [ ] **Step 6: Run full test suite to confirm no regressions**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/ -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
cd /mnt/c/Users/gocho/MQK-v2
git add agents/drift_detector.py prompts/agents/drift_detector.md tests/test_drift_detector.py
git commit -m "feat(v3): add RegimeDriftDetector with Tier2 code monitor + Tier3 Lite LLM"
```

---

### Task 13: TradingAgent — Phase별 프롬프트 + MIL 도구 바인딩 + 사전주입 컨텍스트

**Files:**
- Create: `agents/trading_agent.py`
- Create: `prompts/agents/trading_agent/premarket.md`
- Create: `prompts/agents/trading_agent/scan.md`
- Create: `prompts/agents/trading_agent/intraday.md`
- Create: `prompts/agents/trading_agent/close.md`
- Create: `prompts/agents/trading_agent/market_close.md`
- Test: `tests/test_trading_agent.py` (신규)

- [ ] **Step 1: Write failing tests**

`tests/test_trading_agent.py`:

```python
"""TradingAgent 테스트 - Phase별 도구 바인딩 + ReAct 루프 + 사전주입 컨텍스트"""
import pytest

from agents import trading_agent
from agents.trading_agent import (
    TOOL_REGISTRY,
    PHASE_TOOLS,
    TradingAgent,
    TradingPhase,
    build_context,
)
from market_intelligence.base import ToolFailure


class FakeLLMClient:
    """미리 정해진 응답 큐를 순서대로 반환하는 LLM 스텁."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def call(self, system, user, tier=None, expect_json=True):
        self.calls.append((system, user))
        return self._responses.pop(0)


def test_build_context_includes_allowed_tools_for_phase():
    ctx = build_context(
        phase=TradingPhase.INTRADAY,
        trading_date="2026-06-09",
        regime={"status": "YELLOW", "confidence": 44},
        drift_status="STABLE",
        risk_guidance={"buy_confidence_threshold": 75, "risk_per_trade_pct": 0.35,
                        "max_positions": 4, "min_trading_value_krw": 10_000_000_000},
        portfolio_snapshot={"positions": [], "position_count": 0},
        daily_pnl={"realized_pnl_pct": 0.0},
        risk_budget_remaining={"positions_left": 4, "daily_loss_remaining_pct": 2.0},
        watchlist=["005930", "000660"],
    )

    assert ctx["current_phase"] == "INTRADAY"
    assert ctx["watchlist"] == ["005930", "000660"]
    assert ctx["allowed_tools"] == PHASE_TOOLS[TradingPhase.INTRADAY]
    assert "psearch_title" not in ctx["allowed_tools"]


def test_phase_tools_only_reference_known_tools():
    for phase, tools in PHASE_TOOLS.items():
        for tool in tools:
            assert tool in TOOL_REGISTRY, f"{phase}: unknown tool {tool}"


def test_run_executes_allowed_tool_then_returns_final(monkeypatch):
    def fake_get_market_context(ctx, phase):
        return {"status": "YELLOW", "confidence": 44, "kospi_change_pct": -0.5}

    monkeypatch.setitem(TOOL_REGISTRY, "get_market_context", fake_get_market_context)

    llm = FakeLLMClient([
        {"next_action": "call_tool", "tool": "get_market_context", "tool_args": {}},
        {"next_action": "final", "action": "WATCHLIST_UPDATE",
         "watchlist": ["005930"], "reason": "반도체 강세"},
    ])
    agent = TradingAgent(mil=object(), llm=llm)
    context = build_context(
        phase=TradingPhase.SCAN, trading_date="2026-06-09",
        regime={"status": "YELLOW"}, drift_status="STABLE",
        risk_guidance={}, portfolio_snapshot={}, daily_pnl={}, risk_budget_remaining={},
    )

    result = agent.run(TradingPhase.SCAN, context)

    assert result["action"] == "WATCHLIST_UPDATE"
    assert result["watchlist"] == ["005930"]
    assert len(llm.calls) == 2
    # 두 번째 호출의 user 메시지에 첫 번째 도구 결과가 포함되어야 한다
    assert "kospi_change_pct" in llm.calls[1][1]


def test_run_blocks_tool_not_allowed_in_phase():
    llm = FakeLLMClient([
        {"next_action": "call_tool", "tool": "psearch_title", "tool_args": {}},
        {"next_action": "final", "action": "NO_TRADE", "reason": "psearch 사용 불가, 관망"},
    ])
    agent = TradingAgent(mil=object(), llm=llm)
    context = build_context(
        phase=TradingPhase.INTRADAY, trading_date="2026-06-09",
        regime={"status": "YELLOW"}, drift_status="STABLE",
        risk_guidance={}, portfolio_snapshot={}, daily_pnl={}, risk_budget_remaining={},
        watchlist=["005930"],
    )

    result = agent.run(TradingPhase.INTRADAY, context)

    assert result["action"] == "NO_TRADE"
    # 차단 사유가 다음 턴 LLM에게 전달되었어야 한다
    assert "tool_not_allowed_in_phase" in llm.calls[1][1]


def test_run_handles_tool_failure(monkeypatch):
    def failing_get_flow(ctx, phase, ticker):
        raise ToolFailure("get_flow: circuit breaker open")

    monkeypatch.setitem(TOOL_REGISTRY, "get_flow", failing_get_flow)

    llm = FakeLLMClient([
        {"next_action": "call_tool", "tool": "get_flow", "tool_args": {"ticker": "005930"}},
        {"next_action": "final", "action": "NO_TRADE", "reason": "수급 데이터 없음"},
    ])
    agent = TradingAgent(mil=object(), llm=llm)
    context = build_context(
        phase=TradingPhase.INTRADAY, trading_date="2026-06-09",
        regime={"status": "YELLOW"}, drift_status="STABLE",
        risk_guidance={}, portfolio_snapshot={}, daily_pnl={}, risk_budget_remaining={},
        watchlist=["005930"],
    )

    result = agent.run(TradingPhase.INTRADAY, context)

    assert result["action"] == "NO_TRADE"
    assert "tool_failure" in llm.calls[1][1]


def test_run_returns_no_trade_after_max_steps():
    llm = FakeLLMClient([
        {"next_action": "call_tool", "tool": "get_open_positions", "tool_args": {}},
    ] * 10)

    def fake_get_open_positions(ctx, phase):
        return {"positions": []}

    trading_agent.TOOL_REGISTRY["get_open_positions"] = fake_get_open_positions
    agent = TradingAgent(mil=object(), llm=llm, max_steps=3)
    context = build_context(
        phase=TradingPhase.CLOSE, trading_date="2026-06-09",
        regime={"status": "YELLOW"}, drift_status="STABLE",
        risk_guidance={}, portfolio_snapshot={}, daily_pnl={}, risk_budget_remaining={},
    )

    result = agent.run(TradingPhase.CLOSE, context)

    assert result["action"] == "NO_TRADE"
    assert result["reason"] == "max_steps_exceeded"
    assert len(llm.calls) == 3


def test_psearch_tools_inject_hts_user_id_from_env(monkeypatch):
    monkeypatch.setenv("KIS_HTS_ID", "test-hts-id")
    captured = {}

    def fake_psearch_title(ctx, phase, user_id):
        captured["user_id"] = user_id
        return {"conditions": []}

    monkeypatch.setitem(TOOL_REGISTRY, "psearch_title", fake_psearch_title)

    llm = FakeLLMClient([
        {"next_action": "call_tool", "tool": "psearch_title", "tool_args": {}},
        {"next_action": "final", "action": "WATCHLIST_UPDATE", "watchlist": [], "reason": "조건없음"},
    ])
    agent = TradingAgent(mil=object(), llm=llm)
    context = build_context(
        phase=TradingPhase.SCAN, trading_date="2026-06-09",
        regime={"status": "YELLOW"}, drift_status="STABLE",
        risk_guidance={}, portfolio_snapshot={}, daily_pnl={}, risk_budget_remaining={},
    )

    agent.run(TradingPhase.SCAN, context)

    assert captured["user_id"] == "test-hts-id"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_trading_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.trading_agent'`

- [ ] **Step 3: Create Phase별 프롬프트**

`prompts/agents/trading_agent/premarket.md`:

```markdown
# TradingAgent — PREMARKET

## Role
장 시작 전(08:45), 전일 보유 포지션의 리스크를 점검하는 단계입니다.
오늘의 레짐 판단(`regime`)과 `risk_guidance`는 이미 RegimeAgent가 결정했습니다.
당신은 이를 변경하지 않고, 전일 보유 종목에 새로운 위험 신호가 있는지만 확인합니다.

## Inputs (사전주입 컨텍스트)
- `regime`: 오늘 아침 레짐 판단 (status, confidence)
- `risk_guidance`: 오늘의 리스크 파라미터
- `portfolio.positions`: 전일 보유 종목 목록

## 사용 가능 도구
`allowed_tools`에 명시된 도구만 사용하세요. 보유 종목 한정으로 `get_ohlcv`, `get_flow`,
`get_event_schedule`을 호출해 갭/공시/수급 급변을 확인할 수 있습니다.

## 진행 방식 (ReAct)
매 턴마다 아래 중 하나를 출력합니다:

```json
{"next_action": "call_tool", "tool": "<도구명>", "tool_args": {...}}
```

또는 충분한 정보를 얻었으면:

```json
{
  "next_action": "final",
  "action": "PREMARKET_REVIEW",
  "position_notes": [
    {"ticker": "005930", "risk_level": "NORMAL|WATCH|URGENT", "note": "..."}
  ],
  "reason": ""
}
```

## Forbidden
- 레짐(`status`/`regime`/`risk_guidance`) 변경 금지 — RegimeAgent의 영역입니다.
- 신규 매수/매도 proposal 생성 금지 — SCAN/INTRADAY/CLOSE의 영역입니다.
```

`prompts/agents/trading_agent/scan.md`:

```markdown
# TradingAgent — SCAN

## Role
신규 후보를 탐색하고 watchlist를 생성/갱신합니다 (09:10, 11:00, 14:00).
**레짐이 RED여도 스캔은 항상 수행합니다.** RED일 때는 `risk_guidance`에 따라
더 엄격한 기준(높은 confidence threshold, 큰 거래대금, 강한 상대강도)으로 후보를 선별합니다.

## Inputs (사전주입 컨텍스트)
- `regime`, `risk_guidance`, `drift_status`
- `portfolio`, `risk_budget_remaining` (남은 포지션 슬롯 수)

## 권장 흐름
1. `get_market_context`로 시장 배경 확인
2. `psearch_result`로 조건검색 후보 탐색 (실패 시 `get_top_movers`로 백업,
   백업 사용 시 최종 결과에 `"overheated_bias_warning": true` 포함)
3. 후보별 `get_stock_status`로 VI/관리종목/거래정지 확인 → 문제 있으면 후보에서 제외
4. 후보별 `get_ohlcv` + `get_flow` + `get_news_stock`으로 분석
5. `risk_guidance.min_trading_value_krw` 미만 거래대금 종목은 제외
6. watchlist 확정 (최대 10개, `risk_budget_remaining.positions_left` 고려)

## 진행 방식 (ReAct)
```json
{"next_action": "call_tool", "tool": "<도구명>", "tool_args": {...}}
```

또는:

```json
{
  "next_action": "final",
  "action": "WATCHLIST_UPDATE",
  "watchlist": ["005930", "000660"],
  "candidates": [
    {"ticker": "005930", "confidence": 78, "reason": "...", "setup": "TREND|RELATIVE_STRENGTH|INTRADAY_RECOVERY|REVERSAL"}
  ],
  "overheated_bias_warning": false,
  "reason": ""
}
```

## Forbidden
- 직접 주문/매수 proposal 생성 금지 — INTRADAY의 역할입니다.
- `min_trading_value_krw` 미만 종목을 watchlist에 포함 금지.
```

`prompts/agents/trading_agent/intraday.md`:

```markdown
# TradingAgent — INTRADAY

## Role
watchlist 종목을 모니터링하며 매수/청산 proposal을 생성합니다 (09:20~15:00, 5분 간격).
**최종 결정은 proposal일 뿐입니다.** RiskOfficer/PositionSizer/Telegram 승인을 통과해야
실제 주문이 실행됩니다.

## Inputs (사전주입 컨텍스트)
- `regime`, `risk_guidance` (drift detector에 의해 장중 강화/완화될 수 있음)
- `drift_status`: STABLE/CAUTION/REGIME_SHIFT
- `watchlist`: 평가 대상 종목 (이 목록 외 종목은 평가하지 않음 — SCAN 재실행만이 갱신 경로)
- `portfolio.positions`: 현재 보유 종목 (청산 판단 대상)
- `risk_budget_remaining`: 남은 포지션 슬롯, 남은 일일 손실 한도

## BUY 판단 기준
- `confidence >= risk_guidance.buy_confidence_threshold`인 경우만 BUY proposal 생성
- `risk_per_trade_pct`는 참고용 — 실제 사이즈는 PositionSizer가 계산
- stop_loss는 반드시 명시 (ATR 또는 직전 저점 기준)
- RED/CAUTION 상황에서도 강한 상대강도 + 회복 신호가 있으면 평가 가능 (단, threshold가 높음)

## SELL 판단 기준
- 보유 종목의 손절/익절 조건 도달 시 SELL proposal
- `drift_status == "REGIME_SHIFT"`이고 새 상태가 RED인 경우 보유 종목 전반의 청산 검토 강화

## 진행 방식 (ReAct)
```json
{"next_action": "call_tool", "tool": "<도구명>", "tool_args": {"ticker": "005930"}}
```

또는:

```json
{
  "next_action": "final",
  "action": "BUY|SELL|HOLD|NO_TRADE",
  "proposals": [
    {
      "ticker": "005930",
      "side": "BUY",
      "confidence": 82,
      "setup": "INTRADAY_RECOVERY",
      "stop_loss_price": 68000,
      "reason": ""
    }
  ],
  "reason": ""
}
```

- 제안할 게 없으면 `action: "NO_TRADE"`, `proposals: []`

## Forbidden
- watchlist 외 종목 신규 평가 금지
- 주문 직접 실행 금지 (proposal까지만)
- stop_loss 없는 BUY proposal 금지
```

`prompts/agents/trading_agent/close.md`:

```markdown
# TradingAgent — CLOSE

## Role
장 마감 전(15:30), 보유 포지션의 청산 여부를 최종 판단합니다.

## Inputs (사전주입 컨텍스트)
- `regime`, `risk_guidance`
- `portfolio.positions`, `daily_pnl`

## 권장 흐름
1. `get_open_positions`로 현재 보유 종목 확인
2. 종목별 `get_ohlcv`로 당일 가격 흐름 확인
3. `get_daily_pnl`로 오늘의 실현/평가 손익 확인
4. 익절/손절/시간 청산 조건에 해당하는 종목 식별

## 진행 방식 (ReAct)
```json
{"next_action": "call_tool", "tool": "<도구명>", "tool_args": {...}}
```

또는:

```json
{
  "next_action": "final",
  "action": "CLOSE_REVIEW",
  "sell_proposals": [
    {"ticker": "005930", "side": "SELL", "reason": "당일 +3% 익절 목표 도달"}
  ],
  "reason": ""
}
```

- 청산할 종목이 없으면 `sell_proposals: []`

## Forbidden
- 신규 매수 proposal 생성 금지
- 보유하지 않은 종목에 대한 SELL proposal 금지
```

`prompts/agents/trading_agent/market_close.md`:

```markdown
# TradingAgent — MARKET_CLOSE

## Role
장 마감 후(17:00), 오늘 시장을 분석하고 다음날 PREMARKET 판단의 prior를 생성합니다.
**거래가 없었던 날에도 이 단계는 항상 수행됩니다.**

## Inputs (사전주입 컨텍스트)
- `regime` (오늘 아침 판단), `daily_pnl`, `portfolio`

## 권장 흐름
1. `get_market_context` + `get_sector_breadth`로 마감 지수/업종 현황 확인
2. `get_news_market`으로 마감 후 주요 뉴스 확인
3. 보유/관심 종목 중 의미 있는 종목은 `get_ohlcv`로 마감 흐름 확인

## 진행 방식 (ReAct)
```json
{"next_action": "call_tool", "tool": "<도구명>", "tool_args": {...}}
```

또는:

```json
{
  "next_action": "final",
  "action": "MARKET_CLOSE_ANALYSIS",
  "market_close_snapshot": {
    "kospi_change_pct": 0.0,
    "kosdaq_change_pct": 0.0,
    "data_quality": {"missing_fields": []}
  },
  "close_market_read": {
    "market_quality": "GOOD|NEUTRAL|POOR",
    "leadership_quality": "STRONG|MIXED|WEAK",
    "distribution_warning": false,
    "accumulation_signal": false,
    "regime_prior_for_tomorrow": "UPTREND|DOWNTREND|SIDEWAYS|RISK_OFF",
    "focus_themes": [],
    "risk_notes": []
  },
  "next_day_premarket_context": {
    "previous_close_prior": {},
    "tomorrow_bias": {"risk_posture": "NORMAL|DEFENSIVE", "scanner_bias": "NORMAL|RELATIVE_STRENGTH_ONLY"}
  },
  "reason": ""
}
```

## Forbidden
- 매수/매도 proposal 생성 금지 (분석 전용 단계)
```

- [ ] **Step 4: Implement agents/trading_agent.py**

```python
"""TradingAgent - 단일 LLM이 Phase별 ReAct 루프로 MIL 16개 도구를 사용해
PREMARKET/SCAN/INTRADAY/CLOSE/MARKET_CLOSE 단계를 수행한다.

최종 출력은 proposal일 뿐이며, v2 Safety Layer(RiskOfficer/PositionSizer/
Telegram approval/OrderManager)가 코드로 강제한다.
"""
from __future__ import annotations

import json
import os
from enum import Enum
from typing import Any, Callable

from config.settings import ModelTier
from llm.client import LLMClient
from llm.soul import inject_agent
from market_intelligence import market, portfolio, risk_filter, screening, stock
from market_intelligence.base import MILContext, ToolFailure


class TradingPhase(str, Enum):
    PREMARKET = "PREMARKET"
    SCAN = "SCAN"
    INTRADAY = "INTRADAY"
    CLOSE = "CLOSE"
    MARKET_CLOSE = "MARKET_CLOSE"


_PHASE_PROMPT_NAMES = {
    TradingPhase.PREMARKET: "trading_agent/premarket",
    TradingPhase.SCAN: "trading_agent/scan",
    TradingPhase.INTRADAY: "trading_agent/intraday",
    TradingPhase.CLOSE: "trading_agent/close",
    TradingPhase.MARKET_CLOSE: "trading_agent/market_close",
}

TOOL_REGISTRY: dict[str, Callable] = {
    "get_market_context": market.get_market_context,
    "get_sector_breadth": market.get_sector_breadth,
    "get_intraday_index_candles": market.get_intraday_index_candles,
    "get_news_market": market.get_news_market,
    "psearch_title": screening.psearch_title,
    "psearch_result": screening.psearch_result,
    "get_top_movers": screening.get_top_movers,
    "get_ohlcv": stock.get_ohlcv,
    "get_realtime_price": stock.get_realtime_price,
    "get_intraday_candles": stock.get_intraday_candles,
    "get_flow": stock.get_flow,
    "get_news_stock": stock.get_news_stock,
    "get_stock_status": risk_filter.get_stock_status,
    "get_event_schedule": risk_filter.get_event_schedule,
    "get_open_positions": portfolio.get_open_positions,
    "get_daily_pnl": portfolio.get_daily_pnl,
}

PHASE_TOOLS: dict[TradingPhase, list[str]] = {
    TradingPhase.PREMARKET: [
        "get_market_context", "get_sector_breadth", "get_intraday_index_candles",
        "get_news_market", "get_event_schedule", "get_ohlcv", "get_flow",
    ],
    TradingPhase.SCAN: [
        "get_market_context", "get_sector_breadth", "get_intraday_index_candles", "get_news_market",
        "psearch_title", "psearch_result", "get_top_movers",
        "get_ohlcv", "get_flow", "get_stock_status", "get_news_stock",
    ],
    TradingPhase.INTRADAY: [
        "get_ohlcv", "get_intraday_candles", "get_flow", "get_news_stock", "get_stock_status",
    ],
    TradingPhase.CLOSE: [
        "get_market_context", "get_sector_breadth", "get_news_market",
        "get_ohlcv", "get_open_positions", "get_daily_pnl", "get_news_stock",
    ],
    TradingPhase.MARKET_CLOSE: [
        "get_market_context", "get_sector_breadth", "get_news_market", "get_ohlcv",
    ],
}

_TOOLS_REQUIRING_USER_ID = {"psearch_title", "psearch_result"}


def build_context(
    phase: TradingPhase,
    trading_date: str,
    regime: dict,
    drift_status: str,
    risk_guidance: dict,
    portfolio_snapshot: dict,
    daily_pnl: dict,
    risk_budget_remaining: dict,
    watchlist: list[str] | None = None,
    context_timestamps: dict | None = None,
) -> dict:
    """TradingAgent에 사전 주입할 컨텍스트를 구성한다 (스펙 섹션 2.4)."""
    return {
        "current_phase": phase.value,
        "trading_date": trading_date,
        "regime": regime,
        "drift_status": drift_status,
        "risk_guidance": risk_guidance,
        "portfolio": portfolio_snapshot,
        "daily_pnl": daily_pnl,
        "risk_budget_remaining": risk_budget_remaining,
        "watchlist": watchlist or [],
        "allowed_tools": list(PHASE_TOOLS[phase]),
        "context_timestamps": context_timestamps or {},
    }


class TradingAgent:
    """Phase별 프롬프트 + MIL 도구로 ReAct 루프를 실행하는 단일 LLM 에이전트."""

    def __init__(self, mil: MILContext, llm: LLMClient | None = None, max_steps: int = 6):
        self._mil = mil
        self._llm = llm or LLMClient()
        self._max_steps = max_steps

    def run(self, phase: TradingPhase, context: dict) -> dict:
        system_prompt = inject_agent(_PHASE_PROMPT_NAMES[phase])
        transcript = [json.dumps({"context": context}, ensure_ascii=False)]

        for _ in range(self._max_steps):
            user_msg = "\n\n---\n\n".join(transcript)
            response = self._llm.call(
                system=system_prompt, user=user_msg, tier=ModelTier.STANDARD, expect_json=True
            )

            next_action = response.get("next_action")
            if next_action == "final":
                return response

            if next_action == "call_tool":
                tool_name = response.get("tool", "")
                tool_args = response.get("tool_args", {})
                tool_result = self._execute_tool(phase, tool_name, tool_args)
                transcript.append(json.dumps(
                    {"tool_call": {"tool": tool_name, "args": tool_args}, "tool_result": tool_result},
                    ensure_ascii=False,
                ))
                continue

            return {"next_action": "final", "action": "NO_TRADE",
                    "reason": f"unknown_next_action:{next_action}"}

        return {"next_action": "final", "action": "NO_TRADE", "reason": "max_steps_exceeded"}

    def _execute_tool(self, phase: TradingPhase, tool_name: str, tool_args: dict) -> dict:
        if tool_name not in TOOL_REGISTRY:
            return {"error": "unknown_tool", "tool": tool_name}

        if tool_name not in PHASE_TOOLS[phase]:
            return {"error": "tool_not_allowed_in_phase", "tool": tool_name, "phase": phase.value}

        func = TOOL_REGISTRY[tool_name]
        call_args: dict[str, Any] = dict(tool_args)
        if tool_name in _TOOLS_REQUIRING_USER_ID:
            call_args["user_id"] = os.environ.get("KIS_HTS_ID", "")

        try:
            return func(self._mil, phase.value, **call_args)
        except ToolFailure as e:
            return {"error": "tool_failure", "tool": tool_name, "message": str(e)}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_trading_agent.py -v`
Expected: All PASS

- [ ] **Step 6: Run full test suite to confirm no regressions**

Run: `cd /mnt/c/Users/gocho/MQK-v2 && .venv/bin/pytest tests/ -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
cd /mnt/c/Users/gocho/MQK-v2
git add agents/trading_agent.py prompts/agents/trading_agent/ tests/test_trading_agent.py
git commit -m "feat(v3): add TradingAgent with phase-based ReAct loop over 16 MIL tools"
```

---

### Task 14: OrchestratorV3 + run_schedule_v3.py + PM2 스케줄

**Files:**
- Create: `orchestrator_v3.py`
- Create: `run_schedule_v3.py`
- Modify: `ecosystem.config.cjs`
- Test: `tests/test_orchestrator_v3.py` (신규)

- [ ] **Step 1: Write failing tests**

`tests/test_orchestrator_v3.py`:

```python
"""OrchestratorV3 테스트 - drift_state/watchlist 영속화 + Phase 오케스트레이션"""
import json
from pathlib import Path

import pytest

from agents.trading_agent import TradingPhase
from codes.risk_officer import RiskViolation
from orchestrator_v3 import (
    MQKOrchestratorV3,
    load_drift_state,
    save_drift_state,
    load_watchlist,
    save_watchlist,
)


# ── drift_state / watchlist 영속화 ──────────────────────────────────────────

def test_load_drift_state_returns_default_when_missing(tmp_path):
    path = tmp_path / "drift_state.json"
    state = load_drift_state(path=path, today="2026-06-09")

    assert state["date"] == "2026-06-09"
    assert state["today_caution_count"] == 0
    assert state["daily_lite_llm_calls"] == 0
    assert state["last_trigger_time"] == {}


def test_save_and_load_drift_state_same_day(tmp_path):
    path = tmp_path / "drift_state.json"
    state = {"date": "2026-06-09", "last_trigger_time": {"index_sharp_drop": "2026-06-09T10:00:00"},
             "today_caution_count": 1, "daily_lite_llm_calls": 1}
    save_drift_state(state, path=path)

    loaded = load_drift_state(path=path, today="2026-06-09")
    assert loaded == state


def test_load_drift_state_resets_on_new_day(tmp_path):
    path = tmp_path / "drift_state.json"
    save_drift_state({"date": "2026-06-09", "last_trigger_time": {}, "today_caution_count": 2,
                       "daily_lite_llm_calls": 3}, path=path)

    loaded = load_drift_state(path=path, today="2026-06-10")
    assert loaded["date"] == "2026-06-10"
    assert loaded["today_caution_count"] == 0
    assert loaded["daily_lite_llm_calls"] == 0


def test_save_and_load_watchlist_roundtrip(tmp_path):
    path = tmp_path / "watchlist.json"
    save_watchlist(["005930", "000660"], path=path)

    assert load_watchlist(path=path) == ["005930", "000660"]


def test_load_watchlist_returns_empty_when_missing(tmp_path):
    assert load_watchlist(path=tmp_path / "missing.json") == []


# ── 오케스트레이터 헬퍼 ──────────────────────────────────────────────────────

def make_orchestrator(tmp_path: Path) -> MQKOrchestratorV3:
    orch = MQKOrchestratorV3.__new__(MQKOrchestratorV3)
    orch._today = "2026-06-09"
    orch._log_dir = tmp_path
    orch._mil = object()
    orch._atr_cache = {}
    return orch


def test_build_context_uses_mil_portfolio_tools(monkeypatch, tmp_path):
    import market_intelligence.portfolio as mil_portfolio

    monkeypatch.setattr(mil_portfolio, "get_open_positions",
                         lambda ctx, phase: {"positions": [{"ticker": "005930"}], "position_count": 1})
    monkeypatch.setattr(mil_portfolio, "get_daily_pnl",
                         lambda ctx, phase: {"realized_pnl_pct": -0.5, "realized_pnl_krw": -50000, "total_eval_amt": 10_000_000})

    orch = make_orchestrator(tmp_path)
    regime = {"status": "YELLOW", "regime": "SIDEWAYS", "confidence": 50,
              "risk_guidance": {"max_positions": 4, "buy_confidence_threshold": 75,
                                 "risk_per_trade_pct": 0.35, "min_trading_value_krw": 1_000_000_000},
              "timestamp": "2026-06-09T08:45:00"}

    ctx = orch._build_context(TradingPhase.INTRADAY, regime, "STABLE", watchlist=["005930"])

    assert ctx["portfolio"]["position_count"] == 1
    assert ctx["risk_budget_remaining"]["positions_left"] == 3
    assert ctx["daily_pnl"]["realized_pnl_pct"] == -0.5
    assert ctx["watchlist"] == ["005930"]
    assert ctx["allowed_tools"] == ["get_ohlcv", "get_intraday_candles", "get_flow", "get_news_stock", "get_stock_status"]


def test_collect_drift_snapshot_combines_market_tools(monkeypatch, tmp_path):
    import market_intelligence.market as mil_market

    monkeypatch.setattr(mil_market, "get_market_context",
                         lambda ctx, phase: {"kospi": 2480.0, "foreign_net_buy_krw": -450_000_000_000})
    monkeypatch.setattr(mil_market, "get_intraday_index_candles",
                         lambda ctx, phase: {"candles": [
                             {"open": 2530.0, "high": 2531.0, "low": 2525.0, "close": 2528.0},
                             {"open": 2528.0, "high": 2529.0, "low": 2470.0, "close": 2480.0},
                         ]})
    monkeypatch.setattr(mil_market, "get_sector_breadth",
                         lambda ctx, phase: {"sectors": [
                             {"advancers": 100, "decliners": 300},
                             {"advancers": 50, "decliners": 250},
                         ]})

    orch = make_orchestrator(tmp_path)
    snapshot = orch._collect_drift_snapshot()

    assert snapshot["kospi_current"] == 2480.0
    assert snapshot["kospi_open"] == 2530.0
    assert snapshot["kospi_low"] == 2470.0
    assert snapshot["foreign_net_buy_bln"] == -4500.0
    assert snapshot["advance_count"] == 150
    assert snapshot["decline_count"] == 550


# ── run_intraday_v3 ──────────────────────────────────────────────────────────

class FakeDriftDetector:
    def __init__(self, response):
        self._response = response

    def check(self, **kwargs):
        return self._response


class FakeTradingAgent:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def run(self, phase, context):
        self.calls.append((phase, context))
        return self._response


def test_run_intraday_v3_stable_executes_no_trade(monkeypatch, tmp_path):
    import market_intelligence.portfolio as mil_portfolio
    import market_intelligence.market as mil_market

    monkeypatch.setattr(mil_portfolio, "get_open_positions",
                         lambda ctx, phase: {"positions": [], "position_count": 0})
    monkeypatch.setattr(mil_portfolio, "get_daily_pnl",
                         lambda ctx, phase: {"realized_pnl_pct": 0.0, "realized_pnl_krw": 0, "total_eval_amt": 10_000_000})
    monkeypatch.setattr(mil_market, "get_market_context",
                         lambda ctx, phase: {"kospi": 2520.0, "foreign_net_buy_krw": 0})
    monkeypatch.setattr(mil_market, "get_intraday_index_candles",
                         lambda ctx, phase: {"candles": [{"open": 2525.0, "high": 2526.0, "low": 2515.0, "close": 2520.0}]})
    monkeypatch.setattr(mil_market, "get_sector_breadth",
                         lambda ctx, phase: {"sectors": [{"advancers": 400, "decliners": 300}]})

    orch = make_orchestrator(tmp_path)
    orch._drift_detector = FakeDriftDetector({
        "drift_judgment": "STABLE", "reason": "no_trigger_fired", "metrics": {}, "triggered": [],
        "new_status": None, "risk_guidance_delta": {},
        "drift_state": {"date": "2026-06-09", "last_trigger_time": {}, "today_caution_count": 0, "daily_lite_llm_calls": 0},
    })
    orch._trading_agent = FakeTradingAgent({"action": "NO_TRADE", "proposals": [], "reason": "조건 미충족"})

    regime = {
        "status": "YELLOW", "regime": "SIDEWAYS", "confidence": 50,
        "risk_guidance": {"max_positions": 4, "buy_confidence_threshold": 75,
                           "risk_per_trade_pct": 0.35, "min_trading_value_krw": 1_000_000_000},
        "drift_triggers": [], "cooldown_minutes": 60, "max_daily_triggers": 3,
        "timestamp": "2026-06-09T08:45:00",
    }
    (tmp_path / "last_regime.json").write_text(json.dumps(regime), encoding="utf-8")
    (tmp_path / "watchlist.json").write_text(json.dumps({"watchlist": ["005930"]}), encoding="utf-8")

    monkeypatch.setattr("orchestrator_v3._LAST_REGIME_PATH", tmp_path / "last_regime.json")
    monkeypatch.setattr("orchestrator_v3._DRIFT_STATE_PATH", tmp_path / "drift_state.json")
    monkeypatch.setattr("orchestrator_v3._WATCHLIST_PATH", tmp_path / "watchlist.json")

    result = orch.run_intraday_v3()

    assert result["action"] == "NO_TRADE"
    assert orch._trading_agent.calls[0][0] == TradingPhase.INTRADAY
    assert orch._trading_agent.calls[0][1]["watchlist"] == ["005930"]
    saved_drift = json.loads((tmp_path / "drift_state.json").read_text(encoding="utf-8"))
    assert saved_drift["today_caution_count"] == 0


def test_run_intraday_v3_regime_shift_updates_status_and_rescans(monkeypatch, tmp_path):
    import market_intelligence.portfolio as mil_portfolio
    import market_intelligence.market as mil_market

    monkeypatch.setattr(mil_portfolio, "get_open_positions",
                         lambda ctx, phase: {"positions": [], "position_count": 0})
    monkeypatch.setattr(mil_portfolio, "get_daily_pnl",
                         lambda ctx, phase: {"realized_pnl_pct": 0.0, "realized_pnl_krw": 0, "total_eval_amt": 10_000_000})
    monkeypatch.setattr(mil_market, "get_market_context",
                         lambda ctx, phase: {"kospi": 2470.0, "foreign_net_buy_krw": -500_000_000_000})
    monkeypatch.setattr(mil_market, "get_intraday_index_candles",
                         lambda ctx, phase: {"candles": [{"open": 2530.0, "high": 2531.0, "low": 2465.0, "close": 2470.0}]})
    monkeypatch.setattr(mil_market, "get_sector_breadth",
                         lambda ctx, phase: {"sectors": [{"advancers": 100, "decliners": 600}]})

    orch = make_orchestrator(tmp_path)
    orch._drift_detector = FakeDriftDetector({
        "drift_judgment": "REGIME_SHIFT", "reason": "지수 급락 + 외인 대량매도", "metrics": {}, "triggered": [],
        "new_status": "RED", "risk_guidance_delta": {"buy_confidence_threshold": 88, "risk_per_trade_pct": 0.15, "max_positions": 2},
        "drift_state": {"date": "2026-06-09", "last_trigger_time": {}, "today_caution_count": 3, "daily_lite_llm_calls": 1},
    })
    orch._trading_agent = FakeTradingAgent({"action": "NO_TRADE", "proposals": [], "reason": "관망"})
    rescan_calls = []
    monkeypatch.setattr(orch, "run_scan_v3", lambda: rescan_calls.append(1))

    regime = {
        "status": "YELLOW", "regime": "SIDEWAYS", "confidence": 50,
        "risk_guidance": {"max_positions": 4, "buy_confidence_threshold": 75,
                           "risk_per_trade_pct": 0.35, "min_trading_value_krw": 1_000_000_000},
        "drift_triggers": [], "cooldown_minutes": 60, "max_daily_triggers": 3,
        "timestamp": "2026-06-09T08:45:00",
    }
    last_regime_path = tmp_path / "last_regime.json"
    last_regime_path.write_text(json.dumps(regime), encoding="utf-8")
    (tmp_path / "watchlist.json").write_text(json.dumps({"watchlist": []}), encoding="utf-8")

    monkeypatch.setattr("orchestrator_v3._LAST_REGIME_PATH", last_regime_path)
    monkeypatch.setattr("orchestrator_v3._DRIFT_STATE_PATH", tmp_path / "drift_state.json")
    monkeypatch.setattr("orchestrator_v3._WATCHLIST_PATH", tmp_path / "watchlist.json")

    orch.run_intraday_v3()

    assert rescan_calls == [1]
    updated = json.loads(last_regime_path.read_text(encoding="utf-8"))
    assert updated["status"] == "RED"
    assert updated["risk_guidance"]["max_positions"] == 2


# ── BUY proposal → Safety Layer ──────────────────────────────────────────────

class FakeOrderManager:
    def __init__(self):
        self.buy_calls = []

    def execute_buy(self, order):
        self.buy_calls.append(order)
        from codes.order_manager import ExecutionResult
        return ExecutionResult(
            success=True, ticker=order.ticker, side="BUY", quantity=order.quantity,
            executed_price=order.price, order_no="ORD1", timestamp="2026-06-09T09:30:00",
        )


class FakeRiskOfficer:
    def __init__(self, raise_violation=False):
        self._raise = raise_violation
        self.checked = []

    def check(self, proposal, portfolio_state):
        self.checked.append(proposal)
        if self._raise:
            raise RiskViolation(rule="MAX_POSITIONS", detail="포지션 한도 초과")


class FakePositionSizer:
    def calculate_flexible_stop(self, ticker, entry_price, atr, total_capital, support_stop_price=None, risk_pct_override=None):
        from codes.position_sizer import SizingResult
        stop_loss_price = support_stop_price or entry_price * 0.95
        quantity = 10
        return SizingResult(
            ticker=ticker, entry_price=entry_price, stop_loss_price=stop_loss_price,
            quantity=quantity, risk_amount=(entry_price - stop_loss_price) * quantity,
            risk_pct=0.3, position_value=entry_price * quantity, atr_used=atr,
            stop_method="support",
        )


class FakeTelegramApproval:
    def __init__(self, approved=True):
        self._approved = approved
        self.requests = []

    def request_approval(self, req):
        self.requests.append(req)
        from broker.telegram import ApprovalResult
        return ApprovalResult(approved=self._approved, request_id="REQ1")


def test_process_v3_buy_proposal_executes_order_when_approved(tmp_path, monkeypatch):
    orch = make_orchestrator(tmp_path)
    orch._risk_officer = FakeRiskOfficer()
    orch._position_sizer = FakePositionSizer()
    orch._telegram = FakeTelegramApproval(approved=True)
    orch._order_manager = FakeOrderManager()
    monkeypatch.setattr(orch, "build_portfolio_state", lambda: object())
    monkeypatch.setattr(orch, "_estimate_atr", lambda ticker: 1500.0)

    class FakeSnapshot:
        current_price = 70000.0

    monkeypatch.setattr(orch, "_market_data", type("MD", (), {"get_snapshot": staticmethod(lambda t: FakeSnapshot())})())

    proposal = {"ticker": "005930", "side": "BUY", "confidence": 82, "stop_loss_price": 68000, "reason": "강한 회복"}
    result = orch._process_v3_buy_proposal(proposal)

    assert result["action"] == "BUY_EXECUTED"
    assert orch._order_manager.buy_calls[0].ticker == "005930"


def test_process_v3_buy_proposal_blocked_by_risk_officer(tmp_path, monkeypatch):
    orch = make_orchestrator(tmp_path)
    orch._risk_officer = FakeRiskOfficer(raise_violation=True)
    orch._position_sizer = FakePositionSizer()
    orch._telegram = FakeTelegramApproval(approved=True)
    orch._order_manager = FakeOrderManager()
    monkeypatch.setattr(orch, "build_portfolio_state", lambda: object())
    monkeypatch.setattr(orch, "_estimate_atr", lambda ticker: 1500.0)

    class FakeSnapshot:
        current_price = 70000.0

    monkeypatch.setattr(orch, "_market_data", type("MD", (), {"get_snapshot": staticmethod(lambda t: FakeSnapshot())})())

    proposal = {"ticker": "005930", "side": "BUY", "confidence": 82, "stop_loss_price": 68000, "reason": "강한 회복"}
    result = orch._process_v3_buy_proposal(proposal)

    assert result["action"] == "BLOCKED"
    assert orch._order_manager.buy_calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_orchestrator_v3.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'orchestrator_v3'`

- [ ] **Step 3: Implement orchestrator_v3.py**

```python
"""MQK v3 오케스트레이터 - 단일 TradingAgent + MIL + v2 Safety Layer.

v2의 RED hard block을 제거한다. RegimeAgent가 매일 아침 risk_guidance/drift_triggers를
선언하면, RegimeDriftDetector가 장중 5분마다 무료로 감시한다(Tier2). 드리프트가 발동하면
Lite LLM(Tier3)을 호출해 risk_guidance를 조정하거나 레짐을 전환한다. TradingAgent는
Phase별로 MIL 16개 도구를 사용해 proposal을 생성하고, v2 Safety Layer
(RiskOfficer/PositionSizer/Telegram/OrderManager)가 이를 코드로 강제한다.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from agents.drift_detector import RegimeDriftDetector
from agents.regime_agent import load_last_regime, save_last_regime, _LAST_REGIME_PATH
from agents.trading_agent import TradingAgent, TradingPhase, build_context
from broker.kis_mcp_client import KISMCPClient
from broker.telegram import ApprovalRequest
from codes.order_manager import OrderRequest
from codes.risk_officer import RiskViolation, TradeProposal
from config.settings import RISK
from market_intelligence import market as mil_market
from market_intelligence import portfolio as mil_portfolio
from market_intelligence.base import MILContext
from market_intelligence.cache import MILCache
from market_intelligence.circuit_breaker import CircuitBreaker
from orchestrator import MQKOrchestrator

logger = logging.getLogger("mqk_v3")

_DATA_DIR = Path(__file__).parent / "data"
_DRIFT_STATE_PATH = _DATA_DIR / "drift_state.json"
_WATCHLIST_PATH = _DATA_DIR / "watchlist.json"


def _default_drift_state(date: str) -> dict:
    return {"date": date, "last_trigger_time": {}, "today_caution_count": 0, "daily_lite_llm_calls": 0}


def load_drift_state(path: Path = _DRIFT_STATE_PATH, today: str | None = None) -> dict:
    today = today or datetime.now().strftime("%Y-%m-%d")
    if not path.exists():
        return _default_drift_state(today)
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("date") != today:
        return _default_drift_state(today)
    return state


def save_drift_state(state: dict, path: Path = _DRIFT_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_watchlist(path: Path = _WATCHLIST_PATH) -> list[str]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("watchlist", [])


def save_watchlist(watchlist: list[str], path: Path = _WATCHLIST_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"watchlist": watchlist, "updated_at": datetime.now().isoformat()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class MQKOrchestratorV3(MQKOrchestrator):
    """v2 Safety Layer를 재사용하는 v3 아젠틱 오케스트레이터."""

    def __init__(self, kis_api=None, mil: MILContext | None = None, dry_run_orders: bool | None = None):
        super().__init__(kis_api=kis_api, dry_run_orders=dry_run_orders)
        self._mil = mil or MILContext(
            kis_api=kis_api,
            mcp_client=KISMCPClient(),
            cache=MILCache(),
            circuit_breaker=CircuitBreaker(),
        )
        self._drift_detector = RegimeDriftDetector()
        self._trading_agent = TradingAgent(mil=self._mil)

    # ── 08:45 PREMARKET ──────────────────────────────────────────────────────
    def run_premarket_v3(self) -> dict:
        market_status = self.run_premarket()  # v2 RegimeAgent.judge() 재사용
        regime = self._last_regime
        save_last_regime(regime, path=_LAST_REGIME_PATH)
        save_drift_state(_default_drift_state(self._today), path=_DRIFT_STATE_PATH)
        self._mil.circuit_breaker.reset()

        regime_dict = _regime_to_dict(regime)
        context = self._build_context(TradingPhase.PREMARKET, regime_dict, "STABLE", watchlist=[])
        review = self._trading_agent.run(TradingPhase.PREMARKET, context)
        self._save_json("premarket_review.json", review)
        return market_status

    # ── 09:10 / 11:00 / 14:00 SCAN ────────────────────────────────────────────
    def run_scan_v3(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH) or {}
        drift_state = load_drift_state(path=_DRIFT_STATE_PATH, today=self._today)
        context = self._build_context(TradingPhase.SCAN, regime, _drift_status(drift_state), watchlist=[])
        result = self._trading_agent.run(TradingPhase.SCAN, context)
        save_watchlist(result.get("watchlist", []), path=_WATCHLIST_PATH)
        self._save_json("scan_v3.json", result)
        return result

    # ── */5 09:20~15:00 INTRADAY (드리프트 체크 + 매수/청산 판단) ──────────────
    def run_intraday_v3(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH)
        if regime is None:
            logger.warning("[INTRADAY] last_regime.json 없음 — premarket을 먼저 실행하세요.")
            return {"action": "NO_TRADE", "reason": "no_regime"}

        drift_state = load_drift_state(path=_DRIFT_STATE_PATH, today=self._today)
        snapshot = self._collect_drift_snapshot()
        drift_result = self._drift_detector.check(
            market_snapshot=snapshot,
            drift_triggers=regime.get("drift_triggers", []),
            cooldown_minutes=regime.get("cooldown_minutes", 60),
            max_daily_triggers=regime.get("max_daily_triggers", 3),
            drift_state=drift_state,
            current_status=regime.get("status", "YELLOW"),
            current_regime=regime,
        )
        save_drift_state(drift_result["drift_state"], path=_DRIFT_STATE_PATH)

        risk_guidance = dict(regime.get("risk_guidance", {}))
        drift_judgment = drift_result["drift_judgment"]
        if drift_judgment in {"CAUTION", "REGIME_SHIFT"}:
            risk_guidance.update(drift_result.get("risk_guidance_delta", {}))
            self._notify_drift(drift_result)

        if drift_judgment == "REGIME_SHIFT":
            regime["status"] = drift_result["new_status"]
            regime["risk_guidance"] = risk_guidance
            save_last_regime_dict(regime, path=_LAST_REGIME_PATH)
            self.run_scan_v3()

        watchlist = load_watchlist(path=_WATCHLIST_PATH)
        context = self._build_context(
            TradingPhase.INTRADAY, regime, drift_judgment,
            watchlist=watchlist, risk_guidance_override=risk_guidance,
        )
        result = self._trading_agent.run(TradingPhase.INTRADAY, context)
        self._handle_proposals(result.get("proposals", []))
        self._save_json(f"intraday_v3_{datetime.now().strftime('%H%M%S')}.json", result)
        return result

    # ── 15:30 CLOSE ────────────────────────────────────────────────────────────
    def run_close_v3(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH) or {}
        drift_state = load_drift_state(path=_DRIFT_STATE_PATH, today=self._today)
        context = self._build_context(TradingPhase.CLOSE, regime, _drift_status(drift_state), watchlist=[])
        result = self._trading_agent.run(TradingPhase.CLOSE, context)
        self._handle_sell_proposals(result.get("sell_proposals", []))
        self._save_json("close_v3.json", result)
        self.run_close_review()  # v2 거래 복기 재사용
        return result

    # ── 17:00 MARKET_CLOSE ───────────────────────────────────────────────────
    def run_market_close_v3(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH) or {}
        context = self._build_context(TradingPhase.MARKET_CLOSE, regime, "STABLE", watchlist=[])
        result = self._trading_agent.run(TradingPhase.MARKET_CLOSE, context)
        self._save_json("market_close_snapshot.json", result.get("market_close_snapshot", {}))
        self._save_json("close_market_read.json", result.get("close_market_read", {}))
        self._save_json("next_day_premarket_context.json", result.get("next_day_premarket_context", {}))
        return result

    # ── 컨텍스트/스냅샷 빌더 ───────────────────────────────────────────────────

    def _build_context(
        self,
        phase: TradingPhase,
        regime: dict,
        drift_status: str,
        watchlist: list[str],
        risk_guidance_override: dict | None = None,
    ) -> dict:
        risk_guidance = risk_guidance_override or regime.get("risk_guidance", {})
        positions = mil_portfolio.get_open_positions(self._mil, phase.value)
        daily_pnl = mil_portfolio.get_daily_pnl(self._mil, phase.value)

        max_positions = risk_guidance.get("max_positions", RISK.max_positions)
        positions_left = max(max_positions - positions.get("position_count", 0), 0)
        realized_loss_pct = abs(min(daily_pnl.get("realized_pnl_pct", 0.0), 0.0))
        daily_loss_remaining = max(RISK.max_daily_loss_pct - realized_loss_pct, 0.0)

        return build_context(
            phase=phase,
            trading_date=self._today,
            regime={
                "status": regime.get("status"),
                "regime": regime.get("regime"),
                "confidence": regime.get("confidence"),
            },
            drift_status=drift_status,
            risk_guidance=risk_guidance,
            portfolio_snapshot=positions,
            daily_pnl=daily_pnl,
            risk_budget_remaining={
                "positions_left": positions_left,
                "daily_loss_remaining_pct": daily_loss_remaining,
            },
            watchlist=watchlist,
            context_timestamps={
                "regime": regime.get("timestamp", ""),
                "now": datetime.now().isoformat(),
            },
        )

    def _collect_drift_snapshot(self) -> dict:
        market_ctx = mil_market.get_market_context(self._mil, "INTRADAY")
        candles = mil_market.get_intraday_index_candles(self._mil, "INTRADAY").get("candles", [])
        sectors = mil_market.get_sector_breadth(self._mil, "INTRADAY").get("sectors", [])

        kospi_current = market_ctx.get("kospi", 0.0)
        kospi_open = candles[0]["open"] if candles else kospi_current
        lows = [c["low"] for c in candles if c.get("low")]
        kospi_low = min(lows) if lows else kospi_current

        return {
            "kospi_current": kospi_current,
            "kospi_open": kospi_open,
            "kospi_low": kospi_low,
            "foreign_net_buy_bln": market_ctx.get("foreign_net_buy_krw", 0.0) / 1e8,
            "advance_count": sum(s.get("advancers", 0) for s in sectors),
            "decline_count": sum(s.get("decliners", 0) for s in sectors),
        }

    def _notify_drift(self, drift_result: dict) -> None:
        lines = [
            f"⚠️ *드리프트 감지: {drift_result['drift_judgment']}*",
            f"사유: {drift_result.get('reason', '')}",
        ]
        if drift_result.get("new_status"):
            lines.append(f"새 상태: {drift_result['new_status']}")
        delta = drift_result.get("risk_guidance_delta", {})
        if delta:
            lines.append(f"risk_guidance 조정: {json.dumps(delta, ensure_ascii=False)}")
        try:
            self._telegram.notify("\n".join(lines))
        except Exception as e:
            logger.warning(f"[드리프트 알림] 텔레그램 발송 실패: {e}")

    # ── proposal → Safety Layer ─────────────────────────────────────────────

    def _handle_proposals(self, proposals: list[dict]) -> list[dict]:
        results = []
        for p in proposals:
            if p.get("side") == "BUY":
                results.append(self._process_v3_buy_proposal(p))
            elif p.get("side") == "SELL":
                results.append(self._process_v3_sell_proposal(p))
        return results

    def _handle_sell_proposals(self, proposals: list[dict]) -> list[dict]:
        return [self._process_v3_sell_proposal(p) for p in proposals]

    def _process_v3_buy_proposal(self, proposal: dict) -> dict:
        ticker = proposal["ticker"]
        stop_loss_price = proposal["stop_loss_price"]
        snapshot = self._market_data.get_snapshot(ticker)
        entry_price = snapshot.current_price
        atr = self._estimate_atr(ticker)
        portfolio_state = self.build_portfolio_state()

        sizing = self._position_sizer.calculate_flexible_stop(
            ticker=ticker,
            entry_price=entry_price,
            atr=atr,
            total_capital=portfolio_state.total_capital,
            support_stop_price=stop_loss_price,
        )

        trade_proposal = TradeProposal(
            ticker=ticker,
            theme="V3",
            entry_price=entry_price,
            stop_loss_price=sizing.stop_loss_price,
            quantity=sizing.quantity,
        )

        try:
            self._risk_officer.check(trade_proposal, portfolio_state)
        except RiskViolation as e:
            logger.warning(f"[V3 RISK BLOCK] {ticker}: {e}")
            return {"action": "BLOCKED", "ticker": ticker, "reason": str(e)}

        approval_request_id = None
        if RISK.require_telegram_approval:
            approval_req = ApprovalRequest(
                ticker=ticker, name=ticker, decision="BUY",
                entry_price=entry_price,
                stop_loss_price=sizing.stop_loss_price,
                quantity=sizing.quantity,
                risk_pct=sizing.risk_pct,
                confidence=proposal.get("confidence", 0),
                reason=proposal.get("reason", ""),
                counter_argument="",
            )
            approval = self._telegram.request_approval(approval_req)
            approval_request_id = approval.request_id
            if not approval.approved:
                return {"action": "REJECTED", "ticker": ticker, "reason": "텔레그램 거부"}

        order = OrderRequest(
            ticker=ticker, name=ticker, side="BUY",
            quantity=sizing.quantity,
            price=entry_price,
            stop_loss_price=sizing.stop_loss_price,
            reason=proposal.get("reason", ""),
            confidence=proposal.get("confidence", 0),
            approval_request_id=approval_request_id,
            strategy_type=proposal.get("setup", "TREND"),
        )
        result = self._order_manager.execute_buy(order)
        return {"action": "BUY_EXECUTED", "ticker": ticker, "success": result.success}

    def _process_v3_sell_proposal(self, proposal: dict) -> dict:
        ticker = proposal["ticker"]
        open_pos = self._journal.get_open_positions()
        match = next((p for p in open_pos if p["ticker"] == ticker), None)
        if match is None:
            return {"action": "SKIP", "ticker": ticker, "reason": "보유하지 않은 종목"}

        snapshot = self._market_data.get_snapshot(ticker)
        order = OrderRequest(
            ticker=ticker, name=match.get("name", ticker), side="SELL",
            quantity=int(match["quantity"]),
            price=snapshot.current_price,
            stop_loss_price=float(match["stop_loss_price"]),
            reason=proposal.get("reason", ""),
            confidence=100,
        )
        result = self._order_manager.execute_sell(order)
        return {"action": "SELL_EXECUTED", "ticker": ticker, "success": result.success}


def _regime_to_dict(regime) -> dict:
    return {
        "status": regime.status.value,
        "regime": regime.regime.value,
        "confidence": regime.confidence,
        "risk_guidance": regime.risk_guidance,
        "drift_triggers": regime.drift_triggers,
        "cooldown_minutes": regime.cooldown_minutes,
        "max_daily_triggers": regime.max_daily_triggers,
    }


def save_last_regime_dict(regime: dict, path: Path = _LAST_REGIME_PATH) -> None:
    """REGIME_SHIFT 후 갱신된 레짐 dict를 last_regime.json에 다시 저장한다."""
    payload = dict(regime)
    payload["timestamp"] = datetime.now().isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _drift_status(drift_state: dict) -> str:
    if drift_state.get("today_caution_count", 0) > 0:
        return "CAUTION"
    return "STABLE"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_orchestrator_v3.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite to confirm no regressions**

Run: `.venv/bin/pytest tests/ -v`
Expected: All PASS

- [ ] **Step 6: Create run_schedule_v3.py**

```python
#!/usr/bin/env python3
"""
MQK v3 자동 운영 진입점 (PM2 cron_restart로 각 단계별 실행)

MQK_PHASE 환경변수:
  premarket    - 08:45 레짐 판단 + risk_guidance/drift_triggers 생성 + 보유종목 점검
  scan         - 09:10 / 11:00 / 14:00 watchlist 생성/갱신
  intraday     - 09:20~15:00, */5 드리프트 체크 + 매수/청산 proposal
  close        - 15:30 청산 판단 + 거래 복기
  market_close - 17:00 장마감 분석 + 다음날 prior 생성

휴장일 가드는 v2와 동일하게 codes/market_calendar의 캐시를 사용한다.
"""
from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("mqk_v3_schedule")

PHASE = os.environ.get("MQK_PHASE", "")


def _guard_trading_day() -> None:
    from codes.market_calendar import check_trading_day, read_cached_trading_day

    cached = read_cached_trading_day()
    if cached is None:
        logger.info("[휴장일 가드] 캐시 없음 — check_trading_day() 호출")
        cached = check_trading_day()

    if not cached:
        logger.info("[휴장일 가드] 오늘은 휴장일 — 작동 중단")
        sys.exit(0)


def _make_orchestrator():
    from broker.kis_api import KISApi
    from orchestrator_v3 import MQKOrchestratorV3

    kis = KISApi()
    return MQKOrchestratorV3(kis_api=kis)


def run_premarket() -> None:
    _guard_trading_day()
    orch = _make_orchestrator()
    result = orch.run_premarket_v3()
    logger.info(f"[v3 PREMARKET] {result['regime']} ({result['status']})")


def run_scan() -> None:
    _guard_trading_day()
    orch = _make_orchestrator()
    result = orch.run_scan_v3()
    logger.info(f"[v3 SCAN] watchlist={result.get('watchlist', [])}")


def run_intraday() -> None:
    _guard_trading_day()
    orch = _make_orchestrator()
    result = orch.run_intraday_v3()
    logger.info(f"[v3 INTRADAY] action={result.get('action')}")


def run_close() -> None:
    _guard_trading_day()
    orch = _make_orchestrator()
    result = orch.run_close_v3()
    logger.info(f"[v3 CLOSE] sell_proposals={len(result.get('sell_proposals', []))}")


def run_market_close() -> None:
    _guard_trading_day()
    orch = _make_orchestrator()
    orch.run_market_close_v3()
    logger.info("[v3 MARKET_CLOSE] 분석 완료")


_RUNNERS = {
    "premarket": run_premarket,
    "scan": run_scan,
    "intraday": run_intraday,
    "close": run_close,
    "market_close": run_market_close,
}

if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        logger.info(f"[DRY RUN] MQK_PHASE={PHASE!r}")
        sys.exit(0)

    if PHASE not in _RUNNERS:
        logger.error(
            f"MQK_PHASE={PHASE!r} 미지원. "
            f"premarket | scan | intraday | close | market_close 중 하나를 설정하세요."
        )
        sys.exit(1)

    _RUNNERS[PHASE]()
```

- [ ] **Step 7: Add v3 PM2 schedule to ecosystem.config.cjs**

`ecosystem.config.cjs`의 `apps` 배열 끝(`mqk-telegram-news` 앞 또는 뒤)에 다음 항목들을 추가합니다.
**기존 `mqk-premarket`/`mqk-scan`/`mqk-intraday`/`mqk-close` 항목은 그대로 둡니다** —
v2/v3 동시 운영 충돌을 피하려면, v3로 전환 시 기존 v2 4개 앱을 PM2에서 `pm2 stop`/`delete`
하거나 `cron_restart`를 주석 처리해야 합니다 (이 작업은 사용자가 운영 전환 시점에 수동 결정).

```javascript
    {
      name: "mqk-v3-premarket",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "premarket" },
      cron_restart: "45 23 * * 0-4",  // UTC 23:45 = KST 08:45 (일~목 UTC = 월~금 KST)
      autorestart: false,
    },
    {
      name: "mqk-v3-scan",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "scan" },
      cron_restart: "10 0,2,5 * * 1-5",  // UTC 00:10/02:10/05:10 = KST 09:10/11:00/14:00
      autorestart: false,
    },
    {
      name: "mqk-v3-intraday",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "intraday" },
      cron_restart: "*/5 0-5 * * 1-5",  // UTC 00:00~05:55 = KST 09:00~14:55, 5분 간격
      autorestart: false,
    },
    {
      name: "mqk-v3-close",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "close" },
      cron_restart: "30 6 * * 1-5",  // UTC 06:30 = KST 15:30
      autorestart: false,
    },
    {
      name: "mqk-v3-market-close",
      script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
      args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v3.py",
      cwd: "/mnt/c/Users/gocho/MQK-v2",
      env: { MQK_PHASE: "market_close" },
      cron_restart: "0 8 * * 1-5",  // UTC 08:00 = KST 17:00
      autorestart: false,
    },
```

> **참고**: `mqk-v3-intraday`의 KST 09:00~14:55 범위는 스펙의 09:20~15:00과 약간 다릅니다.
> PM2 cron 표현식은 시(時) 단위로 KST↔UTC 변환 시 09:20처럼 분 단위 시작점을 표현하기 어려우므로,
> 09:00부터 시작해 `run_intraday_v3()` 내부에서 매번 동일한 로직(드리프트 체크 + proposal 평가)을
> 수행하도록 했습니다. 09:00~09:15 사이 호출은 watchlist가 비어있으면 자연히 NO_TRADE를 반환합니다.
> 15:00~15:55 구간은 `mqk-v3-close`(15:30)와 겹치지 않도록 운영 중 스케줄을 조정하세요.

- [ ] **Step 8: Commit**

```bash
cd /mnt/c/Users/gocho/MQK-v2
git add orchestrator_v3.py run_schedule_v3.py ecosystem.config.cjs tests/test_orchestrator_v3.py
git commit -m "feat(v3): add OrchestratorV3 with PM2 schedule (premarket/scan/intraday/close/market_close)"
```

---
