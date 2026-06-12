# MQK v3 실환경 통합 테스트 계획 (Live Integration Test Plan)

**목표:** 단위 테스트(214개, 전부 mock)와 달리, 실제 KIS API·LLM·Telegram을 호출해 각 아키텍처 레이어가 **실데이터로 올바른 값을 가져오고 작동하는지** 검증한다.

**대원칙 (안전):**
1. 모든 테스트는 `KIS_MODE=paper`(주문 모의투자) + `ORDER_DRY_RUN=true` 상태에서만 실행한다. 시세 데이터는 `KIS_DATA_MODE=real` 그대로 사용.
2. 테스트 시작 전 반드시 확인: PM2의 v2 트레이딩 앱 4개(mqk-premarket/scan/intraday/close)가 실행 중이면 정지. 테스트와 자동매매가 같은 계좌를 건드리면 안 된다.
3. 결측값을 시장 신호로 해석하지 않는다 — 값이 0이면 "실제 0"인지 "결측"인지 raw 응답으로 구분해 기록.

**판정 기록:** 각 항목을 아래 체크리스트에 ✅/❌/⏭️(장외 불가)로 기록하고, ❌는 raw 응답을 `data/live_test_logs/`에 저장한다.

**시간 제약:** 🕘 표시 항목은 장중(09:00–15:30 KST)에만 의미 있는 값이 나온다. 장외에 1차(Level 0–1, Level 2 일부), 다음 거래일 장중에 2차(나머지)로 나눠 실행한다.

---

## D1 결과 (2026-06-12, 장중 09:02~09:30 실행)

| 항목 | 판정 | 핵심 결과 |
|------|:---:|-----------|
| 0-0 주문 TR 완결 | ✅ | 구 TR(VTTC0802U)로 paper 매수 접수 성공(order_no 0000002130). 취소는 `KIS_ORDER_ADMIN_MODE=paper`에서만 성공(VTTC0013U) — **기본값(real)으로는 paper 주문 취소 불가** |
| 0-0b 미체결 조회(모의) | ✅ | VTTC0084R → "없는 서비스 코드" (문서대로 모의 미지원). graceful 빈 리스트 확인 |
| 5-1 premarket | ✅ | YELLOW/THEME_MARKET/78. risk_guidance 4키 전부 클램프 범위 내, drift_triggers 4개 스키마 OK, drift_state 리셋, premarket_review.json 생성 |
| 2 장중 스모크 | ✅ | 19건 실패 0 (수정 후). psearch_result만 ⚠️ — **HTS 조건식이 KIS 서버에 미저장 상태** (사용자: HTS 조건검색 화면에서 "서버 저장" 필요) |
| 4-2 Tier2 드리프트 | ✅ | 실데이터 metrics 정상, recovery_signal+breadth_support 발동, 쿨다운 60분 작동(다음 틱 재발동 안 함) |
| 4-4 / 5-2 scan | ✅ | watchlist=["005930"] 생성(확신도 82, 신규 도구 데이터 활용). psearch 실패 시 백업 스캔으로 자체 우회 |
| 5-3 intraday 1틱 | ✅ | Tier3 Lite LLM 1회(daily_lite_llm_calls=1, 캡 추적 OK), drift STABLE, NO_TRADE(확신도<78) — proposals 미발생으로 Telegram 승인 왕복은 ⏭️ (자연 발생 대기) |
| 5-4 RiskOfficer 차단 | ⏭️ | proposal 미발생 — 단위테스트로는 검증됨, 라이브는 BUY 발생 시 |
| 5-3b intraday 매수 체결 (오후 재실행) | ✅ | GREEN/89 레짐 → scan watchlist(backfill) → **BUY proposal(095340, 확신도 82) → Telegram 승인 실왕복 → 모의계좌 6주 @243,000 체결** — 전 안전체인 라이브 완주. 현금가드/RiskOfficer 통과 확인 |
| 5-5 close | ✅* | 크래시 없음. LLM이 SELL 2건 제안(005930 익절/095340 손절) — 095340은 저널 보유라 주문 실행, 005930은 v3 저널 외 종목이라 SKIP(설계대로). v2 close review 연계 정상. ***단, 15:32 매도 주문은 "모의투자 장종료"로 거부 — close(15:30) 단계의 SELL은 구조적으로 체결 불가** (운영 결정 필요: ①close를 15:18로 당겨 종가 동시호가 참여 ②시간외 종가 주문 사용 ③청산 판단을 intraday 14:5x까지로 한정) |

