# MQK v4 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 미네르비니 기반 v3를 대체하는 국장 세력주 + 테마 선도주 단기 스윙 봇 v4를 v3와 병행 운용 가능하게 구축한다.

**Architecture:** v3 코드베이스(broker/, MIL, Safety Layer) 재사용, v4 전용 파일(orchestrator_v4.py, run_schedule_v4.py, prompts/trading_agent_v4/)로 분리 구축. 1차 스크리닝은 코드, 세력/테마 검증 및 청산 판단은 LLM이 담당.

**Tech Stack:** Python 3.12, uv, PM2, KIS API, OpenAI API, pytest

## Global Constraints

- 최대 포지션: 3개 (v3의 4개에서 축소)
- 테스트 실행: `uv run pytest tests/ -x -q`
- 모든 새 파일은 v4 suffix/디렉토리로 분리 (v3 파일 수정 금지)
- 모의투자(paper) 모드로 개발 및 검증
- 커밋 전 반드시 전체 테스트 통과 확인
- 미결 사항 확정값: 분봉=10분, VOLUME_DRY 임계=-40%, PRICE_SIGNAL=당일저점 하향돌파+2배거래대금, 최대포지션=3

---

## 파일 맵

**신규 생성**
```
agents/trading_agent_v4.py                    # TradingPhaseV4, PHASE_TOOLS_V4, TradingAgentV4
orchestrator_v4.py                            # MQKOrchestratorV4
run_schedule_v4.py                            # PM2 진입점
prompts/agents/trading_agent_v4/
  premarket_sejuk.md                          # 장전 상한가 + 장전거래 복합 분석
  premarket.md                                # 레짐 판단 (v3 premarket.md 기반)
  scan.md                                     # 거래대금 폭증 + 세력 검증
  intraday.md                                 # 눌림 진입 + 세력 이탈 감시
  close.md                                    # 신호 기반 청산
  market_close.md                             # 복기 + 다음날 prior
tests/test_mil_screening_v4.py                # get_limit_up_stocks 테스트
tests/test_mil_stock_v4.py                    # get_intraday_volume_trend 테스트
tests/test_trading_agent_v4.py                # TradingAgentV4 구조 테스트
tests/test_orchestrator_v4.py                 # MQKOrchestratorV4 phase 테스트
```

**기존 파일 수정**
```
market_intelligence/screening.py              # get_limit_up_stocks() 추가
market_intelligence/stock.py                  # get_intraday_volume_trend() 추가
ecosystem.config.cjs                          # v4 PM2 앱 추가
```

---

## Task 1: MIL 도구 — get_limit_up_stocks

**Files:**
- Modify: `market_intelligence/screening.py` (끝에 추가)
- Test: `tests/test_mil_screening_v4.py` (신규 생성)

**Interfaces:**
- Produces: `get_limit_up_stocks(ctx: MILContext, phase: str) -> dict`
  - 반환: `{"stocks": [{"ticker": str, "name": str, "change_pct": float, "trading_value_krw": float, "is_limit_up": bool}]}`

- [ ] **Step 1: 테스트 파일 작성**

```python
# tests/test_mil_screening_v4.py
"""get_limit_up_stocks 테스트"""
import pytest
from unittest.mock import MagicMock
from market_intelligence.base import MILContext, ToolFailure
from market_intelligence.screening import get_limit_up_stocks


class StubKisApi:
    def __init__(self, rows):
        self._rows = rows

    def raw_get(self, tr_id, path, params):
        return {"output": self._rows}


def _make_ctx(rows):
    return MILContext(kis_api=StubKisApi(rows))


def test_get_limit_up_stocks_filters_above_25pct():
    rows = [
        {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스",
         "prdy_ctrt": "29.90", "acml_tr_pbmn": "500000000000", "stck_prpr": "100000"},
        {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자",
         "prdy_ctrt": "10.00", "acml_tr_pbmn": "300000000000", "stck_prpr": "80000"},
        {"mksc_shrn_iscd": "035420", "hts_kor_isnm": "NAVER",
         "prdy_ctrt": "27.00", "acml_tr_pbmn": "100000000000", "stck_prpr": "200000"},
    ]
    ctx = _make_ctx(rows)
    result = get_limit_up_stocks(ctx, "PREMARKET_SEJUK")
    stocks = result["stocks"]
    # 25% 이상만 포함 (000660: 29.9%, NAVER: 27%)
    assert len(stocks) == 2
    tickers = [s["ticker"] for s in stocks]
    assert "000660" in tickers
    assert "035420" in tickers
    assert "005930" not in tickers


def test_get_limit_up_stocks_is_limit_up_flag():
    rows = [
        {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스",
         "prdy_ctrt": "29.90", "acml_tr_pbmn": "500000000000", "stck_prpr": "100000"},
        {"mksc_shrn_iscd": "035420", "hts_kor_isnm": "NAVER",
         "prdy_ctrt": "26.00", "acml_tr_pbmn": "100000000000", "stck_prpr": "200000"},
    ]
    ctx = _make_ctx(rows)
    result = get_limit_up_stocks(ctx, "PREMARKET_SEJUK")
    stocks = {s["ticker"]: s for s in result["stocks"]}
    assert stocks["000660"]["is_limit_up"] is True   # 29.9% → 상한가
    assert stocks["035420"]["is_limit_up"] is False  # 26% → 상한가 근접


def test_get_limit_up_stocks_empty_when_no_rows():
    ctx = _make_ctx([])
    result = get_limit_up_stocks(ctx, "PREMARKET_SEJUK")
    assert result["stocks"] == []


def test_get_limit_up_stocks_uses_cache(monkeypatch):
    calls = []
    rows = [{"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK",
             "prdy_ctrt": "29.9", "acml_tr_pbmn": "100000000000", "stck_prpr": "100000"}]
    ctx = _make_ctx(rows)
    original_raw_get = ctx.kis_api.raw_get
    def counting_raw_get(*args, **kwargs):
        calls.append(1)
        return original_raw_get(*args, **kwargs)
    ctx.kis_api.raw_get = counting_raw_get

    get_limit_up_stocks(ctx, "PREMARKET_SEJUK")
    get_limit_up_stocks(ctx, "PREMARKET_SEJUK")
    assert len(calls) == 1  # 캐시 적중
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/test_mil_screening_v4.py -v
```
예상: `ImportError: cannot import name 'get_limit_up_stocks'`

- [ ] **Step 3: screening.py 끝에 함수 추가**

```python
def get_limit_up_stocks(ctx: MILContext, phase: str) -> dict:
    """당일 상한가(29.9%) 또는 상한가 근접(25%↑) 종목 리스트.

    v4 세력주 매매의 핵심 탐지 도구. 등락률 순위 API(FHPST01700000)에서
    change_pct >= 25% 종목을 추출한다. is_limit_up은 29% 이상 여부.
    """

    def fetch():
        raw = ctx.kis_api.raw_get(
            "FHPST01700000",
            "domestic-stock/v1/ranking/fluctuation",
            {
                "fid_cond_mrkt_div_code": "J",
                "fid_cond_scr_div_code": "20170",
                "fid_input_iscd": "0000",
                "fid_rank_sort_cls_code": "0",   # 등락률 높은 순
                "fid_input_cnt_1": "0",
                "fid_prc_cls_code": "1",
                "fid_input_price_1": "",
                "fid_input_price_2": "",
                "fid_vol_cnt": "",
                "fid_trgt_cls_code": "0",
                "fid_trgt_exls_cls_code": "0",
                "fid_div_cls_code": "0",
                "fid_rsfl_rate1": "",
                "fid_rsfl_rate2": "",
            },
        )
        rows = raw.get("output", []) or []
        stocks = []
        for row in rows:
            change_pct = _to_float(row.get("prdy_ctrt"))
            if change_pct < 25.0:
                continue
            trading_value = _to_float(row.get("acml_tr_pbmn"))
            if trading_value == 0:
                price = _to_float(row.get("stck_prpr"))
                volume = _to_float(row.get("acml_vol"))
                trading_value = price * volume
            stocks.append({
                "ticker": row.get("mksc_shrn_iscd"),
                "name": row.get("hts_kor_isnm"),
                "change_pct": change_pct,
                "trading_value_krw": trading_value,
                "is_limit_up": change_pct >= 29.0,
            })
        return {"stocks": stocks}

    return ctx.cached_call("get_limit_up_stocks", phase, {}, fetch)
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/test_mil_screening_v4.py -v
```
예상: 4개 PASS

