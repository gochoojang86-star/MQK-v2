# MQK v3 아젠틱 트레이딩 시스템 설계 명세

**작성일**: 2026-06-09  
**버전**: v3.0  
**상태**: 설계 확정 (구현 전)

---

## 배경 및 동기

### v2의 핵심 문제

```python
# orchestrator.py:296 — 이 한 줄이 모든 문제의 원인
if scanner_mode != "REVERSAL_ONLY" and market_status.get("status") == "RED":
    logger.warning("[SCAN BLOCK] 시장 상태 RED - 신규 후보 스캔 중단")
    return []
```

아침 레짐이 RED면 하루 종일 스캔이 차단된다. SK하이닉스처럼 오전 약세 후 오후에 급회복하는 종목을 포착할 수 없다. 근본 원인은 **코드가 판단**하기 때문이다.

### v2 vs v3 핵심 차이

| | v2 | v3 |
|---|---|---|
| 판단 주체 | 코드 | LLM |
| RED 대응 | `return []` hard block | 기준 강화 후 스캔 계속 |
| 레짐 파라미터 | `settings.py` 하드코딩 | LLM이 `risk_guidance` 선언 |
| 시장 재평가 | 아침 1회 고정 | `drift_triggers` 기반 자동 재검토 |
| 도구 접근 | 파이프라인 고정 | Phase별 MCP 도구 자율 선택 |

### 핵심 철학

> **레짐은 계기판이지 브레이크 페달이 아니다.**  
> RED = 강한 증거 없이는 매매하지 않음 (매매 금지 ≠)

> **LLM이 판단, Code가 안전을 강제.**  
> LLM은 proposal까지. 주문 실행은 Safety Layer 통과 후에만.

---

## 섹션 1: 전체 아키텍처

### 1.1 시스템 구성

```
┌─────────────────────────────────────────────────────┐
│                   TradingAgent                      │
│              (단일 LLM, gpt-5.4)                    │
│                                                     │
│  Phase: PREMARKET / SCAN / INTRADAY / CLOSE         │
│  사전주입: regime, portfolio, risk_budget, watchlist │
└──────────────────┬──────────────────────────────────┘
                   │ MCP 도구 호출 (Phase별 허용 목록)
┌──────────────────▼──────────────────────────────────┐
│           Market Intelligence Layer                 │
│              (16개 래핑 도구)                        │
│                                                     │
│  KIS MCP 서버 ──► 74개 원본 API                     │
│  주문/계좌 도구는 LLM에 노출 안 함 (Safety Layer 전용)│
└──────────────────┬──────────────────────────────────┘
                   │ BUY/SELL proposal
┌──────────────────▼──────────────────────────────────┐
│              v2 Safety Layer (non-negotiable)       │
│                                                     │
│  RiskOfficer ──► PositionSizer ──► Telegram ──► OrderManager
│                                                     │
│  LLM 우회 불가. 코드가 물리적 한계 강제.             │
└─────────────────────────────────────────────────────┘
```

### 1.2 RegimeDriftDetector (병렬 실행)

```
아침 Full LLM
  └─ drift_triggers 생성 (LLM이 스스로 재검토 조건 선언)

매 5분 Code 감시 (무료)
  └─ drift_triggers 임계값 체크 → 발동 시 Lite LLM 호출

Lite LLM (gpt-5.4-mini, 이벤트 트리거)
  └─ STABLE / CAUTION / REGIME_SHIFT 판단
  └─ 하루 최대 3회, 쿨다운 60분
```

### 1.3 LLM SPOF 대응

```
LLM 연속 실패 2회
  → NO_TRADE 모드 진입
  → Telegram 알림: "[MQK] LLM 장애 — 신규 매수 중단"
  → 보유 포지션: 기존 손절가/목표가 기준 code가 자동 처리
  → last_regime.json 캐시 24시간 이내 존재 시
     → SCAN phase까지 허용, buy_confidence_threshold +10% 가산
```

---

## 섹션 2: RegimeJudgment 확장

### 2.1 Full LLM 출력 (PREMARKET 08:45)