**D1에서 발견·수정된 실버그 6건** (전부 라이브 전용 — mock 테스트로는 검출 불가):
1. `get_intraday_index_candles`: 필수 `FID_ETC_CLS_CODE` 누락으로 **개통 이래 모든 호출이 거부**(rt_cd=2가 빈 candles로 은폐) + 과거일 캔들 혼입 + 내림차순 (`d717452`)
2. `get_market_context`: 지수값이 KIS raw 문자열로 와서 **Tier2 드리프트 감시가 매 틱 무력화** (`dafd71e` 직전 커밋)
3. `get_sector_breadth`: 상승/하락 종목수가 output1(기준지수)에만 존재 — 업종 행 합산은 **항상 0** → market_breadth로 재설계 (`dafd71e`)
4. LLM이 JSON 2개를 한 응답에 반환 → **scan phase 전체 크래시** → raw_decode fallback + NO_TRADE 강등 + 단일 JSON 프롬프트 강제 + max_steps 12 (`e4a1816`, 후속)
5. `_build_context`: 일시적 KIS 500이 phase를 죽임 → 보수적 강등(매수 예산 0) (직후 커밋)
6. `get_intraday_candles`(종목 분봉): `FID_INPUT_HOUR_1="60"` 오용으로 **전일 15시대 캔들 반환** — TradingAgent가 직접 "비정상 시계열" 지적 (`bab9700`)

**미해결/사용자 액션:**
- ⚠️ `_order_admin_mode` 기본값(real) 탓에 paper 운영 시 OrderManager의 미체결 취소가 동작 불가 — paper 운영 기간엔 `.env`에 `KIS_ORDER_ADMIN_MODE=paper` 설정 권장 (실전 전환 시 제거)
- psearch_result: HTS 조건검색식 "서버 저장" 후 재테스트
- 5-5 close(15:27)/5-6 market_close(16:58): 당일 예약 등록됨

> **사전 발견 이슈 (API 문서 대조, `docs/superpowers/specs/2026-06-11-kis-api-inventory.md`):**
> ① 코드의 주문 TR(TTTC0802U/0801U)이 2026-05-28 KIS 문서에서 사라짐 — 신규 TR은 TTTC0012U(매수)/TTTC0011U(매도). **0-0에서 최우선 확인.**
> ② TTTC0084R(미체결 조회)은 모의투자 미지원 — paper 모드 실패 양상 확인 필요 (0-0b).

## Level 0: 환경 / 인증 (장외 가능)

