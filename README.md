# MQK v3

**한국형 테마 스윙 마스터들의 철학을 가진 멀티 Agent 자율 트레이더**

> Agent는 사고하고, Code는 생존을 보장한다.

---

## 아키텍처 철학

| 구분 | 역할 |
|------|------|
| **Agent** (LLM) | 해석 · 판단 · 추론 · 의사결정 |
| **Code** (순수 계산) | 리스크 통제 · 수량 계산 · 주문 실행 |

LLM이 운전하고, Code가 가드레일을 친다.

---

## 운영 플로우

```
08:00  장전   → Regime Agent → 시장 체제 판단 (GREEN/YELLOW/RED)
08:30  스캔   → Scanner(5000종목→30종목) → Theme Agent → 후보 확정
장중          → News + Disclosure + Portfolio Manager → 진입 판단
매수 발생     → Risk Officer → Position Sizer → Telegram 승인 → KIS 주문
15:30  장마감 → Review Agent → Self Improvement → 개선 제안 (텔레그램 통보)
```

---

## 프로젝트 구조

```
MQK-v3/
├── agents/                  # LLM 판단 Agent (7개)
│   ├── regime_agent.py      # 시장 체제 판단 (GREEN/YELLOW/RED)
│   ├── theme_agent.py       # 주도 테마 분석
│   ├── news_agent.py        # 뉴스 품질 평가
│   ├── disclosure_agent.py  # 공시 해석 (CB/BW/유증/수주)
│   ├── portfolio_manager.py # 최종 매수/매도/보유 결정
│   ├── review_agent.py      # 거래 복기
│   └── self_improvement_agent.py # 전략 개선 제안
│
├── codes/                   # Code 엔진 (LLM 미사용)
│   ├── market_data.py       # 시장 데이터 모델
│   ├── scanner.py           # 5000종목 → 30종목 압축
│   ├── technical.py         # ATR/RSI/VCP/신고가
│   ├── flow.py              # 외국인/기관/프로그램 수급
│   ├── risk_officer.py      # 리스크 최종 거부권
│   ├── position_sizer.py    # ATR 기반 수량 계산
│   ├── stop_take_profit.py  # 손절/1차익절/2차익절/트레일링
│   ├── order_manager.py     # 주문 실행 최종 관문
│   ├── trade_journal.py     # 거래 생애주기 SQLite DB
│   ├── improvement_manager.py # 개선 제안 저장·승인 관리
│   ├── news_fetcher.py      # Naver + KIS 뉴스 수집
│   └── disclosure_fetcher.py # DART 공시 수집 (키워드 추출)
│
├── broker/                  # 외부 연동
│   ├── kis_api.py           # KIS REST API (실전/모의 자동 전환)
│   ├── kis_mcp_client.py    # KIS MCP 서버 클라이언트 (대안 경로)
│   ├── telegram.py          # 매수 승인 시스템 (UUID 코드 기반)
│   └── telegram_news.py     # 텔레그램 채널 실시간 뉴스 수집
│
├── backtest/                # 백테스트
│   ├── backtest_engine.py   # 수익률/MDD/샤프/손익비 계산
│   ├── historical_loader.py # OHLCV 히스토리컬 데이터 + 파일 캐시
│   └── strategy_runner.py   # 신고가/VCP 전략 시뮬레이션
│
├── llm/                     # LLM 클라이언트
│   ├── client.py            # OpenAI 래퍼 (OAuth 자동 폴백)
│   ├── oauth_loader.py      # Codex/Hermes OAuth 토큰 로더
│   └── soul.py              # Agent 페르소나 프롬프트 주입
│
├── config/
│   └── settings.py          # 리스크·스캐너·LLM 파라미터 단일 관리
│
├── orchestrator_v3.py       # 전체 플로우 조율
├── run_schedule_v3.py       # PM2 단계별 진입점
└── ecosystem.config.cjs     # PM2 자동 운영 설정
```

---

## 설치

```bash
# Python 3.12 가상환경
uv venv .venv --python 3.12
uv pip install -r requirements.txt
```

---

## 환경 설정

`.env` 파일 생성 (`.gitignore` 적용됨):