```json
{
  "status": "GREEN|YELLOW|RED",
  "regime": "UPTREND|DOWNTREND|SIDEWAYS|THEME_MARKET|POLICY_MARKET|EARNINGS_MARKET|RISK_OFF",
  "confidence": 44,
  "reason": "...",
  "risk_notes": [],
  "opportunity_mode": "NORMAL|SETUP4_PANIC",

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
      "description": "KOSPI 시가 대비 -1.5% 하락 시 RED 전환 가능성"
    },
    {
      "id": "foreign_heavy_sell",
      "metric": "foreign_net_sell_cumulative_bln",
      "threshold": 4000,
      "direction": "above",
      "description": "외국인 누적 순매도 4천억 초과"
    },
    {
      "id": "breadth_collapse",
      "metric": "advance_decline_ratio",
      "threshold": 0.25,
      "direction": "below",
      "description": "등락비율 0.25 미만 → 광범위 하락"
    },
    {
      "id": "recovery_signal",
      "metric": "kospi_recovery_from_low_pct",
      "threshold": 1.0,
      "direction": "above",
      "description": "장중 저점 대비 +1% 회복 → GREEN 재검토"
    }
  ],
  "cooldown_minutes": 60,
  "max_daily_triggers": 3
}
```

### 2.2 RegimeSafetyBounds (코드 클램핑)

LLM이 선언한 `risk_guidance` 값은 아래 범위를 벗어날 수 없다.  
코드가 강제 클램핑. LLM이 극단값을 선언해도 무시된다.

```python
@dataclass(frozen=True)
class RegimeSafetyBounds:
    min_buy_confidence_threshold: float = 65.0
    max_buy_confidence_threshold: float = 95.0
    min_risk_per_trade_pct: float = 0.10
    max_risk_per_trade_pct: float = 0.50   # settings.RISK 값이 천장
    min_positions: int = 1
    max_positions: int = 5                  # settings.RISK 값이 천장
    min_trading_value_krw: int = 5_000_000_000
```

### 2.3 Lite LLM 출력 (drift 발동 시)

```json
{
  "drift_judgment": "CAUTION",
  "reason": "외국인 매도 강도 높으나 지수 낙폭 제한적, RED 전환 기준 미달",
  "new_status": null,
  "risk_guidance_delta": {
    "buy_confidence_threshold": 82,
    "risk_per_trade_pct": 0.25,
    "max_positions": 3
  },
  "updated_triggers": []
}
```

| drift_judgment | 의미 | 처리 |
|---|---|---|
| `STABLE` | 오탐 | 쿨다운만 업데이트 |
| `CAUTION` | 리스크 강화, 레짐 유지 | `risk_guidance_delta` 반영, Telegram 알림 |
| `REGIME_SHIFT` | 레짐 전환 | 새 `status` 교체, SCAN 재실행, Telegram 알림 |

**CAUTION 반복 카운터**: `today_caution_count >= 3` → 자동 REGIME_SHIFT 처리 (한 단계 하향)

---

## 섹션 3: Market Intelligence Layer

### 3.1 설계 원칙

- KIS MCP 서버의 74개 원본 API 중 **주문/계좌 도구는 LLM에 노출하지 않는다.**
- LLM에게는 **16개 목적별 래핑 도구**만 보인다.
- 각 래핑 도구는 내부적으로 KIS API를 호출하고, TTL 캐싱 및 circuit breaker를 처리한다.

### 3.2 도구 목록 (16개, KIS API 문서 기반 확정)

#### 시장관찰 (4개)

| 도구 | KIS API | TR_ID | 반환 핵심 데이터 |
|------|---------|-------|-----------------|
| `get_market_context` | 국내업종 현재지수 + 국내기관_외국인 매매종목가집계 + 프로그램매매 종합현황(일별) | FHPUP02100000 / FHPTJ04400000 / FHPPG04600001 | 코스피/코스닥 지수, 외국인/기관 순매수 대금, 프로그램 매수/매도 |
| `get_sector_breadth` | 국내업종 구분별전체시세 | FHPUP02140000 | 업종별 지수·등락률 + **상승/하락/보합/상한/하한 종목 수** (브레드스 통합) |
| `get_intraday_index_candles` | 국내업종 시간별지수(분) | (업종 분봉) | 지수 분봉 (VWAP 기준선 파악용) |
| `get_news_market` | 종합 시황_공시(제목) | FHKST01011800 | 전체 시황/공시 제목 목록 |