- [ ] **Step 5: 전체 테스트 통과 확인**

```bash
uv run pytest tests/ -x -q
```
예상: 311 passed (기존) + 4 passed = 315 passed

- [ ] **Step 6: 커밋**

```bash
git add market_intelligence/screening.py tests/test_mil_screening_v4.py
git commit -m "feat: add get_limit_up_stocks MIL tool for v4 세력주 탐지"
```

---

## Task 2: MIL 도구 — get_intraday_volume_trend

**Files:**
- Modify: `market_intelligence/stock.py` (끝에 추가)
- Test: `tests/test_mil_stock_v4.py` (신규 생성)

**Interfaces:**
- Produces: `get_intraday_volume_trend(ctx: MILContext, phase: str, ticker: str) -> dict`
  - 반환: `{"ticker": str, "trend": "INCREASING"|"STABLE"|"DECLINING"|"DRY", "recent_avg_krw": float, "prev_avg_krw": float, "decline_pct": float, "signal": "VOLUME_DRY"|None}`
  - 기준: 최근 3봉 평균 거래대금 vs 직전 3봉 평균. -40% 이하 → signal="VOLUME_DRY"

- [ ] **Step 1: 테스트 파일 작성**

```python
# tests/test_mil_stock_v4.py
"""get_intraday_volume_trend 테스트"""
import pytest
from market_intelligence.base import MILContext
from market_intelligence.stock import get_intraday_volume_trend


class StubKisApi:
    def __init__(self, candles):
        self._candles = candles

    def raw_get(self, tr_id, path, params):
        # 10분봉 조회 응답 형식
        return {"output2": [
            {"stck_bsop_date": c["date"], "stck_cntg_hour": c["time"],
             "acml_tr_pbmn": str(int(c["vol"]))}
            for c in self._candles
        ]}


def _make_ctx(candles):
    return MILContext(kis_api=StubKisApi(candles))


def _candle(vol):
    return {"date": "20260623", "time": "0900", "vol": vol}


def test_volume_dry_when_recent_drops_40pct():
    # 직전 3봉 평균 1000억, 최근 3봉 평균 500억 → -50% → VOLUME_DRY
    candles = [
        _candle(400_0000_0000), _candle(500_0000_0000), _candle(600_0000_0000),  # 최근 3봉
        _candle(900_0000_0000), _candle(1000_0000_0000), _candle(1100_0000_0000),  # 직전 3봉
    ]
    ctx = _make_ctx(candles)
    result = get_intraday_volume_trend(ctx, "INTRADAY", "000660")
    assert result["signal"] == "VOLUME_DRY"
    assert result["trend"] == "DECLINING"


def test_stable_when_volume_maintained():
    # 직전 3봉 평균 1000억, 최근 3봉 평균 950억 → -5% → STABLE
    candles = [
        _candle(900_0000_0000), _candle(950_0000_0000), _candle(1000_0000_0000),
        _candle(950_0000_0000), _candle(1000_0000_0000), _candle(1050_0000_0000),
    ]
    ctx = _make_ctx(candles)
    result = get_intraday_volume_trend(ctx, "INTRADAY", "000660")
    assert result["signal"] is None
    assert result["trend"] == "STABLE"


def test_increasing_when_recent_higher():
    candles = [
        _candle(1200_0000_0000), _candle(1300_0000_0000), _candle(1400_0000_0000),
        _candle(800_0000_0000),  _candle(900_0000_0000),  _candle(1000_0000_0000),
    ]
    ctx = _make_ctx(candles)
    result = get_intraday_volume_trend(ctx, "INTRADAY", "000660")
    assert result["trend"] == "INCREASING"
    assert result["signal"] is None


def test_returns_ticker_in_result():
    candles = [_candle(100_0000_0000)] * 6
    ctx = _make_ctx(candles)
    result = get_intraday_volume_trend(ctx, "INTRADAY", "005930")
    assert result["ticker"] == "005930"
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/test_mil_stock_v4.py -v
```
예상: `ImportError: cannot import name 'get_intraday_volume_trend'`

- [ ] **Step 3: stock.py 끝에 함수 추가**

```python
def get_intraday_volume_trend(ctx: MILContext, phase: str, ticker: str) -> dict:
    """10분봉 기준 거래대금 트렌드 분석 — v4 세력 이탈 감지 핵심 도구.

    최근 3봉 평균 거래대금 vs 직전 3봉 평균을 비교한다.
    decline_pct <= -40% 이면 signal="VOLUME_DRY" (세력 이탈 신호).
    """

    def fetch():
        raw = ctx.kis_api.raw_get(
            "FHKST03010230",
            "domestic-stock/v1/quotations/inquire-time-itemconclusion",
            {
                "fid_cond_mrkt_div_code": "J",
                "fid_input_iscd": ticker,
                "fid_hour_cls_code": "10",  # 10분봉
                "fid_pw_data_incu_yn": "N",
            },
        )
        candles = raw.get("output2", []) or []
        vols = []
        for c in candles[:6]:
            v = float(c.get("acml_tr_pbmn") or 0)
            vols.append(v)

        if len(vols) < 6:
            return {
                "ticker": ticker,
                "trend": "STABLE",
                "recent_avg_krw": 0,
                "prev_avg_krw": 0,
                "decline_pct": 0.0,
                "signal": None,
            }

        recent_avg = sum(vols[:3]) / 3
        prev_avg = sum(vols[3:6]) / 3
        decline_pct = ((recent_avg - prev_avg) / prev_avg * 100) if prev_avg > 0 else 0.0

        if decline_pct <= -40.0:
            trend = "DECLINING"
            signal = "VOLUME_DRY"
        elif decline_pct <= -10.0:
            trend = "DECLINING"
            signal = None
        elif decline_pct >= 20.0:
            trend = "INCREASING"
            signal = None
        else:
            trend = "STABLE"
            signal = None

        return {
            "ticker": ticker,
            "trend": trend,
            "recent_avg_krw": recent_avg,
            "prev_avg_krw": prev_avg,
            "decline_pct": round(decline_pct, 1),
            "signal": signal,
        }

    return ctx.cached_call("get_intraday_volume_trend", phase, {"ticker": ticker}, fetch)
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/test_mil_stock_v4.py -v
```
예상: 4개 PASS

- [ ] **Step 5: 전체 테스트 통과 확인**

```bash
uv run pytest tests/ -x -q
```

- [ ] **Step 6: 커밋**

```bash
git add market_intelligence/stock.py tests/test_mil_stock_v4.py
git commit -m "feat: add get_intraday_volume_trend MIL tool for v4 세력 이탈 감지"
```

---

## Task 3: v4 프롬프트 — premarket_sejuk (장전 상한가 분석)

**Files:**
- Create: `prompts/agents/trading_agent_v4/premarket_sejuk.md`