| # | 항목 | 실행 | 통과 기준 |
|---|------|------|-----------|
| 0-0 | **주문 TR 구버전 동작 확인 (최우선)** | paper 모드로 소액 지정가 매수 1건 → 즉시 취소 | 구 TR(VTTC0802U) 정상 접수되면 통과(폐기 예고 여부 응답 메시지 확인). 실패 시 **신 TR(VTTC0012U/0011U)로 마이그레이션 후 테스트 재개** — 다른 모든 항목보다 우선 |
| 0-0b | 미체결 조회 모의 미지원 확인 | paper 모드에서 `VTTC0084R` 경로 1회 호출 | 실패 시 빈 목록 graceful 처리 확인, "실전 전용"으로 기록 |
| 0-1 | 안전모드 확인 | `python -c "from broker.kis_api import KISApi; k=KISApi(); print(k.mode, k._data_mode)"` + `echo $ORDER_DRY_RUN` | 주문 모드 `paper`, `ORDER_DRY_RUN=true`, 데이터 모드 `real` (MIL 11개 도구가 모의 미지원이므로 real 필수) |
| 0-2 | .env 필수 키 | KIS app key/secret, 계좌번호, `KIS_HTS_ID`, LLM API 키, Telegram 토큰/챗ID, `KIS_MCP_URL` 존재 확인 | 전부 비어있지 않음 |
| 0-3 | KIS 토큰 발급 | `KISApi()` 생성 후 아무 시세 1건 호출 | HTTP 200, 토큰 캐시 생성 |
| 0-4 | KIS MCP 서버 | `python -c "from broker.kis_mcp_client import KISMCPClient; print(KISMCPClient().available())"` | `True` (PM2 `mqk-kis-mcp` 기동 상태) — `False`면 REST 폴백 경로로 진행하되 기록 |
| 0-5 | Telegram 발송 | `self._telegram.notify("[LIVE TEST] ping")` 1건 | 실제 채팅방 수신 확인 |
| 0-6 | LLM 연결 | `llm/client.py`로 최소 프롬프트 1회 (gpt-5.4-mini) | 정상 JSON 응답 |
| 0-7 | 휴장일 캐시 | `python -c "from codes.market_calendar import check_trading_day; print(check_trading_day())"` | 오늘 영업일 여부가 실제와 일치 |

## Level 1: KIS REST 원시 API (장외 일부 가능)

raw 레벨에서 값 자체의 신뢰성을 먼저 확인한다. MIL을 거치기 전에 여기서 어긋나면 상위 전부가 의심된다.

| # | 항목 | 검증 내용 | 통과 기준 |
|---|------|-----------|-----------|
| 1-1 | 지수 시세 | KOSPI/KOSDAQ 현재가·등락률 | 지수 > 0, 등락률이 포털(네이버금융) 값과 ±0.1%p 이내 |
| 1-2 | 일봉 + **전일 거래대금 회귀** | 005930 일봉 60개 | 전일 row의 `acml_tr_pbmn > 0` (장전 rows[0]=당일 0원 버그 재발 여부 — 장전 시간대에 반드시 1회 실행) |
| 1-3 | 분봉 🕘 | 005930 당일 분봉 | 캔들 수 > 0, OHLC 순서 정합 (low ≤ open/close ≤ high) |
| 1-4 | 뉴스 raw | `FHKST01011800` news-title | `output` 리스트 비어있지 않음, 제목·일시 필드 채워짐 |
| 1-5 | 수급 | 005930 외국인/기관 순매수 | 숫자 파싱 성공 (결측 시 missing으로 기록, 0으로 처리되지 않는지) |

## Level 2: MIL 16개 도구 스모크 (핵심)

스모크 러너 `tools/live_smoke_mil.py`를 작성해 16개 도구를 순회 호출한다 (이 스크립트 작성이 테스트 실행의 첫 작업).

```python
# tools/live_smoke_mil.py 골격
# MILContext(KISApi(), KISMCPClient(), MILCache(), CircuitBreaker()) 구성 후
# 각 도구를 phase="SCAN"으로 호출, 결과 요약 + sanity 판정 출력, 실패 시 raw 저장
```