#### 리스크 필터 (2개)

| 도구 | KIS API | TR_ID | 반환 핵심 데이터 |
|------|---------|-------|-----------------|
| `get_stock_status` | 변동성완화장치(VI) 현황 + 주식기본조회 + 국내주식 공매도 일별추이 | FHPST01390000 / CTPF1002R / FHPST04830000 | VI 발동 여부, 관리종목/거래정지/ETF 여부, 공매도 비중 |
| `get_event_schedule` | 예탁원정보(유상증자일정) + 예탁원정보(배당일정) + 예탁원정보(무상증자일정) | HHKDB669100C0 / HHKDB669102C0 / (무상증자) | 권리락일, 청약기간, 배당기준일, 배당금 |

> `get_ohlcv`의 `flng_cls_code`(락구분코드)로 권리락 여부를 보조 확인할 수 있다.

#### 조건검색 (3개)

| 도구 | KIS API | TR_ID | 반환 핵심 데이터 |
|------|---------|-------|-----------------|
| `psearch_title` | 종목조건검색 목록조회 | HHKST03900300 | 저장된 HTS 조건 목록 (seq, 조건명) |
| `psearch_result` | 종목조건검색조회 | HHKST03900400 | 종목코드/명, 현재가, 등락률, 거래량, 거래대금, **52주 고저가**, 시가총액 |
| `get_top_movers` | 거래량순위 + 국내주식 시가총액 상위 | FHPST01710000 / FHPST01740000 | psearch 실패 시 백업 (과열주 편향 경고 플래그 포함) |

#### 종목분석 (5개)

| 도구 | KIS API | TR_ID | 반환 핵심 데이터 |
|------|---------|-------|-----------------|
| `get_ohlcv` | 국내주식기간별시세(일_주_월_년) | FHKST03010100 | **output1**: 현재가, 호가, PER/EPS/PBR, 시가총액, 상한가/하한가 / **output2**: OHLCV × N일 + flng_cls_code |
| `get_realtime_price` | 관심종목(멀티종목) 시세조회 | FHKST11300006 | 최대 30종목 현재가 배치 조회 (모의투자 미지원) |
| `get_intraday_candles` | 주식당일분봉조회 | FHKST03010200 | 당일 분봉 (시간, O/H/L/C, 거래량) |
| `get_flow` | 종목별 투자자매매동향(일별) | FHPTJ04160001 | 날짜별 외국인/기관/개인/투신/사모/은행/보험/기금 순매수 수량+대금 + OHLCV |
| `get_news_stock` | 종합 시황_공시(제목) + opendart search_disclosures | FHKST01011800 | ticker 필터 뉴스/공시 |

> `get_snapshot`은 제거: `get_ohlcv`의 output1이 현재가+호가+밸류에이션을 이미 포함한다.

#### 포트폴리오 (2개)

| 도구 | KIS API | TR_ID | 반환 핵심 데이터 |
|------|---------|-------|-----------------|
| `get_open_positions` | 주식잔고조회 | (inquire_balance) | 보유 종목, 수량, 평균단가, 평가손익 |
| `get_daily_pnl` | 주식잔고조회_실현손익 | (inquire_balance_rlz_pl) | 당일 실현손익 % (사전주입용) |

### 3.3 Phase별 도구 허용 그래프

```
PREMARKET (08:45):
  허용:    get_market_context, get_sector_breadth, get_intraday_index_candles,
           get_news_market, get_event_schedule
  예외허용: get_ohlcv, get_flow — 전일 보유종목 한정
  차단:    psearch, get_realtime_price, get_intraday_candles

SCAN (09:10 / 11:00 / 14:00):
  허용:    시장관찰 4개 + 리스크필터 2개 + 조건검색 3개
           + get_ohlcv + get_flow + get_news_stock
  차단:    get_realtime_price, get_intraday_candles (분봉)

INTRADAY (09:20~15:00):
  허용:    watchlist 종목에 한해
           get_realtime_price (배치), get_intraday_candles, get_flow, get_news_stock
  watchlist 갱신: SCAN 재실행 경로만 허용 (직접 psearch 차단)
  drift_status=CAUTION 시: psearch 직접 호출 추가 차단
  drift_status=REGIME_SHIFT 시: 현재 phase 중단 → SCAN 재실행

CLOSE (15:30+):
  허용:    시장관찰 4개 + get_ohlcv + get_open_positions + get_daily_pnl
           + get_news_stock + get_news_market
  차단:    psearch, get_realtime_price, get_intraday_candles
```