- [ ] **Step 1: 디렉토리 생성 및 프롬프트 작성**

```bash
mkdir -p prompts/agents/trading_agent_v4
```

`prompts/agents/trading_agent_v4/premarket_sejuk.md` 내용:

```markdown
# TradingAgent v4 — PREMARKET_SEJUK (장전 상한가 세력 검증)

## Role
08:45 장전. 어제 상한가를 기록한 종목과 장전 시간외 거래 데이터를 결합해
오늘 진입 후보를 확정한다. 가짜 세력(개미 몰림, 뉴스성 단발)을 걸러내는 게 핵심이다.

## Inputs
- `limit_up_stocks`: 전일 상한가(25%↑) 종목 리스트 (ticker, name, change_pct, trading_value_krw)
- `premarket_movers`: 장전 예상 체결가 / 장전 거래대금
- `regime`: 현재 레짐 (RED면 전체 스킵)

## 판단 흐름

1. 레짐이 RED면 모든 후보를 제외하고 빈 watchlist 반환
2. 각 상한가 종목에 대해:
   a. `get_news_stock`으로 밤새 추가 뉴스 확인 (촉매 지속성)
   b. 장전 갭업/보합 유지 → 세력 의지 있음 → 후보 유지
   c. 장전 갭다운 -3%↑ or 장전 거래대금 폭발적 매도 → 세력 이탈 → 제외
3. 통과 종목에 setup=LIMIT_UP_PULLBACK, cluster=서브테마명 부여

## 세력 vs 개미 판별 기준 (우선순위 순)
1. 거래대금 연속성: 어제 하루만 터진 것인가 vs 2~3일 연속인가
2. 장전 시가 흐름: 갭업/보합 = 세력 의지. 갭다운 = 이탈
3. 뉴스 촉매: 정책/수주/산업 구조 변화 = 강함. 단순 테마 편승 = 약함
4. 기관·외인: 참여 있으면 더 신뢰. 없어도 진입 가능. 파는 건 위험

## 최종 출력

```json
{
  "next_action": "final",
  "action": "WATCHLIST_UPDATE",
  "watchlist": ["000660"],
  "candidates": [
    {
      "ticker": "000660",
      "setup": "LIMIT_UP_PULLBACK",
      "confidence": 82,
      "cluster": "반도체_메모리코어",
      "premarket_gap_pct": -1.2,
      "sejuk_reason": "장전 갭다운 -1.2%로 소폭 조정, 거래대금 어제 포함 2일 연속, 뉴스 촉매(AI수주) 유효"
    }
  ],
  "reason": ""
}
```

## Forbidden
- 레짐 RED에서 후보 등록 금지
- 장전 갭다운 -3% 이상 종목 등록 금지
- 거래대금 하루만 터진 단발성 종목 등록 금지 (개미 몰림)
- `get_news_stock` 확인 없이 후보 확정 금지
```

- [ ] **Step 2: 커밋**

```bash
git add prompts/agents/trading_agent_v4/premarket_sejuk.md
git commit -m "feat: add v4 premarket_sejuk prompt (장전 상한가 세력 검증)"
```

---

## Task 4: v4 프롬프트 — scan (거래대금 폭증 + 세력 검증)

**Files:**
- Create: `prompts/agents/trading_agent_v4/scan.md`
- Create: `prompts/agents/trading_agent_v4/premarket.md` (레짐 판단, v3 기반)

- [ ] **Step 1: scan.md 작성**

`prompts/agents/trading_agent_v4/scan.md` 내용:

```markdown
# TradingAgent v4 — SCAN (거래대금 폭증 세력주 + 테마 선도주 탐지)

## Role
장중 3회(09:17/11:17/13:17) + 마감 전 1회(15:00). 두 가지 유형의 종목을 찾는다:
1. **VOLUME_SURGE_LEADER**: 당일 거래대금 5배↑ + 10%↑ 강한 양봉 + 테마 내 거래대금 1위
2. **THEME_CATALYST**: 강한 뉴스 촉매 + 테마 내 거래대금 1위 + 당일 5%↑

2등주는 쓰레기다. 테마 내 거래대금 1위만 논한다.

## Inputs
- `regime`, `risk_guidance`
- `watchlist`: 장전 premarket_sejuk에서 확정된 상한가 후보 (이미 주입됨)
- `volume_surge_candidates`: 코드가 사전 탐지한 거래대금 폭증 종목 리스트

## 판단 흐름

1. `get_news_market`으로 오늘 강한 촉매 테마 파악
2. `get_theme_candidates`로 테마별 거래대금 집중도 확인
3. `volume_surge_candidates` 검증:
   - `get_news_stock`으로 촉매 강도 확인 (정책/수주/산업 변화 vs 단순 테마 편승)
   - 거래대금이 오늘만인지 vs 어제부터 붙기 시작한 것인지 확인 (`get_ohlcv`)
   - 테마 내 거래대금 1위인지 확인
4. 통과 종목: setup + cluster + role 부여

## 세력 vs 개미 구분
- **세력 신호**: 거래대금 2~3일 연속 증가 OR 오늘 5배↑ + 강한 촉매
- **개미 신호**: 오늘만 터짐 + 촉매 약함 + 기관/외인 매도 중

## 출력 형식

```json
{
  "next_action": "final",
  "action": "WATCHLIST_UPDATE",
  "watchlist": ["000660", "034730"],
  "candidates": [
    {
      "ticker": "000660",
      "setup": "VOLUME_SURGE_LEADER|THEME_CATALYST",
      "confidence": 85,
      "cluster": "반도체_메모리코어",
      "role": "본류 대장주",
      "sejuk_reason": "거래대금 3일 연속, AI서버 수주 촉매, 테마 내 1위"
    }
  ],
  "reason": ""
}
```

## Forbidden
- 거래대금 오늘 하루만 터진 종목 등록 (촉매 약하면 금지)
- 테마 내 2등주·3등주 등록
- `get_news_stock` 없이 촉매 미검증 종목 등록
- 거래대금 100억 미만 종목
```

- [ ] **Step 2: premarket.md 작성 (v3 premarket.md 내용 기반으로 v4 디렉토리에 복사)**

```bash
cp prompts/agents/trading_agent/premarket.md prompts/agents/trading_agent_v4/premarket.md
```

v4/premarket.md 상단 주석만 변경:

```markdown
# TradingAgent v4 — PREMARKET (레짐 판단)
# v3와 동일 역할. 레짐 + risk_guidance 생성.
# 이하 v3 premarket.md와 동일 내용 유지.
```

- [ ] **Step 3: 커밋**

```bash
git add prompts/agents/trading_agent_v4/scan.md prompts/agents/trading_agent_v4/premarket.md
git commit -m "feat: add v4 scan/premarket prompts (세력주 + 테마 선도주)"
```

---

## Task 5: v4 프롬프트 — intraday (눌림 진입 + 세력 이탈 감시)

**Files:**
- Create: `prompts/agents/trading_agent_v4/intraday.md`
- Create: `prompts/agents/trading_agent_v4/close.md`
- Create: `prompts/agents/trading_agent_v4/market_close.md`

- [ ] **Step 1: intraday.md 작성**

`prompts/agents/trading_agent_v4/intraday.md` 내용:

```markdown
# TradingAgent v4 — INTRADAY (눌림 진입 + 세력 이탈 감시)

## Role
09:20~14:50, 10분 간격. 두 가지 역할:
1. **진입 판단**: watchlist 종목이 눌림 타이밍인지
2. **세력 이탈 감시**: 보유 종목에서 청산 신호 발생 여부

## Inputs
- `watchlist`: LIMIT_UP_PULLBACK / VOLUME_SURGE_LEADER / THEME_CATALYST 후보 (cluster/role 포함)
- `portfolio.positions`: 현재 보유 종목
- `regime`, `risk_guidance`

## 진입 판단 기준 (매수)

### LIMIT_UP_PULLBACK (상한가 눌림)
- 시가 대비 -3~8% 눌림 구간에 있는가
- 눌림 중 거래대금이 유지되는가 (급감하면 세력 이탈, 진입 금지)
- `get_intraday_candles`로 분봉 패턴 확인

### VOLUME_SURGE_LEADER / THEME_CATALYST
- 당일 고점 대비 -3~7% 눌림
- 거래대금이 감소하면서 눌리는가 (좋음) vs 거래대금 동반 하락 (나쁨)

**공통 금지**: 하락 중 거래대금 폭증 = 세력 매도. 절대 진입 금지.

## 세력 이탈 감시 (청산 신호)

보유 종목마다 10분마다 확인:

| 신호 | 조건 | 행동 |
|---|---|---|
| VOLUME_DRY | 최근 3봉 거래대금 평균 -40% 이하 | SELL proposal (다음날 시가) |
| FLOW_REVERSAL | 기관+외인 동시 순매도 2일 연속 | SELL proposal (당일) |
| THEME_FADE | 테마 뉴스 소멸 + 섹터 거래대금 감소 | SELL proposal (다음날 시가) |
| PRICE_SIGNAL | 당일 저점 하향돌파 + 해당봉 거래대금 ≥ 직전10봉 평균 2배 | SELL proposal (즉시) |
| LIMIT_UP_FAIL | 장중 상한가 근접 후 밀리면서 거래대금 폭발 | SELL proposal (즉시) |

**중요**: 거래대금 없이 그냥 밀리는 건 손절 안 한다. 세력이 파는 증거가 있을 때만 청산.

## 도구 사용 순서
1. `get_watchlist_intraday_snapshot`으로 watchlist 전체 스냅샷
2. `get_intraday_volume_trend`로 보유 종목별 거래대금 트렌드 확인
3. 진입 후보는 `get_intraday_candles`로 눌림 깊이/패턴 확인
4. 이탈 신호 발생 시 `get_sector_investor_flow`로 섹터 수급 교차 확인

## 출력 형식

```json
{
  "next_action": "final",
  "action": "BUY|SELL|HOLD|NO_TRADE",
  "proposals": [
    {
      "ticker": "000660",
      "side": "BUY",
      "setup": "LIMIT_UP_PULLBACK",
      "confidence": 80,
      "stop_loss_price": 95000,
      "reason": "시가 대비 -4.2% 눌림, 거래대금 유지, 세력 지지선(당일저점) 유효"
    },
    {
      "ticker": "005930",
      "side": "SELL",
      "sell_type": "VOLUME_DRY",
      "reason": "최근 3봉 거래대금 직전 대비 -52%, 세력 이탈 신호"
    }
  ],
  "reason": ""
}
```

## sell_type 종류
- `VOLUME_DRY` / `FLOW_REVERSAL` / `THEME_FADE` / `PRICE_SIGNAL` / `LIMIT_UP_FAIL`

## Forbidden
- 거래대금 없이 하락하는 종목 손절 (거래대금 동반 필수)
- HOLD이면서 BUY proposal 포함 금지
- stop_loss 없는 BUY proposal 금지
- 물타기(Averaging down) 절대 금지
```

- [ ] **Step 2: close.md 작성**

`prompts/agents/trading_agent_v4/close.md` 내용:

```markdown
# TradingAgent v4 — CLOSE (장마감 전 청산 판단)

## Role
15:18. 오늘 보유 종목 중 내일 들고 갈 이유가 없는 종목 청산.

## 판단 기준
- VOLUME_DRY: 오후 거래대금이 오전 대비 -50% 이상 급감
- THEME_FADE: 오늘 오후 테마 뉴스 소멸 확인
- 홀딩 3일차: 목적 달성 여부와 무관하게 세력 피로도 점검
- D_DAY 도달: setup이 THEME_CATALYST인 경우 이벤트 당일 청산 검토

## 출력 형식

```json
{
  "next_action": "final",
  "action": "SELL|NO_TRADE",
  "sell_proposals": [
    {
      "ticker": "000660",
      "side": "SELL",
      "sell_type": "VOLUME_DRY",
      "reason": "오후 거래대금 오전 대비 -55%, 내일 반등 근거 없음"
    }
  ],
  "reason": ""
}
```
```

- [ ] **Step 3: market_close.md 복사 (v3와 동일)**

```bash
cp prompts/agents/trading_agent/market_close.md prompts/agents/trading_agent_v4/market_close.md
```

- [ ] **Step 4: 커밋**

```bash
git add prompts/agents/trading_agent_v4/intraday.md \
        prompts/agents/trading_agent_v4/close.md \
        prompts/agents/trading_agent_v4/market_close.md
git commit -m "feat: add v4 intraday/close/market_close prompts (신호 기반 청산)"
```

---

## Task 6: TradingAgentV4 기반 구조

**Files:**
- Create: `agents/trading_agent_v4.py`
- Test: `tests/test_trading_agent_v4.py`

**Interfaces:**
- Produces:
  - `TradingPhaseV4(str, Enum)`: PREMARKET_SEJUK, PREMARKET, SCAN, INTRADAY, CLOSE, MARKET_CLOSE
  - `PHASE_TOOLS_V4: dict[TradingPhaseV4, list[str]]`
  - `TradingAgentV4` 클래스: `run(phase, context) -> dict`

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_trading_agent_v4.py
"""TradingAgentV4 구조 테스트"""
import pytest
from agents.trading_agent_v4 import TradingPhaseV4, PHASE_TOOLS_V4, TradingAgentV4


def test_all_phases_defined():
    phases = list(TradingPhaseV4)
    names = {p.value for p in phases}
    assert "PREMARKET_SEJUK" in names
    assert "PREMARKET" in names
    assert "SCAN" in names
    assert "INTRADAY" in names
    assert "CLOSE" in names
    assert "MARKET_CLOSE" in names


def test_premarket_sejuk_has_limit_up_tool():
    tools = PHASE_TOOLS_V4[TradingPhaseV4.PREMARKET_SEJUK]
    assert "get_limit_up_stocks" in tools
    assert "get_premarket_movers" in tools
    assert "get_news_stock" in tools


def test_intraday_has_volume_trend_tool():
    tools = PHASE_TOOLS_V4[TradingPhaseV4.INTRADAY]
    assert "get_intraday_volume_trend" in tools
    assert "get_watchlist_intraday_snapshot" in tools


def test_scan_has_theme_and_news_tools():
    tools = PHASE_TOOLS_V4[TradingPhaseV4.SCAN]
    assert "get_theme_candidates" in tools
    assert "get_news_market" in tools
    assert "get_volume_surge" in tools


def test_trading_agent_v4_instantiates():
    agent = TradingAgentV4()
    assert agent is not None
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/test_trading_agent_v4.py -v
```

- [ ] **Step 3: trading_agent_v4.py 작성**

```python
# agents/trading_agent_v4.py
"""MQK v4 TradingAgent — 국장 세력주 스나이퍼.

v3 TradingAgent를 재사용하되 v4 전용 Phase/도구/프롬프트를 등록한다.
"""
from __future__ import annotations
from enum import Enum
from agents.trading_agent import TradingAgent, ModelTier


