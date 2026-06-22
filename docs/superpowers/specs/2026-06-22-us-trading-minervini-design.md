# US Trading — Minervini 스윙매매 봇 설계

**날짜:** 2026-06-22  
**상태:** 승인됨  
**기반:** MQK v3 Fork → 독립 레포 (`us_trading/`)

---

## 1. 개요

KIS 해외주식 API를 기반으로 Mark Minervini의 SEPA(Specific Entry Point Analysis) 방법론을 LLM 페르소나로 구현한 미장 스윙매매 봇.

**핵심 원칙:**
- 코드 가드 없이 LLM 판단 90%+
- 규칙 기반 코드가 낳는 예외 처리 스파게티 방지
- 코드는 데이터 수집 / LLM 호출 / 주문 실행만 담당

---

## 2. 전체 아키텍처

```
[yfinance]          [KIS 해외주식 API]
    │                       │
    └──────  DataLayer  ────┘
                 │
          ┌──────▼───────┐
          │ScreenerAgent │  1단계 LLM
          │(미네르비니    │  500~600종목 → 워치리스트 20~30개
          │  스크리너)   │
          └──────┬───────┘
                 │ watchlist.json
          ┌──────▼───────┐
          │ TraderAgent  │  2단계 LLM
          │(미네르비니   │  VCP 판단 + 진입/청산/홀딩 결정
          │  트레이더)   │
          └──────┬───────┘
                 │
          ┌──────▼───────┐
          │  OrderLayer  │  KIS 해외주식 매수/매도 실행
          └──────┬───────┘
                 │
            Telegram 알림
```

### 파일 구조

```
us_trading/                         # MQK v3 fork, 독립 레포
├── orchestrator_us.py              # 3-phase 스케줄러 + DST 처리
├── agents/
│   ├── screener_agent.py           # 1단계 LLM (Trend Template 스크린)
│   └── trader_agent.py             # 2단계 LLM (VCP + 진입/청산)
├── data/
│   └── market_data.py              # yfinance 래퍼 (가격/펀더멘털/뉴스)
├── broker/
│   └── kis_us_api.py               # KIS 해외주식 API (현재가/주문/잔고)
├── prompts/
│   ├── screener_persona.md         # 미네르비니 스크리너 페르소나
│   └── trader_persona.md           # 미네르비니 트레이더 페르소나
├── ecosystem.config.cjs            # PM2 설정 (DST 2벌 cron)
└── docs/
    └── kis_us_api_inventory.md     # KIS 해외주식 API 인벤토리 (구현 초반 작성)
```

---

## 3. 미네르비니 페르소나

### 3-1. Screener 페르소나 (`screener_persona.md`)

**역할:** 500~600종목 → VCP 후보 20~30개 선별

**Trend Template 8조건:**
1. 현재가 > 150일 MA AND > 200일 MA
2. 150일 MA > 200일 MA
3. 200일 MA 상승 추세 (최소 30거래일)
4. 50일 MA > 150일 MA > 200일 MA (MA 완벽 정렬)
5. 현재가 > 50일 MA
6. 현재가 ≥ 52주 저점 × 1.25 (+25% 이상)
7. 현재가 ≥ 52주 고점 × 0.75 (고점 대비 -25% 이내)
8. RS(상대강도) 상위 30% 이상

**펀더멘털 조건:**
- EPS 성장률 20%+ (전년 동기 대비 최근 분기)
- 매출 성장률 20%+
- 기관 보유 비율 증가 추세

**판단 원칙:** 애매하면 탈락. 목표는 엄선된 소수.

**출력 (JSON):**
```json
{
  "watchlist": [
    {
      "ticker": "NVDA",
      "trend_template_pass": true,
      "rs_rank": "top 3%",
      "setup_quality": "A+",
      "reason": "MA 완벽 정렬, EPS 122% 성장, 52주 고점 3% 이내"
    }
  ],
  "rejected_count": 572,
  "scan_summary": "강한 기술주 중심 셋업 집중"
}
```

---

### 3-2. Trader 페르소나 (`trader_persona.md`)

**역할:** 워치리스트 20~30개 → 진입/청산/홀딩 결정

**VCP(Volatility Contraction Pattern) 판단 기준:**
- 수축 횟수: 3~4회 (변동폭이 점점 줄어야 함)
- 거래량: 수축 구간마다 감소, 피벗 돌파 시 평균 대비 2배+
- 피벗 포인트: 마지막 수축 구간의 고점
- 진입 조건: 피벗 돌파 + 거래량 폭증 동시 확인
- 추격 금지: 피벗 대비 +3% 초과 시 패스

**포지션 관리 규칙:**
- 최대 3포지션 동시 보유
- 포지션당 포트폴리오 약 33%
- 손절: 진입가 -10% (하드 스탑)
- +10% 도달 → 손절선 본전으로 이동
- +20% 도달 → 트레일링 스탑 적용
- 큰 수익 후 50일 MA 이탈 → 절반 청산
- 좋은 셋업 없으면 현금 보유