### 3.4 도구 실패 강등

```
get_ohlcv 실패            → ticker SKIP (현재가 없으면 판단 불가)
get_stock_status 실패     → ticker SKIP (상태 미확인 = 진입 금지)
get_news_stock 실패       → 전략별:
                             뉴스모멘텀 전략 → SKIP
                             TREND/RS 전략  → confidence cap 75% + 진행
get_flow 실패             → confidence cap 70% + 진행
psearch 실패              → get_top_movers 백업 + 과열주편향 경고 플래그
get_market_context 실패   → 캐시 사용 + YELLOW 강등
  └─ 캐시 30분 초과 시    → NO_TRADE
circuit breaker           → 동일 도구 연속 3회 실패 → phase 내 비활성화
phase 핵심도구 50% 실패   → phase downgrade (INTRADAY → 모니터 전용)
```

### 3.5 TTL (phase별)

| 도구 | PREMARKET | SCAN | INTRADAY | CLOSE |
|------|-----------|------|----------|-------|
| `get_ohlcv` | 5분 | 2분 | 2분 | 1일 |
| `get_realtime_price` | — | — | **15초** | — |
| `get_intraday_candles` | — | — | 다음봉 갱신주기 | — |
| `get_flow` | 10분 | 5분 | 5분 | 15분 |
| `get_news_stock` | 30분 | **5분** | 10분 | 15분|
| `get_stock_status` | 1시간 | **10분** | 10분 | 1시간 |
| `get_event_schedule` | 1일 | 1일 | 1일 | 1일 |
| `get_market_context` | 5분 | 2분 | 2분 | 5분 |
| `get_sector_breadth` | 5분 | 3분 | 3분 | 5분 |

### 3.6 사전 주입 컨텍스트

```json
{
  "current_phase": "INTRADAY",
  "trading_date": "2026-06-09",
  "market_open": true,
  "regime": {
    "status": "YELLOW",
    "regime": "SIDEWAYS",
    "confidence": 44,
    "timestamp": "2026-06-09T08:45:00"
  },
  "drift_status": "STABLE",
  "risk_guidance": {
    "buy_confidence_threshold": 75,
    "risk_per_trade_pct": 0.35,
    "max_positions": 4,
    "min_trading_value_krw": 10000000000
  },
  "portfolio": {
    "positions": [],
    "position_count": 2,
    "timestamp": "2026-06-09T09:15:00"
  },
  "today_trade_summary": {
    "trade_count": 1,
    "realized_pnl_pct": -0.3,
    "win": 0,
    "loss": 1,
    "last_trade": {"ticker": "005930", "result": "LOSS", "pct": -0.8}
  },
  "risk_budget_remaining": {
    "positions_left": 2,
    "daily_loss_remaining_pct": 1.7
  },
  "watchlist": ["005930", "000660"],
  "allowed_tools": [
    "get_realtime_price", "get_intraday_candles", "get_flow", "get_news_stock"
  ],
  "context_timestamps": {
    "regime": "2026-06-09T08:45:00",
    "portfolio": "2026-06-09T09:15:00",
    "trade_summary": "2026-06-09T11:05:00"
  }
}
```

> 모든 사전주입값에 `timestamp` 필수. LLM이 stale 여부를 직접 인지할 수 있어야 한다.

---

## 섹션 4: RegimeDriftDetector

### 4.1 3-티어 비용 모델

```
Tier 1  Full LLM (gpt-5.4)      08:45  하루 1회
        → RegimeJudgment + drift_triggers + risk_guidance

Tier 2  Code Drift Monitor       매 5분  비용 0
        → drift_triggers 임계값 숫자 비교만

Tier 3  Lite LLM (gpt-5.4-mini)  이벤트 트리거  하루 최대 3회
        → STABLE / CAUTION / REGIME_SHIFT 판단
```

### 4.2 감시 메트릭