| # | 도구 | sanity 기준 | 장중 필요 |
|---|------|-------------|:---:|
| 2-1 | `market.get_market_context` | kospi/kosdaq > 0, foreign_net_buy_krw 숫자 | |
| 2-2 | `market.get_sector_breadth` | sectors 비어있지 않음, advancers+decliners > 0 | 🕘 |
| 2-3 | `market.get_intraday_index_candles` | 당일 캔들 ≥ 1, OHLC 정합 | 🕘 |
| 2-4 | `market.get_news_market` | headlines ≥ 1건, title/date/time 채워짐 | |
| 2-5 | `screening.psearch_title` | HTS 조건검색 목록 반환 (**사전조건**: HTS에 조건식 1개 이상 등록, `KIS_HTS_ID` 설정) | |
| 2-6 | `screening.psearch_result` | 2-5의 seq로 종목 리스트 반환 | 🕘 |
| 2-7 | `screening.get_top_movers` | 거래대금 상위 종목 ≥ 1, 거래대금 내림차순 | 🕘 |
| 2-8 | `stock.get_ohlcv` (005930) | 60개 행, 종가·거래대금 > 0 | |
| 2-9 | `stock.get_realtime_price` ([005930, 000660]) | 두 종목 모두 현재가 > 0 | 🕘 |
| 2-10 | `stock.get_intraday_candles` (005930) | 당일 분봉 ≥ 1 | 🕘 |
| 2-11 | `stock.get_flow` (005930) | 외국인/기관 수급 숫자 파싱 | |
| 2-12 | `stock.get_news_stock` (005930) | 해당 종목 헤드라인 반환 | |
| 2-13 | `risk_filter.get_stock_status` | 005930 → 정상, 알려진 관리종목 1개 → 위험 플래그 검출 | |
| 2-14 | `risk_filter.get_event_schedule` (005930) | 호출 성공 (이벤트 없으면 빈 목록 허용) | |
| 2-15 | `portfolio.get_open_positions` | paper 계좌 잔고와 position_count 일치 | |
| 2-16 | `portfolio.get_daily_pnl` | realized_pnl/total_eval_amt가 계좌 조회값과 일치 | |

**인프라 동작 검증 (같은 스크립트에서):**
- **캐시**: 동일 도구 2회 연속 호출 → 2회차가 API 미호출(시간 < 10ms 또는 호출 카운트 동일)
- **Circuit breaker**: 존재하지 않는 ticker로 `get_ohlcv` 반복 실패 → 임계 도달 후 `ToolFailure: circuit breaker open` 확인
- **MCP 폴백**: MCP 서버 내린 상태에서 1개 도구 호출 → REST 폴백으로 성공하는지 (또는 명시적 실패가 ToolFailure로 잡히는지)

## Level 3: 뉴스 수집 경로 전체 점검

| # | 항목 | 검증 내용 |
|---|------|-----------|
| 3-1 | MIL 뉴스 도구 | 2-4, 2-12 결과를 같은 시각 HTS/포털 뉴스와 대조 — 누락·시차 확인 |
| 3-2 | `broker/telegram_news.py` (mqk-telegram-news 앱) | `data/mqk_news_session.session` 유효한지, 앱 기동 후 뉴스 채널 수신 1건 이상 로그 확인 |
| 3-3 | `codes/news_fetcher.py` / `agents/news_agent.py` (v2 경로) | v3 흐름에서 실제 사용되는지 확인 — 미사용이면 "v2 전용"으로 기록만 |
| 3-4 | 공시 | `codes/disclosure_fetcher.py` 실호출 1건 (DART) — 당일 공시 목록 반환 |

## Level 4: 에이전트 단독 실행 (실 LLM, 비용 발생 — 각 1회)

| # | 항목 | 실행 | 통과 기준 |
|---|------|------|-----------|
| 4-1 | RegimeAgent.judge() | 실 시장 데이터 + 실 LLM 1회 | status∈{GREEN,YELLOW,RED}, risk_guidance 4키 전부 클램프 범위 내, drift_triggers 스키마(id/metric/threshold/direction) 유효, cooldown 15–240·max_daily_triggers 1–5, `data/last_regime.json` 저장됨 |
| 4-2 | DriftDetector Tier2 (무료) | `_collect_drift_snapshot()` 실데이터 → `compute_metrics`+`evaluate_triggers` | 🕘 metrics 숫자 정상, LLM 호출 0회 |
| 4-3 | DriftDetector Tier3 | threshold를 인위적으로 낮춘 trigger로 Lite LLM 1회 강제 발동 | drift_judgment 반환, `daily_lite_llm_calls` 증가, new_status가 GREEN/YELLOW/RED로 검증됨 |
| 4-4 | TradingAgent 1회 | PREMARKET phase, 실 컨텍스트로 `run()` | ReAct 루프가 max_steps(6) 내 종료, 허용 도구만 호출(로그 확인), final JSON 스키마 정상 |