```env
# ── KIS 한국투자증권 ────────────────────────────────────────────────────────
KIS_MODE=paper                  # paper(모의) | real(실전)
KIS_PAPER_APP_KEY=...
KIS_PAPER_APP_SECRET=...
KIS_PAPER_ACCOUNT=50189814-01
KIS_REAL_APP_KEY=...
KIS_REAL_APP_SECRET=...
KIS_REAL_ACCOUNT=44187565-01

# ── Naver 뉴스 API ────────────────────────────────────────────────────────
NAVER_CLIENT_ID=...
NAVER_CLIENT_SECRET=...

# ── OpenDart ──────────────────────────────────────────────────────────────
DART_AUTH_KEY=...

# ── Telegram ──────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...

# ── LLM 인증 (둘 중 하나) ─────────────────────────────────────────────────
OPENAI_API_KEY=...              # 직접 API 키 (있으면 우선 사용)
# 없으면 ~/.codex/auth.json Hermes/Codex OAuth 토큰 자동 폴백

# ── 주문 안전 ─────────────────────────────────────────────────────────────
ORDER_DRY_RUN=true              # 실전 전 반드시 true

# ── 운영 자본금 ───────────────────────────────────────────────────────────
MQK_CAPITAL=50000000            # 5천만원 기본값
```

---

## 자동 운영 (PM2)

```bash
# 프로세스 등록
pm2 start ecosystem.config.cjs
pm2 save

# 프로세스 목록
pm2 list
```

| 프로세스 | 실행 시간 | 역할 |
|---------|---------|------|
| `mqk-v3-premarket-early` | 평일 08:45 | 장전 진입 후보 선별 |
| `mqk-v3-premarket-first` | 평일 08:50 | 장전 첫 레짐 판단 |
| `mqk-v3-premarket` | 평일 09:03/11:03/13:03 | 장중 레짐 재평가 |
| `mqk-v3-scan` | 평일 09:17/11:17/13:17 | 후보 종목 스캔 |
| `mqk-v3-intraday` | 평일 09:00~14:50 (10분마다) | 장중 진입/청산 판단 |
| `mqk-v3-close` | 평일 15:18 | 정규장 내 청산 판단 |
| `mqk-v3-market-close` | 평일 17:00 | 장마감 복기 + 개선 제안 |
| `mqk-telegram-news` | 상시 | 텔레그램 채널 뉴스 수집 |

---

## LLM 모델 배치

| Agent | 티어 | 모델 |
|-------|------|------|
| PortfolioManagerAgent | REASONING | gpt-5.4 |
| SelfImprovementAgent | REASONING | gpt-5.4 |
| RegimeAgent | STANDARD | gpt-5.4 |
| ThemeAgent | STANDARD | gpt-5.4 |
| ReviewAgent | STANDARD | gpt-5.4 |
| NewsAgent | FAST | gpt-5.4-mini |
| DisclosureAgent | FAST | gpt-5.4-mini |

`config/settings.py`의 `LLMConfig`에서 모델 변경 가능.

---

## 리스크 파라미터

`config/settings.py`의 `RiskConfig` 참조:

| 파라미터 | 기본값 | 설명 |
|---------|-------|------|
| `risk_per_trade_pct` | 0.5% | 종목당 최대 손실 |
| `max_daily_loss_pct` | 2.0% | 일일 최대 손실 |
| `max_positions` | 5 | 최대 보유 종목 수 |
| `max_theme_exposure_pct` | 40.0% | 테마 집중도 한도 |
| `require_telegram_approval` | True | 매수 전 텔레그램 승인 필수 |
| `ORDER_DRY_RUN` | true | 실제 주문 차단 (env) |

---

## 자기개선 사이클

```
거래 → Review Agent → Self Improvement Agent
     → ImprovementManager (SQLite 저장 + 텔레그램 통보)
     → 사용자 승인 → Backtest 검증 → 실전 반영
```

자동 적용 금지 — 모든 개선안은 사람 승인 후 `config/settings.py`에 수동 반영.

---

## 문서

- 현재 운영 스펙: [PROJECT_MASTER_SPEC.md](PROJECT_MASTER_SPEC.md)
- 문서 인덱스: [docs/README.md](docs/README.md)