| metric | KIS API | 계산 방법 |
|--------|---------|-----------|
| `kospi_drop_from_open_pct` | 국내업종 현재지수 | (현재가 - 시가) / 시가 × 100 |
| `foreign_net_sell_cumulative_bln` | 국내기관_외국인 매매종목가집계 | 외국인 순매수 대금 합계 (음수=순매도) |
| `advance_decline_ratio` | 국내업종 구분별전체시세 | ascn_issu_cnt / (ascn_issu_cnt + down_issu_cnt) |
| `kospi_recovery_from_low_pct` | 국내업종 현재지수 | (현재가 - 장중저가) / 장중저가 × 100 |

### 4.3 CAUTION 반복 카운터

```
drift_state.json에 today_caution_count 저장

Lite LLM → CAUTION 판단 시마다 +1
today_caution_count >= 3
  → 자동 REGIME_SHIFT 처리 (현재 status 한 단계 하향)
  → Telegram: "CAUTION 3회 누적 → 레짐 강등 (YELLOW→RED)"
  → SCAN 재실행
```

---

## 섹션 5: v2 Safety Layer 통합

### 5.1 Proposal 흐름

```
TradingAgent (LLM)
  └─ BUY / SELL / HOLD proposal 생성

RiskOfficer (code, non-negotiable 체크):
  - 일일 손실 한도 초과?          → BLOCK
  - max_positions 초과?           → BLOCK
  - 테마 집중도 초과?             → BLOCK
  - 단일 종목 20% 초과?           → BLOCK
  - get_stock_status 미확인?      → BLOCK
  - LLM risk_guidance 클램핑 적용

PositionSizer (code):
  - ATR 기반 손절가 계산
  - risk_per_trade_pct 기준 수량 산출
  - risk_guidance 클램핑값 사용

Telegram Approval (사용자):
  - 신규 매수: 필수 (5분 타임아웃 → 자동 거부)
  - 손절/목표가 청산: 선택적

OrderManager (code):
  - KIS MCP 또는 KIS API 직접

TradeJournal (code):
  - 모든 거래 기록
  - today_summary() 메서드로 LLM 주입용 요약 생성
```

### 5.2 drift_status → 행동 변화

| drift_status | allowed_tools 변화 | risk_guidance 변화 |
|---|---|---|
| STABLE | 아침 기준 그대로 | 그대로 |
| CAUTION | INTRADAY: psearch 직접 차단 추가 | `risk_guidance_delta` 반영 |
| REGIME_SHIFT | 현재 phase 중단 → SCAN 재실행 | 전체 `risk_guidance` 교체 |

---

## 섹션 6: PM2 스케줄 (v3 개정)

```
cron (KST)          프로세스명              역할
─────────────────────────────────────────────────────────────
08:45               mqk-premarket          Full LLM 레짐 + drift_triggers 생성
09:10               mqk-scan               첫 스캔 (SCAN phase)
09:20               mqk-intraday-start     첫 매수 판단 (INTRADAY phase)
*/5  09:20~15:00    mqk-intraday           RegimeDriftDetector + 포지션 모니터링
11:00               mqk-midday-scan        미드데이 재스캔 (watchlist 갱신 공식 경로)
14:00               mqk-afternoon-scan     오후 재스캔 (SK하이닉스형 회복 포착)
15:30               mqk-close              청산 판단
17:00               mqk-market-close       장마감 분석 + next_day_context.json 생성
06:00 (restart)     mqk-telegram-news      텔레그램 뉴스 수집 (상시)
항상                 mqk-kis-mcp            KIS MCP 서버
항상                 mqk-holiday-check      휴장일 확인
```

v2 대비 변경:
- `08:00 → 08:45` (전일 컨텍스트 로드 시간 확보)
- `08:30 → 09:10` (개장 초기 변동성 안정 후 스캔)
- `11:00`, `14:00` 재스캔 신규 (INTRADAY watchlist 갱신)
- `17:00 market-close` 신규 (다음날 prior 생성)

### 일일 LLM 비용 시뮬레이션

| 항목 | 횟수 | 모델 | 예상비용 |
|------|------|------|----------|
| Full LLM premarket | 1 | gpt-5.4 | ~$0.08 |
| Lite LLM drift | 0~3 | gpt-5.4-mini | ~$0.005 |
| TradingAgent (scan 3회 + intraday) | 6~10 | gpt-5.4 | ~$0.30 |
| Close/MarketClose | 2 | gpt-5.4 | ~$0.10 |
| **일일 총계** | | | **~$0.50 이하** |