class TradingPhaseV4(str, Enum):
    PREMARKET_SEJUK = "PREMARKET_SEJUK"  # 08:45 장전 상한가 세력 검증
    PREMARKET       = "PREMARKET"        # 09:03 레짐 판단
    SCAN            = "SCAN"             # 09:17/11:17/13:17/15:00 종목 스캔
    INTRADAY        = "INTRADAY"         # 09:20~14:50 진입 + 세력 이탈 감시
    CLOSE           = "CLOSE"            # 15:18 마감 청산
    MARKET_CLOSE    = "MARKET_CLOSE"     # 17:00 복기


_PHASE_PROMPT_NAMES_V4: dict[TradingPhaseV4, str] = {
    TradingPhaseV4.PREMARKET_SEJUK: "trading_agent_v4/premarket_sejuk",
    TradingPhaseV4.PREMARKET:       "trading_agent_v4/premarket",
    TradingPhaseV4.SCAN:            "trading_agent_v4/scan",
    TradingPhaseV4.INTRADAY:        "trading_agent_v4/intraday",
    TradingPhaseV4.CLOSE:           "trading_agent_v4/close",
    TradingPhaseV4.MARKET_CLOSE:    "trading_agent_v4/market_close",
}

PHASE_TOOLS_V4: dict[TradingPhaseV4, list[str]] = {
    TradingPhaseV4.PREMARKET_SEJUK: [
        "get_limit_up_stocks", "get_premarket_movers",
        "get_news_stock", "get_news_market", "get_ohlcv",
    ],
    TradingPhaseV4.PREMARKET: [
        "get_market_context", "get_sector_breadth", "get_news_market",
        "get_sector_investor_flow", "get_foreign_institution_rank",
    ],
    TradingPhaseV4.SCAN: [
        "get_market_context", "get_theme_candidates", "get_news_market",
        "get_volume_surge", "get_foreign_institution_rank",
        "get_news_stock", "get_ohlcv", "get_stock_status",
        "get_sector_investor_flow", "get_top_movers",
        "psearch_title", "psearch_result",
        "kw_psearch_title", "kw_psearch_result",
    ],
    TradingPhaseV4.INTRADAY: [
        "get_watchlist_intraday_snapshot", "get_intraday_candles",
        "get_intraday_volume_trend", "get_realtime_price",
        "get_sector_investor_flow", "get_foreign_institution_rank",
        "get_intraday_investor_rank", "get_volume_surge",
        "get_news_stock", "get_flow", "get_stock_status",
        "get_orderbook",
    ],
    TradingPhaseV4.CLOSE: [
        "get_open_positions", "get_realtime_price",
        "get_intraday_volume_trend", "get_news_stock",
        "get_sector_investor_flow",
    ],
    TradingPhaseV4.MARKET_CLOSE: [
        "get_market_context", "get_sector_breadth", "get_news_market",
        "get_open_positions", "get_daily_pnl",
        "get_ohlcv", "get_sector_investor_flow",
    ],
}

_TIER_MAP: dict[TradingPhaseV4, ModelTier] = {
    TradingPhaseV4.PREMARKET_SEJUK: ModelTier.REASONING,
    TradingPhaseV4.PREMARKET:       ModelTier.FAST,
    TradingPhaseV4.SCAN:            ModelTier.REASONING,
    TradingPhaseV4.INTRADAY:        ModelTier.LITE,
    TradingPhaseV4.CLOSE:           ModelTier.FAST,
    TradingPhaseV4.MARKET_CLOSE:    ModelTier.FAST,
}


class TradingAgentV4:
    """v4 전용 TradingAgent 래퍼. v3 TradingAgent 내부 로직을 재사용."""

    def __init__(self, max_steps: int = 15):
        self._agent = TradingAgent(
            phase_prompt_names=_PHASE_PROMPT_NAMES_V4,  # type: ignore[arg-type]
            phase_tools=PHASE_TOOLS_V4,                  # type: ignore[arg-type]
            tier_map=_TIER_MAP,                          # type: ignore[arg-type]
            max_steps=max_steps,
        )

    def run(self, phase: TradingPhaseV4, context: dict) -> dict:
        return self._agent.run(phase, context)  # type: ignore[arg-type]
```

- [ ] **Step 4: build_context + TradingAgent v4 override 수정**

`build_context` (agents/trading_agent.py:162) 에 `allowed_tools` 파라미터 추가:

```python
def build_context(
    phase: TradingPhase,
    trading_date: str,
    regime: dict,
    drift_status: str,
    risk_guidance: dict,
    portfolio_snapshot: dict,
    daily_pnl: dict,
    risk_budget_remaining: dict,
    watchlist: list[Any] | None = None,
    context_timestamps: dict | None = None,
    exploration_policy: dict | None = None,
    allowed_tools: list[str] | None = None,   # ← 추가 (v4 override용)
) -> dict:
    watchlist = watchlist or []
    watchlist_tickers = _normalize_tickers([], watchlist)
    return {
        "current_phase": phase.value if hasattr(phase, "value") else str(phase),
        "trading_date": trading_date,
        "regime": regime,
        "drift_status": drift_status,
        "risk_guidance": risk_guidance,
        "portfolio": portfolio_snapshot,
        "daily_pnl": daily_pnl,
        "risk_budget_remaining": risk_budget_remaining,
        "watchlist": watchlist,
        "watchlist_tickers": watchlist_tickers,
        "exploration_policy": exploration_policy or {},
        "allowed_tools": allowed_tools if allowed_tools is not None else list(PHASE_TOOLS[phase]),
        "context_timestamps": context_timestamps or {},
    }
```

`TradingAgent.__init__` 수정 (agents/trading_agent.py:195 근처):

```python
def __init__(
    self,
    llm: LLMClient | None = None,
    max_steps: int = 15,
    phase_prompt_names: dict | None = None,   # v4 override
    phase_tools: dict | None = None,           # v4 override
    tier_map: dict | None = None,              # v4 override
) -> None:
    self._llm = llm or LLMClient()
    self._max_steps = max_steps
    self._phase_prompt_names = phase_prompt_names or _PHASE_PROMPT_NAMES
    self._phase_tools = phase_tools or PHASE_TOOLS
    self._tier_map_override = tier_map
```

`run()` 메서드 내 프롬프트 로드 (204줄 근처):
```python
system_prompt = inject_agent(self._phase_prompt_names[phase])
```

`_execute_tool()` 내 도구 검증 (408줄 근처):
```python
# PHASE_TOOLS[phase] → self._phase_tools.get(phase, PHASE_TOOLS.get(phase, []))
if tool_name not in self._phase_tools.get(phase, PHASE_TOOLS.get(phase, [])):
```

`_tier_for_phase()`:
```python
def _tier_for_phase(self, phase) -> ModelTier:
    if self._tier_map_override and phase in self._tier_map_override:
        return self._tier_map_override[phase]
    if phase == TradingPhase.SCAN:
        return ModelTier.REASONING
    if phase in {TradingPhase.PREMARKET, TradingPhase.CLOSE, TradingPhase.MARKET_CLOSE}:
        return ModelTier.FAST
    return ModelTier.LITE