## Level 5: 오케스트레이터 phase 통합 (ORDER_DRY_RUN=true 필수)

하루 흐름을 phase 순서대로 1회씩 수동 실행. 산출물 파일로 검증한다.

| # | Phase | 실행 시점 | 산출물/통과 기준 |
|---|-------|----------|------------------|
| 5-1 | `run_premarket_v3()` | 08:30–08:55 | `last_regime.json`(클램프된 값), `drift_state.json` 리셋(date=오늘, 카운트 0), `premarket_review.json` |
| 5-2 | `run_scan_v3()` | 09:10 이후 🕘 | `watchlist.json` 생성, 티커 6자리 형식 |
| 5-3 | `run_intraday_v3()` 1틱 | 장중 🕘 | drift 체크 로그, `intraday_v3_HHMMSS.json` 저장. BUY proposal 발생 시: Telegram 승인 요청 실수신 → 승인 → **dry-run 주문 로그**(실주문 0건) |
| 5-4 | RiskOfficer 차단 | 장중 🕘 | max_positions를 일시적으로 0으로 낮춰 proposal → `BLOCKED` 반환 + 주문 0건 확인 후 원복 |
| 5-5 | `run_close_v3()` | 15:30 | `close_v3.json`, v2 `run_close_review()` 정상 연계 |
| 5-6 | `run_market_close_v3()` | 17:00 | `market_close_snapshot.json` / `close_market_read.json` / `next_day_premarket_context.json` 3개 생성, data_quality 필드 확인 |
| 5-7 | 다음날 연계 | 익일 08:45 | 5-6의 prior가 RegimeAgent 입력에 반영되는지 로그 확인 |

## Level 6: 운영(스케줄러) 검증

| # | 항목 | 검증 내용 |
|---|------|-----------|
| 6-1 | `run_schedule_v3.py` 각 phase | `MQK_PHASE=premarket .venv/bin/python run_schedule_v3.py` 식으로 5개 phase 수동 실행 — Level 5와 동일 산출물 |
| 6-2 | flock 중복 가드 | 터미널 2개에서 intraday 동시 실행 → 한쪽이 "이전 인스턴스 실행 중 — 스킵" 후 exit 0 |
| 6-3 | 휴장일 가드 | 주말/휴일에 실행 → "휴장일 — 작동 중단" exit 0 |
| 6-4 | PM2 등록 + 1거래일 관찰 | **v2 트레이딩 앱 4개 정지 후** v3 5개 앱 등록. 1거래일 동안 paper+dry-run으로 자동 운영: 크론 발화 시각(KST) 정확성, intraday 5분 주기, 로그/Telegram 알림, LLM 비용(Tier3 호출 횟수 ≤ max_daily_triggers) 확인 |

---

## 실행 순서 요약

1. **D0 (장외, 오늘 가능)**: Level 0 전체 → Level 1-1/1-2(장전이면 더 좋음)/1-4/1-5 → `tools/live_smoke_mil.py` 작성 → Level 2 장외 가능 항목(2-1, 2-4, 2-5, 2-8, 2-11~2-16) + 캐시/서킷브레이커 → Level 3 → Level 4-1
2. **D1 (다음 거래일 장중)**: Level 1-3 → Level 2 장중 항목 → Level 4-2/4-3/4-4 → Level 5 전체 (시간대별)
3. **D2 (그 다음 거래일)**: Level 5-7 → Level 6 (PM2 1거래일 자동 관찰)

**예상 LLM 비용**: Level 4–5에서 Full LLM 3~5회 + Lite 1~2회 수준 (틱당 반복 없음).

## 종료 기준

- Level 0–3 전 항목 ✅ (❌는 수정 후 재시도)
- Level 4–5에서 실주문 0건, 클램프/차단/승인 게이트 전부 실측 확인
- Level 6-4 1거래일 무인 운영에서 크래시·시간대 오류·중복 실행 0건
- 이후 실전 전환(KIS_MODE=real, ORDER_DRY_RUN=false)은 별도 사용자 결정