---

## 섹션 7: v3 프로젝트 구조

### 7.1 디렉토리

```
MQK-v2/ (v3는 동일 레포, feature/v3 브랜치)
│
├── agents/
│   ├── regime_agent.py           ✅ v2 재사용 + drift_triggers/risk_guidance 출력 추가
│   ├── trading_agent.py          🆕 단일 TradingAgent (Phase별 프롬프트 로드)
│   └── drift_detector.py         🆕 RegimeDriftDetector
│
├── market_intelligence/          🆕 전체 신규
│   ├── market.py                 # get_market_context, get_sector_breadth, get_intraday_index_candles, get_news_market
│   ├── screening.py              # psearch_title, psearch_result, get_top_movers
│   ├── stock.py                  # get_ohlcv, get_realtime_price, get_intraday_candles, get_flow, get_news_stock
│   ├── risk_filter.py            # get_stock_status, get_event_schedule
│   ├── portfolio.py              # get_open_positions, get_daily_pnl
│   ├── cache.py                  # phase-aware TTL 캐시
│   └── circuit_breaker.py        # 도구별 circuit breaker
│
├── codes/
│   ├── risk_officer.py           ✅ v2 재사용 + clamp_risk_guidance() 추가
│   ├── position_sizer.py         ✅ v2 그대로
│   ├── order_manager.py          ✅ v2 그대로
│   ├── trade_journal.py          ✅ v2 재사용 + today_summary() 추가
│   └── scanner.py                ✅ v2 그대로 (SCAN 백업 fallback용)
│
├── config/
│   └── settings.py               ✅ v2 재사용 + RegimeSafetyBounds 추가
│
├── prompts/agents/
│   ├── regime_agent.md           ✅ v2 재사용 + drift_triggers/risk_guidance 섹션 추가
│   └── trading_agent/            🆕
│       ├── premarket.md
│       ├── scan.md
│       ├── intraday.md
│       ├── close.md
│       └── market_close.md
│
├── run_schedule.py               ✅ v2 재사용 + 신규 phase 함수 추가
├── orchestrator_v3.py            🆕 TradingOrchestrator v3 (v2 orchestrator.py 유지)
└── ecosystem.config.cjs          ✅ v2 재사용 + PM2 스케줄 업데이트
```

### 7.2 데이터 파일 흐름

```
data/
├── last_regime.json           # Full LLM 아침 판단 + drift_triggers (매일 갱신)
├── drift_state.json           # 장중 drift 상태 + today_caution_count
├── watchlist.json             # 현재 SCAN 결과 watchlist
├── next_day_context.json      # 17:00 장마감 분석 → 다음날 prior
└── today_trade_summary.json   # TradeJournal 요약 (각 phase 시작 시 LLM에 주입)
```

---

## 섹션 8: 구현 범위 외 (v3.1 이후)

다음 항목은 설계에서 인지하되 v3.0 구현에서는 제외한다.

| 항목 | 이유 |
|------|------|
| 테마 탐지 (KIS sector ≠ 시장 테마) | 별도 스펙 필요 |
| drift_triggers 동적 임계값 (변동성 연동) | 백테스팅 데이터 필요 |
| 실시간 뉴스 속보 크롤링 | 외부 서비스 연동 필요 |
| PM2 → Redis 공유 상태 마이그레이션 | 인프라 변경 필요 |
| opendart 기업 이벤트 캘린더 고도화 | DART API 별도 연구 필요 |
| 모의투자 환경 전체 테스트 | get_realtime_price 모의 미지원 우회 필요 |

---

## 리뷰 이력

| 리뷰어 | 방법 | 주요 지적 |
|--------|------|-----------|
| Codex (gpt-5.4) | `codex exec` | get_stock_status 누락, SCAN phase 공시 차단 위험, TTL phase별 분리 필요 |
| Gemini 2.5 Flash | Gemini CLI | LLM SPOF 대응, risk_guidance ↔ RiskOfficer 조율, CAUTION 반복 → 자동 강등 |
| KIS API 전체문서 | Excel (339개 시트) | 국내주식기간별시세 output1이 현재가+호가+밸류에이션 포함 → get_snapshot 불필요 확인 |