```

- [ ] **Step 6: 전체 테스트 통과 확인**

```bash
uv run pytest tests/ -x -q
```
기존 311개 + v4 5개 = 316개 이상 PASS

- [ ] **Step 7: 커밋**

```bash
git add agents/trading_agent.py agents/trading_agent_v4.py tests/test_trading_agent_v4.py
git commit -m "feat: add TradingAgentV4 with v4 phases/tools/prompts registration"
```

---

## Task 7: orchestrator_v4.py

**Files:**
- Create: `orchestrator_v4.py`
- Test: `tests/test_orchestrator_v4.py`

**Interfaces:**
- Produces: `MQKOrchestratorV4` 클래스
  - `run_premarket_sejuk_v4() -> dict`
  - `run_premarket_v4() -> dict`
  - `run_scan_v4() -> dict`
  - `run_intraday_v4() -> dict`
  - `run_close_v4() -> dict`
  - `run_market_close_v4() -> dict`

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_orchestrator_v4.py
"""MQKOrchestratorV4 phase 테스트"""
import pytest
from unittest.mock import MagicMock, patch
from orchestrator_v4 import MQKOrchestratorV4


class StubKisApi:
    def get_balance(self):
        return {"output2": [{"ord_psbl_cash": "50000000"}]}


def _make_orch():
    return MQKOrchestratorV4(kis_api=StubKisApi())


def test_orchestrator_v4_instantiates():
    orch = _make_orch()
    assert orch is not None


def test_run_premarket_sejuk_returns_dict(monkeypatch):
    orch = _make_orch()
    monkeypatch.setattr(
        "orchestrator_v4.MQKOrchestratorV4._run_agent",
        lambda self, phase, ctx: {"action": "WATCHLIST_UPDATE", "watchlist": [], "candidates": []},
    )
    result = orch.run_premarket_sejuk_v4()
    assert isinstance(result, dict)
    assert "watchlist" in result


def test_run_intraday_v4_skips_when_no_regime(monkeypatch):
    orch = _make_orch()
    monkeypatch.setattr("orchestrator_v4.load_last_regime", lambda path: None)
    result = orch.run_intraday_v4()
    assert result.get("action") == "NO_TRADE"
    assert "stale_regime" in result.get("reason", "")


def test_max_positions_is_3():
    from orchestrator_v4 import MAX_POSITIONS_V4
    assert MAX_POSITIONS_V4 == 3
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/test_orchestrator_v4.py -v
```
예상: ImportError

- [ ] **Step 3: orchestrator_v4.py 작성**

```python
# orchestrator_v4.py
"""MQK v4 오케스트레이터 — 국장 세력주 스나이퍼.

v3 코드베이스(Safety Layer, MIL, broker)를 재사용하되
Phase/프롬프트/철학을 전면 교체한다.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from agents.regime_agent import load_last_regime, save_last_regime
from agents.trading_agent_v4 import TradingAgentV4, TradingPhaseV4
from agents.trading_agent import build_context
from broker.kis_api import KISApi
from broker.kiwoom_api import KiwoomApi
from broker.telegram import TelegramApproval
from codes.order_manager import OrderManager, OrderRequest
from codes.position_sizer import PositionSizer
from codes.risk_officer import PortfolioState, RiskOfficer, TradeProposal
from codes.trade_journal import TradeJournal
from config.settings import RISK
from market_intelligence import market as mil_market
from market_intelligence import portfolio as mil_portfolio
from market_intelligence import screening as mil_screening
from market_intelligence.base import MILContext, ToolFailure
from market_intelligence.cache import MILCache
from market_intelligence.circuit_breaker import CircuitBreaker

logger = logging.getLogger("mqk_v4")

_DATA_DIR = Path(__file__).parent / "data"
_WATCHLIST_PATH_V4 = _DATA_DIR / "watchlist_v4.json"
_LAST_REGIME_PATH = _DATA_DIR / "last_regime.json"  # v3와 레짐 공유

MAX_POSITIONS_V4 = 3  # v3(4개)보다 적게 — 세력주 집중 투자


class MQKOrchestratorV4:
    """v4 오케스트레이터. v3 Safety Layer 전부 재사용."""

    def __init__(self, kis_api: KISApi, kiwoom_api: KiwoomApi | None = None):
        cache = MILCache()
        breaker = CircuitBreaker()
        self._mil = MILContext(kis_api=kis_api, kiwoom_api=kiwoom_api,
                               cache=cache, circuit_breaker=breaker)
        self._kis_api = kis_api
        self._agent = TradingAgentV4()
        self._today = datetime.now().strftime("%Y-%m-%d")

        # v2 Safety Layer 재사용
        self._journal = TradeJournal()
        self._risk = RiskOfficer()
        self._sizer = PositionSizer()
        self._order_mgr = OrderManager(kis_api=kis_api)
        self._telegram = TelegramApproval()

    def _run_agent(self, phase: TradingPhaseV4, context: dict) -> dict:
        return self._agent.run(phase, context)

    def _build_context_v4(self, phase: TradingPhaseV4, regime: dict,
                           watchlist: list[dict]) -> dict:
        """v4용 컨텍스트 생성. v3 build_context 재사용."""
        try:
            positions = mil_portfolio.get_open_positions(self._mil, phase.value)
            daily_pnl = mil_portfolio.get_daily_pnl(self._mil, phase.value)
        except ToolFailure:
            positions = {"positions": [], "position_count": 0}
            daily_pnl = {"realized_pnl_pct": 0.0, "realized_pnl_krw": 0.0}

        position_count = positions.get("position_count", 0)
        positions_left = max(MAX_POSITIONS_V4 - position_count, 0)

        risk_guidance = regime.get("risk_guidance", {})
        return build_context(
            phase=phase,  # type: ignore[arg-type]
            trading_date=self._today,
            regime={
                "status": regime.get("status"),
                "regime": regime.get("regime"),
                "confidence": regime.get("confidence"),
            },
            drift_status="STABLE",
            risk_guidance=risk_guidance,
            portfolio_snapshot=positions,
            daily_pnl=daily_pnl,
            risk_budget_remaining={
                "positions_left": positions_left,
                "monitoring_slots": min(6, max(positions_left, 4)),
                "daily_loss_remaining_pct": RISK.max_daily_loss_pct,
            },
            watchlist=watchlist,
        )

    def _save_watchlist_v4(self, candidates: list[dict]) -> None:
        import json
        _WATCHLIST_PATH_V4.parent.mkdir(parents=True, exist_ok=True)
        with open(_WATCHLIST_PATH_V4, "w", encoding="utf-8") as f:
            json.dump(candidates, f, ensure_ascii=False, indent=2)

    def _load_watchlist_v4(self) -> list[dict]:
        import json
        if not _WATCHLIST_PATH_V4.exists():
            return []
        try:
            with open(_WATCHLIST_PATH_V4, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    # ── 08:45 장전 상한가 세력 검증 ────────────────────────────────────────
    def run_premarket_sejuk_v4(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH) or {}

        # 코드: 전일 상한가 종목 추출 + 장전 데이터 주입
        try:
            limit_up = mil_screening.get_limit_up_stocks(
                self._mil, TradingPhaseV4.PREMARKET_SEJUK.value
            )
        except ToolFailure:
            limit_up = {"stocks": []}

        try:
            from market_intelligence import screening as scr
            premarket = scr.get_premarket_movers(
                self._mil, TradingPhaseV4.PREMARKET_SEJUK.value
            )
        except ToolFailure:
            premarket = {"movers": []}

        context = self._build_context_v4(
            TradingPhaseV4.PREMARKET_SEJUK, regime, watchlist=[]
        )
        context["limit_up_stocks"] = limit_up.get("stocks", [])
        context["premarket_movers"] = premarket.get("movers", [])

        result = self._run_agent(TradingPhaseV4.PREMARKET_SEJUK, context)

        candidates = result.get("candidates", [])
        if candidates:
            self._save_watchlist_v4(candidates)
            logger.info(f"[v4 PREMARKET_SEJUK] 진입 후보 {len(candidates)}개 → watchlist_v4.json")
        else:
            logger.info("[v4 PREMARKET_SEJUK] 통과 후보 없음")

        return result

    # ── 09:03 레짐 판단 ────────────────────────────────────────────────────
    def run_premarket_v4(self) -> dict:
        # v3 regime_agent 재사용
        from agents.regime_agent import RegimeAgent
        agent = RegimeAgent()
        regime = agent.run(self._mil, session_type="PREMARKET_REGIME")
        save_last_regime(regime, path=_LAST_REGIME_PATH)
        logger.info(f"[v4 PREMARKET] {regime.get('regime')} ({regime.get('status')})")
        return regime

    # ── 09:17/11:17/13:17/15:00 스캔 ──────────────────────────────────────
    def run_scan_v4(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH) or {}
        watchlist = self._load_watchlist_v4()

        # 코드: 거래대금 폭증 종목 사전 탐지
        try:
            volume_surge = mil_screening.get_volume_surge(
                self._mil, TradingPhaseV4.SCAN.value
            )
        except ToolFailure:
            volume_surge = {}

        context = self._build_context_v4(TradingPhaseV4.SCAN, regime, watchlist=watchlist)
        context["volume_surge_candidates"] = volume_surge.get("surge_top", [])

        result = self._run_agent(TradingPhaseV4.SCAN, context)

        new_candidates = result.get("candidates", [])
        if new_candidates:
            existing = {e["ticker"]: e for e in watchlist}
            for c in new_candidates:
                existing[c["ticker"]] = c
            self._save_watchlist_v4(list(existing.values()))
            logger.info(f"[v4 SCAN] watchlist={[c['ticker'] for c in existing.values()]}")
        return result

    # ── 09:20~14:50 장중 ───────────────────────────────────────────────────
    def run_intraday_v4(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH)
        if regime is None or str(regime.get("timestamp", ""))[:10] != self._today:
            logger.warning("[v4 INTRADAY] 당일 레짐 없음 — 스킵")
            return {"action": "NO_TRADE", "reason": "stale_regime"}

        watchlist = self._load_watchlist_v4()
        context = self._build_context_v4(TradingPhaseV4.INTRADAY, regime, watchlist=watchlist)
        result = self._run_agent(TradingPhaseV4.INTRADAY, context)

        self._handle_proposals_v4(result.get("proposals", []))
        logger.info(f"[v4 INTRADAY] action={result.get('action')} reason={result.get('reason','')[:80]}")
        return result

    def _handle_proposals_v4(self, proposals: list[dict]) -> None:
        """v3 _handle_proposals + _handle_sell_proposals 재사용."""
        buy_proposals = [p for p in proposals if str(p.get("side","")).upper() == "BUY"]
        sell_proposals = [p for p in proposals if str(p.get("side","")).upper() == "SELL"]

        for p in buy_proposals:
            try:
                proposal = TradeProposal(
                    ticker=p["ticker"],
                    side="BUY",
                    confidence=p.get("confidence", 70),
                    setup=p.get("setup", "VOLUME_SURGE_LEADER"),
                    stop_loss_price=p.get("stop_loss_price"),
                    reason=p.get("reason", ""),
                )
                # RiskOfficer → PositionSizer → Telegram → OrderManager (v3와 동일 흐름)
                risk_result = self._risk.check(proposal, PortfolioState())
                if risk_result.approved:
                    qty = self._sizer.size(proposal, PortfolioState())
                    req = OrderRequest(ticker=proposal.ticker, side="BUY", qty=qty)
                    self._order_mgr.execute(req)
            except Exception as e:
                logger.warning(f"[v4 BUY] {p.get('ticker')} 실패: {e}")

        for p in sell_proposals:
            try:
                req = OrderRequest(ticker=p["ticker"], side="SELL", qty=0, sell_all=True)
                self._order_mgr.execute(req)
                logger.info(f"[v4 SELL] {p['ticker']} sell_type={p.get('sell_type')}")
            except Exception as e:
                logger.warning(f"[v4 SELL] {p.get('ticker')} 실패: {e}")

    # ── 15:18 마감 청산 ────────────────────────────────────────────────────
    def run_close_v4(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH) or {}
        context = self._build_context_v4(TradingPhaseV4.CLOSE, regime, watchlist=[])
        result = self._run_agent(TradingPhaseV4.CLOSE, context)

        for p in result.get("sell_proposals", []):
            try:
                req = OrderRequest(ticker=p["ticker"], side="SELL", qty=0, sell_all=True)
                self._order_mgr.execute(req)
            except Exception as e:
                logger.warning(f"[v4 CLOSE SELL] {p.get('ticker')}: {e}")

        logger.info(f"[v4 CLOSE] sell_proposals={len(result.get('sell_proposals', []))}")
        return result

    # ── 17:00 복기 ─────────────────────────────────────────────────────────
    def run_market_close_v4(self) -> dict:
        regime = load_last_regime(path=_LAST_REGIME_PATH) or {}
        context = self._build_context_v4(TradingPhaseV4.MARKET_CLOSE, regime, watchlist=[])
        result = self._run_agent(TradingPhaseV4.MARKET_CLOSE, context)
        logger.info("[v4 MARKET_CLOSE] 복기 완료")
        return result
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/test_orchestrator_v4.py -v
uv run pytest tests/ -x -q
```

