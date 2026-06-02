# MQK-v2 Master Spec v1.0

## Repository

### Development Repository
https://github.com/gochoojang86-star/MQK-v2

### Legacy Repository
https://github.com/gochoojang86-star/MQK

**용도:**
- **MQK** = 레퍼런스 (읽기 전용)
- **MQK-v2** = 실제 개발 (모든 신규 기능)

---

## 프로젝트 목표

한국형 스윙매매 마스터들의 철학을 가진

**멀티 Agent 자율 트레이더 + Code 기반 생존 엔진** 구축.

---

## 핵심 철학

### 해외
- Jesse Livermore
- William O'Neil
- Mark Minervini

**핵심:** 강한 종목 · 대장주 · 추세 · 거래량 · 신고가

### 국내
- 실전투자대회 우승자 계열

**핵심:** 테마 · 거래대금 · 대장주 · 눌림목 · 수급

### 최종 투자 철학
```
시장 → 테마 → 대장주 → 차트 → 수급 → 뉴스 → 리스크 → 진입 → 관리
```

---

## Agent와 Code 정의

### Agent (LLM 사용)
역할: 해석 / 판단 / 추론 / 반박 / 의사결정

### Code (LLM 미사용)
역할: 계산 / 검증 / 필터링 / 주문 / 리스크 통제

---

## 최종 권한 구조

### Agent 허용
- 매수 판단
- 매도 판단
- 보유 판단
- 시장 해석
- 뉴스 해석
- 테마 해석
- 공시 해석
- 확신도 산정

### Agent 금지
- 수량 계산
- 손절가 확정
- 몰빵
- 물타기
- 리스크 한도 변경
- 전략 자동 적용

### Code 최종 통제
- 수량 / 손절 / 익절 / 리스크 / 주문

### 시스템 철학
> LLM이 운전, Code가 가드레일

---

## 최종 아키텍처

```
MQK-v2/
├── agents/
├── codes/
├── broker/
├── llm/
├── config/
├── data/
├── logs/
└── backtest/
```

---

## Agent 구조

### 1. Regime Agent
시장 체제 판단 (상승장/하락장/횡보장/테마장/정책장/실적장)

```json
{"regime": "THEME_MARKET", "confidence": 84}
```

### 2. Theme Agent
주도 테마 분석 (AI/반도체/전력/원전/방산/바이오)

```json
{"theme": "전력", "leader": "산일전기", "strength": 91}
```

### 3. News Agent
뉴스 질 평가 (재탕/신규재료/소멸/루머/정책수혜)

### 4. Disclosure Agent
공시 해석 (CB/BW/유증/수주/공급계약)

### 5. Portfolio Manager Agent (핵심)
최종 매수/매도/보유 결정

```json
{
  "decision": "BUY",
  "confidence": 82,
  "reason": "...",
  "counter_argument": "..."
}
```

### 6. Review Agent
거래 복기 (실패 원인 / 성공 원인)

### 7. Self Improvement Agent
전략 개선 제안 (실전 반영 금지)

---

## Code 구조

| Code | 역할 |
|------|------|
| Market Data Code | 가격/거래량/거래대금/수급/지수 수집 |
| Scanner Code | 5000종목 → 30종목 압축 |
| Technical Code | ATR/RSI/VCP/박스돌파/눌림목/이평선 |
| Flow Code | 외국인/기관/프로그램/거래대금 |
| Risk Officer Code | 리스크 최종 검증 + 거부권 |
| Position Sizer Code | 수량/손절폭 계산 |
| Stop TakeProfit Code | 손절/1차익절/2차익절/트레일링 |
| Backtest Code | 전략 검증/비교 |
| Order Manager Code | KIS API/KIS MCP 주문 실행 |

---

## 운영 플로우

| 시간 | 단계 | 실행 |
|------|------|------|
| 08:00 | 장전 | Market Data → Regime Agent → market_status.json |
| 08:30 | 후보 생성 | Scanner → Technical → Flow → Theme Agent → candidates.json |
| 장중 | 의사결정 | News Agent + Disclosure Agent + Portfolio Manager Agent |
| 매수 발생 | 실행 | Portfolio Manager → Risk Officer → Position Sizer → Telegram → Order Manager |
| 장마감 | 복기 | Review Agent → Self Improvement Agent → journal.md |

---

## 로그 시스템

```
logs/
└── debug/
    └── YYYY-MM-DD/
        ├── market_scan.json
        ├── candidate_scores.jsonl
        ├── llm_calls.jsonl
        ├── risk_checks.jsonl
        ├── telegram_approvals.jsonl
        ├── orders.jsonl
        ├── errors.log
        └── journal.md
```

---

## 자기개선 구조

```
Trade → Review Agent → Self Improvement Agent → Backtest Code → Paper Trade → User Approval → Production
```

---

## 리스크 규칙

```python
risk_per_trade_pct       = 0.5    # 종목당 최대 손실 0.5%
max_daily_loss_pct       = 2.0    # 일일 최대 손실 2%
max_positions            = 5      # 최대 보유종목수
max_theme_exposure_pct   = 40     # 테마 집중도 최대 40%
max_single_position_pct  = 20     # 단일 종목 최대 20%
stop_loss_method         = "ATR"
atr_multiplier           = 1.5
allow_averaging_down     = False
require_telegram_approval= True
```

---

## 비용 제어 원칙

- **95% Code, 5% LLM**
- 절대 금지: 5000종목 실시간 LLM 호출
- 올바른 구조: 5000종목 → Scanner Code → 30종목 → LLM 평가 → 3~5종목

---

## 개발 우선순위

| Phase | 내용 |
|-------|------|
| Phase 1 | 폴더 구조 / agents / codes / broker |
| Phase 2 | Risk Officer Code / Position Sizer Code |
| Phase 3 | Portfolio Manager Agent / Theme Agent |
| Phase 4 | Telegram / Order Manager / KIS API |
| Phase 5 | Review Agent / Self Improvement Agent / Backtest / KIS MCP |

---

## 최종 미션

> MQK-v2는 "한국형 테마 스윙 마스터들의 철학을 가진 멀티 Agent 자율 트레이더"를 목표로 한다.
>
> **Agent는 사고하고, Code는 생존을 보장한다.**

---

*이 문서는 모든 개발의 단일 기준 문서(Single Source of Truth)입니다.*