**출력 (JSON):**
```json
{
  "action": "BUY" | "SELL" | "HOLD" | "NO_TRADE",
  "ticker": "NVDA",
  "quantity": 10,
  "reason": "VCP 3차 수축 완료, 피벗 875 돌파, 거래량 2.3배",
  "stop_loss": 787.5,
  "position_update": {}
}
```

---

## 4. 데이터 레이어

### 소스별 역할

| 소스 | 용도 | 비용 |
|------|------|------|
| `yfinance` | 일봉 OHLCV, MA, 52주 고저, EPS/매출 성장률, 뉴스 | 무료 |
| `KIS 해외주식 API` | 실시간 현재가, 거래량, 주문 실행, 잔고 조회 | 무료 |

> KIS 해외주식 API 인벤토리는 구현 초반 D0에서 별도 문서화 후 yfinance와 역할 분담 확정.

### 스크리너 입력 포맷 (종목당 1줄 텍스트 압축)

```
NVDA | 현재가:875 | MA50:820 MA150:720 MA200:650
     | 52W고:900 52W저:410 | EPS성장:122% 매출성장:94%
     | RS순위:상위3% | 뉴스: AI 수요 급증
```

**토큰 추정:**
- 1단계 입력: ~80토큰 × 600종목 = 5~6만 토큰
- 2단계 입력: 20~30종목 = 3~5천 토큰

### 뉴스 소스

- `yfinance.Ticker(t).news` — 종목당 헤드라인 3개
- 필요 시 Finnhub 무료 티어 추가 (감성 점수 포함)
- 뉴스는 컨텍스트 보조용, 판단은 차트/거래량/패턴 기반

---

## 5. 3-Phase 스케줄

### DST 처리

```python
from zoneinfo import ZoneInfo
now_et = datetime.now(ZoneInfo("America/New_York"))
# ET 기준 시간창 가드로 DST 자동 처리
# PM2 cron은 KST 기준 2벌 유지:
#   썸머 (3~11월): "30 21 * * 1-5"  (Pre-market)
#   윈터 (11~3월): "30 22 * * 1-5"  (Pre-market)
# MQK_FORCE=1 로 수동 강제 실행 가능
```

### Phase 상세

| Phase | ET | KST (夏/冬) | 역할 |
|-------|----|-------------|------|
| Pre-market | 08:30 | 21:30 / 22:30 | 전 종목 스캔 → 워치리스트 |
| Intraday | 11:30 | 00:30 / 01:30 | VCP 브레이크아웃 감지 + 진입 |
| Market Close | 16:00 | 05:00 / 06:00 | 포지션 검토 + 손절/익절 + 익일 준비 |

### Phase별 흐름

**Phase 1 — Pre-market:**
1. yfinance → S&P500 + Nasdaq100 전 종목 일봉 + 펀더멘털 수집
2. 1단계 LLM(Screener) 호출 → 워치리스트 20~30개 선별
3. `watchlist.json` 저장
4. Telegram: "오늘 셋업 N개 발견" 요약

**Phase 2 — Intraday:**
1. KIS → 워치리스트 현재가 + 거래량 수집
2. yfinance → 뉴스 헤드라인 업데이트
3. 2단계 LLM(Trader) 호출 → VCP 피벗 돌파 판단 + 진입 결정
4. 진입 결정 시 → KIS 해외주식 매수 주문 실행
5. Telegram: 진입 내역 알림

**Phase 3 — Market Close:**
1. KIS → 현재 포지션 + 평가손익 조회
2. yfinance → 당일 종가 확정 일봉
3. 2단계 LLM(Trader) 호출 → 손절/익절/트레일링 판단
4. KIS → 매도 주문 (필요 시)
5. Telegram: 일일 결산 리포트

### 휴장일 처리

```python
import pandas_market_calendars as mcal
nyse = mcal.get_calendar("NYSE")
# 오늘 NYSE 개장일 여부 확인 후 Phase 실행
```

---

## 6. LLM 인터랙션

### 모델

MQK v3와 동일하게 Claude 최신 모델 사용 (claude-sonnet-4-6 또는 상위).

### 에러 처리

| 상황 | 처리 |
|------|------|
| LLM 응답 파싱 실패 | 재시도 1회 → 실패 시 NO_TRADE |
| KIS API 실패 | 3회 재시도 (MQK v3 패턴 동일) |
| yfinance 실패 | Telegram 알림 + 해당 Phase 스킵 |

---

## 7. 종목 유니버스

- S&P 500 + Nasdaq 100 (중복 제거, 약 500~600개)
- 매일 Pre-market 시 구성 종목 리스트 갱신
- 소스: Wikipedia S&P500 리스트(`pd.read_html`) + Nasdaq100 정적 리스트 조합, D0에서 확정

---

## 8. 구현 우선순위 (D0~D3)

| 단계 | 내용 |
|------|------|
| D0 | 레포 Fork + KIS 해외주식 API 인벤토리 + yfinance 연결 검증 |
| D1 | DataLayer + ScreenerAgent + Phase 1 동작 확인 |
| D2 | TraderAgent + Phase 2/3 + KIS 주문 연결 |
| D3 | PM2 스케줄 + Telegram + 라이브 테스트 |