- [ ] **Step 5: 커밋**

```bash
git add orchestrator_v4.py tests/test_orchestrator_v4.py
git commit -m "feat: add MQKOrchestratorV4 with 6 phases (국장 세력주 스나이퍼)"
```

---

## Task 8: run_schedule_v4.py + PM2 등록

**Files:**
- Create: `run_schedule_v4.py`
- Modify: `ecosystem.config.cjs` (v4 앱 추가)

- [ ] **Step 1: run_schedule_v4.py 작성**

```python
#!/usr/bin/env python3
"""MQK v4 자동 운영 진입점.

MQK_PHASE 환경변수 (KST):
  premarket_sejuk - 08:45 장전 상한가 + 장전거래 복합 분석
  premarket       - 09:03 레짐 판단
  scan            - 09:17/11:17/13:17/15:00 종목 스캔
  intraday        - 09:20~14:50 */10 진입 + 세력 이탈 감시
  close           - 15:18 마감 청산
  market_close    - 17:00 복기 + 다음날 prior
"""
from __future__ import annotations
import logging
import os
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mqk_v4")

PHASE = os.environ.get("MQK_PHASE", "")

_PHASE_WINDOWS: dict[str, tuple[str, str] | list[tuple[str, str]]] = {
    "premarket_sejuk": ("08:40", "09:00"),
    "premarket":       [("09:00", "09:10"), ("10:55", "11:10"), ("12:55", "13:10")],
    "scan":            ("09:10", "15:05"),
    "intraday":        ("09:15", "15:05"),
    "close":           ("15:15", "15:28"),
    "market_close":    ("16:55", "17:30"),
}


def _guard_time_window() -> bool:
    if os.environ.get("MQK_FORCE") == "1":
        return True
    now = datetime.now().strftime("%H:%M")
    window = _PHASE_WINDOWS.get(PHASE)
    if window is None:
        return True
    if isinstance(window, list):
        for start, end in window:
            if start <= now <= end:
                return True
        logger.info(f"[시간창 가드] {PHASE} — 현재 시각 스킵")
        return False
    start, end = window
    if start <= now <= end:
        return True
    logger.info(f"[시간창 가드] {PHASE}는 {start}~{end}에만 실행 — 현재 시각 스킵")
    return False


def _guard_trading_day() -> bool:
    # v3와 동일한 휴장일 체크 재사용
    try:
        from codes.market_data import MarketData
        md = MarketData()
        if not md.is_trading_day():
            logger.info("[v4] 휴장일 — 스킵")
            return False
    except Exception:
        pass
    return True


def _make_orchestrator():
    from broker.kis_api import KISApi
    from orchestrator_v4 import MQKOrchestratorV4
    return MQKOrchestratorV4(kis_api=KISApi())


def run_premarket_sejuk():
    if not _guard_time_window() or not _guard_trading_day():
        return
    orch = _make_orchestrator()
    result = orch.run_premarket_sejuk_v4()
    logger.info(f"[v4 PREMARKET_SEJUK] 후보={len(result.get('candidates', []))}개")


def run_premarket():
    if not _guard_time_window() or not _guard_trading_day():
        return
    orch = _make_orchestrator()
    result = orch.run_premarket_v4()
    logger.info(f"[v4 PREMARKET] {result.get('regime')} ({result.get('status')})")


def run_scan():
    if not _guard_time_window() or not _guard_trading_day():
        return
    orch = _make_orchestrator()
    result = orch.run_scan_v4()
    logger.info(f"[v4 SCAN] watchlist 업데이트")


def run_intraday():
    if not _guard_time_window() or not _guard_trading_day():
        return
    orch = _make_orchestrator()
    result = orch.run_intraday_v4()
    logger.info(f"[v4 INTRADAY] action={result.get('action')}")


def run_close():
    if not _guard_time_window() or not _guard_trading_day():
        return
    orch = _make_orchestrator()
    result = orch.run_close_v4()
    logger.info(f"[v4 CLOSE] sell={len(result.get('sell_proposals', []))}")


def run_market_close():
    if not _guard_time_window() or not _guard_trading_day():
        return
    orch = _make_orchestrator()
    orch.run_market_close_v4()
    logger.info("[v4 MARKET_CLOSE] 완료")


_RUNNERS = {
    "premarket_sejuk": run_premarket_sejuk,
    "premarket":       run_premarket,
    "scan":            run_scan,
    "intraday":        run_intraday,
    "close":           run_close,
    "market_close":    run_market_close,
}

if __name__ == "__main__":
    runner = _RUNNERS.get(PHASE)
    if runner is None:
        logger.error(f"MQK_PHASE='{PHASE}' 미지원. {list(_RUNNERS)} 중 하나를 설정하세요.")
        raise SystemExit(1)
    runner()
```

- [ ] **Step 2: ecosystem.config.cjs에 v4 앱 추가**

기존 v3 앱 블록 아래에 추가:

```javascript
// ── MQK v4 (국장 세력주 스나이퍼) ─────────────────────────────────────────
{
  name: "mqk-v4-premarket-sejuk",
  script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
  args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v4.py",
  cwd: "/mnt/c/Users/gocho/MQK-v2",
  env: { MQK_PHASE: "premarket_sejuk" },
  // KST 08:45 — 장전 상한가 세력 검증
  cron_restart: "45 8 * * 1-5",
  autorestart: false,
},
{
  name: "mqk-v4-premarket",
  script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
  args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v4.py",
  cwd: "/mnt/c/Users/gocho/MQK-v2",
  env: { MQK_PHASE: "premarket" },
  // KST 09:03/11:03/13:03 — 레짐 판단 (v3와 동일)
  cron_restart: "3 9,11,13 * * 1-5",
  autorestart: false,
},
{
  name: "mqk-v4-scan",
  script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
  args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v4.py",
  cwd: "/mnt/c/Users/gocho/MQK-v2",
  env: { MQK_PHASE: "scan" },
  // KST 09:17/11:17/13:17
  cron_restart: "17 9,11,13 * * 1-5",
  autorestart: false,
},
{
  name: "mqk-v4-scan-eod",
  script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
  args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v4.py",
  cwd: "/mnt/c/Users/gocho/MQK-v2",
  env: { MQK_PHASE: "scan" },
  // KST 15:00 — 마감 전 마지막 스캔
  cron_restart: "0 15 * * 1-5",
  autorestart: false,
},
{
  name: "mqk-v4-intraday",
  script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
  args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v4.py",
  cwd: "/mnt/c/Users/gocho/MQK-v2",
  env: { MQK_PHASE: "intraday" },
  // KST 09:20~14:50, 10분 간격
  cron_restart: "*/10 9-14 * * 1-5",
  autorestart: false,
},
{
  name: "mqk-v4-close",
  script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
  args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v4.py",
  cwd: "/mnt/c/Users/gocho/MQK-v2",
  env: { MQK_PHASE: "close" },
  // KST 15:18
  cron_restart: "18 15 * * 1-5",
  autorestart: false,
},
{
  name: "mqk-v4-market-close",
  script: "/mnt/c/Users/gocho/MQK-v2/.venv/bin/python",
  args: "/mnt/c/Users/gocho/MQK-v2/run_schedule_v4.py",
  cwd: "/mnt/c/Users/gocho/MQK-v2",
  env: { MQK_PHASE: "market_close" },
  // KST 17:00
  cron_restart: "0 17 * * 1-5",
  autorestart: false,
},
```

- [ ] **Step 3: 전체 테스트 통과 확인**

```bash
uv run pytest tests/ -x -q
```

- [ ] **Step 4: 커밋**

```bash
git add run_schedule_v4.py ecosystem.config.cjs
git commit -m "feat: add run_schedule_v4 + PM2 ecosystem for v4 (7 apps)"
```

---

## Task 9: v4 PM2 등록 및 통합 검증

**Files:**
- Modify: PM2 런타임 (pm2 start 명령)

- [ ] **Step 1: v4 PM2 앱 등록**

```bash
pm2 start ecosystem.config.cjs --only \
  mqk-v4-premarket-sejuk,mqk-v4-premarket,mqk-v4-scan,mqk-v4-scan-eod,mqk-v4-intraday,mqk-v4-close,mqk-v4-market-close
pm2 save
```

예상: v4 앱 7개 stopped 상태로 등록됨 (cron 대기)

- [ ] **Step 2: premarket_sejuk 수동 실행 검증**

```bash
MQK_FORCE=1 MQK_PHASE=premarket_sejuk uv run python run_schedule_v4.py
```

예상: `[v4 PREMARKET_SEJUK] 후보=N개` 로그 출력 (N >= 0)

- [ ] **Step 3: scan 수동 실행 검증**

```bash
MQK_FORCE=1 MQK_PHASE=scan uv run python run_schedule_v4.py
```

예상: `[v4 SCAN] watchlist 업데이트` + 상세 로그

- [ ] **Step 4: intraday 수동 실행 검증 (NO_TRADE 예상)**

```bash
MQK_FORCE=1 MQK_PHASE=intraday uv run python run_schedule_v4.py
```

예상: `[v4 INTRADAY] action=NO_TRADE or HOLD` (아직 진입 후보 없을 수 있음)

- [ ] **Step 5: pm2 list로 앱 상태 확인**

```bash
pm2 list
```

예상: mqk-v4-* 7개가 stopped 상태 (cron 등록됨)

- [ ] **Step 6: 스펙 업데이트 — 미결 사항 확정값 기록**

`docs/superpowers/specs/2026-06-23-mqk-v4-korean-market-design.md` 9번 미결 사항을 완료로 업데이트:

```markdown
## 9. 확정된 설계 파라미터 (구현 시 결정)

- [x] `get_limit_up_stocks`: FHPST01700000(등락률순위) + change_pct >= 25% 필터
- [x] `get_intraday_volume_trend`: 10분봉, 최근 3봉 vs 직전 3봉 -40% → VOLUME_DRY
- [x] 세력 지지선: 당일 저점 하향돌파 + 해당 봉 거래대금 ≥ 직전10봉 평균 2배
- [x] 최대 포지션: 3개 (v3의 4개에서 축소, 집중 투자)
```

- [ ] **Step 7: 최종 커밋**

```bash
git add docs/superpowers/specs/2026-06-23-mqk-v4-korean-market-design.md
git commit -m "docs: update v4 spec with confirmed design parameters"
```
