# MQK v3 아키텍처별 KIS REST API 인벤토리 + 문서 대조 결과

**대조 기준 문서:** `KIS_API_전체문서_20260528_030000.xlsx` (2026-05-28 발행, 국내주식 REST 135개)
**현재 사용:** 22개 TR (MIL 15 + v2 broker 7) — 갭 분석은 하단 참조.

---

## 1. 아키텍처별 사용 API

### 1-1. v3 Market Intelligence Layer (16개 도구)

| 도구 | TR ID | 문서상 API 명 | 모의지원 |
|------|-------|---------------|:---:|
| `market.get_market_context` | FHPTJ04400000 | 국내기관/외국인 매매종목가집계 | ❌ |
| `market.get_sector_breadth` | FHPUP02140000 | 국내업종 구분별전체시세 | ❌ |
| `market.get_intraday_index_candles` | FHKUP03500200 | 업종 분봉조회 | ❌ |
| `market.get_news_market` | FHKST01011800 | 종합 시황/공시(제목) | ❌ |
| `stock.get_ohlcv` | FHKST03010100 | 국내주식기간별시세(일/주/월/년) | ✅ |
| `stock.get_realtime_price` | FHKST11300006 | 관심종목(멀티종목) 시세조회 | ❌ |
| `stock.get_intraday_candles` | FHKST03010200 | 주식당일분봉조회 | ✅ |
| `stock.get_flow` | FHPTJ04160001 | 종목별 투자자매매동향(일별) | ❌ |
| `stock.get_news_stock` | FHKST01011800 | 종합 시황/공시(제목) — 종목 필터 | ❌ |
| `screening.psearch_title` | HHKST03900300 | 종목조건검색 목록조회 | ❌ |
| `screening.psearch_result` | HHKST03900400 | 종목조건검색조회 | ❌ |
| `screening.get_top_movers` | FHPST01710000 | 거래량순위 | ❌ |
| `risk_filter.get_stock_status` | FHPST01390000 + FHPST04830000 | VI 현황 + 공매도 일별추이 | ❌ |
| `risk_filter.get_event_schedule` | HHKDB669100C0 + HHKDB669102C0 | 예탁원정보(유상증자 + 배당일정) | ❌ |
| `portfolio.get_open_positions` | (v2 잔고 재사용) TTTC8434R | 주식잔고조회 | ✅ VTTC8434R |
| `portfolio.get_daily_pnl` | (v2 잔고 재사용) TTTC8434R | 주식잔고조회 | ✅ VTTC8434R |

> 모의 미지원 ❌ 항목은 **데이터 모드가 실전 도메인(`KIS_DATA_MODE=real`, 현재 기본값)이므로 문제없음**.
> 데이터 모드를 paper로 바꾸면 위 11개 도구가 전부 깨진다 — 절대 변경 금지.

### 1-2. v2 Safety Layer / broker (재사용)

| 컴포넌트 | TR ID | 문서상 API 명 | 모의지원 | 상태 |
|----------|-------|---------------|:---:|------|
| OrderManager 매수 | **TTTC0802U** | **문서에서 사라짐** | (구 VTTC0802U) | ⚠️ 아래 2-1 참조 |
| OrderManager 매도 | **TTTC0801U** | **문서에서 사라짐** | (구 VTTC0801U) | ⚠️ 아래 2-1 참조 |
| 정정/취소 | TTTC0013U | 주식주문(정정취소) | ✅ VTTC0013U | 정상 |
| 잔고조회 | TTTC8434R | 주식잔고조회 | ✅ VTTC8434R | 정상 |
| 미체결(정정취소가능)조회 | TTTC0084R | 주식정정취소가능주문조회 | **❌ 모의 미지원** | ⚠️ 아래 2-2 참조 |
| 현재가 (market_data) | FHKST01010100 | 주식현재가 시세 | ✅ | 정상 |
| 투자자 수급 (get_investor_flow) | FHKST01010900 | 주식현재가 투자자 | ✅ | 정상 |
| 지수 일봉 | FHKUP03500100 | 국내주식업종기간별시세 | ✅ | 정상 |
| 일봉 (전일 거래대금) | FHKST03010100 | 국내주식기간별시세 | ✅ | 정상 (버그 수정됨) |
| 상품기본조회 | CTPF1002R | 주식기본조회 | ❌ | 정상 (real 데이터모드) |
| 휴장일 체크 (market_calendar 3순위) | CTCA0903R 계열 chk-holiday | 국내휴장일조회 | — | 정상 |

### 1-3. 뉴스/공시 수집 (KIS 외 포함)

| 경로 | 소스 | 비고 |
|------|------|------|
| MIL `get_news_market` / `get_news_stock` | KIS FHKST01011800 | v3 TradingAgent가 사용 |
| `broker/telegram_news.py` (mqk-telegram-news) | 텔레그램 뉴스 채널 (Telethon 세션) | KIS 아님, 별도 PM2 앱 |
| `codes/disclosure_fetcher.py` | DART OpenAPI | KIS 아님 |
| `codes/news_fetcher.py` / `agents/news_agent.py` | v2 경로 | v3 흐름 사용 여부 라이브 테스트 3-3에서 확인 |

---

## 2. 문서 대조에서 발견된 문제 (라이브 테스트 전 확인 필수)

### 2-1. ⚠️⚠️ 주문 TR ID가 신규 버전으로 교체됨 (최우선)

2026-05-28 문서 기준 `주식주문(현금)`의 TR은:
- 매수: **TTTC0012U** (모의 VTTC0012U) / 매도: **TTTC0011U** (모의 VTTC0011U)

코드(`broker/kis_api.py:680-684`)는 구버전 **TTTC0802U/TTTC0801U**를 사용 중이며, 이 TR은 현행 문서에서 **완전히 사라졌다** (KRX/NXT/SOR 통합 개편으로 추정). 구 TR이 아직 동작할 수 있으나 폐기 예고 상태일 가능성이 높다.

**조치:** 라이브 테스트 Level 0에서 paper 주문 1건으로 구 TR 동작 여부를 가장 먼저 확인. 실패하거나 경고 응답이 오면 신 TR로 마이그레이션 후 테스트 재개. **주문이 안 되면 시스템 전체가 무의미하므로 다른 모든 테스트보다 앞선다.**

> **D0 결과 (2026-06-11 21:50 KST, 장외):** paper 매수 주문 시도 → `"모의투자 장종료 입니다"` 응답.
> 서버가 구 TR(VTTC0802U)을 인식하고 장운영시간 검증까지 도달 → **구 TR 아직 유효**.
> 접수→취소 전체 검증은 D1 장중 재실행 필요. 추가 발견: `_order_admin_mode` 기본값이 real이라
> paper 모드에서 미체결조회/취소가 실전 서버로 향함 — D1에서 paper 주문 취소 가능 여부 확인 필수.

### 2-2. ⚠️ TTTC0084R(미체결 조회)은 모의투자 미지원

코드(`broker/kis_api.py:598`)는 paper 모드에서 `VTTC0084R`로 분기하지만, 문서상 이 API는 **모의투자 미지원** (VTTC0084R 항목 없음). paper 모드 테스트에서 미체결 조회가 실패할 수 있다.

**조치:** Level 0에서 paper 모드로 1회 호출해 실패 양상 확인. 실패 시 해당 기능은 graceful 처리(빈 목록 반환)되는지 검증하고, 실전 전환 시에만 유효한 기능으로 문서화.

### 2-3. 참고: 주식주문(신용), 예약주문 등은 미사용

현물 현금주문만 사용하는 현 설계와 일치. 문제없음.

---

## 3. 갭 분석 — 추가 도입 후보 (미사용 113개 중 v3 설계 목적 관련)

> **2026-06-11 구현 완료:** 아래 후보 14개 API 전부 구현·라이브 검증됨 (커밋 `66977c0`, `dd1abc6`, `5802c92`).
> 배분: get_market_context(+프로그램매매/투자자동향), get_top_movers(+체결강도/등락률),
> get_stock_status(+상하한가), get_event_schedule(+무상증자/합병분할/주주총회),
> 신규 17번째 MIL 도구 get_fundamentals(재무비율/손익계산서/대차대조표/투자의견, SCAN 전용),
> Safety Layer KISApi.get_buyable_cash + _process_v3_buy_proposal 현금 가드,
> KISApi.get_daily_minute_candles(회고용).
> 추가 라이브 발견 버그 3건도 수정됨 (`1623bd5`): get_flow URL/필드, get_sector_breadth 파라미터/이중계산, get_event_schedule URL/날짜.

v3 설계(SEPA + 오후 회복 포착 + RED 방어 평가)의 신호 요구사항과 대조한 결과. **현재 16개 도구로 설계는 완결**이며, 아래는 신호 품질을 높일 선택적 후보다.

### 우선순위 높음 — 설계 문서에 언급됐으나 미구현인 신호

| TR ID | API 명 | 매핑되는 설계 신호 | 제안 |
|-------|--------|--------------------|------|
| FHPST01680000 | 국내주식 체결강도 상위 | INTRADAY_RECOVERY — "오후 거래대금 가속" | `get_top_movers` 보완 또는 신규 도구 |
| FHPPG04600101 | 프로그램매매 종합현황(시간) | 스펙의 "프로그램 수급 개선" 신호 — **현재 미커버** | `get_market_context`에 통합 |
| FHPTJ04040000 | 시장별 투자자매매동향(일별) | 외인/기관 수급 "전환" 판단 (현재는 종목가집계 스냅샷만) | `get_market_context` 보강 |

### 우선순위 중간 — 기존 도구 확장

| TR ID | API 명 | 용도 |
|-------|--------|------|
| FHPST01700000 | 등락률 순위 | RS 스크리닝 — 거래량순위와 교차 검증 |
| FHKST130000C0 | 상하한가 포착 | risk_filter 보강 (상한가 추격 방지) |
| HHKDB669101C0 / 669104C0 / 669111C0 | 예탁원정보(무상증자/합병분할/주주총회) | `get_event_schedule`이 유상증자+배당만 커버 — 이벤트 리스크 확대 |
| TTTC8908R | 매수가능조회 (모의 VTTC8908R 지원) | 주문 직전 가용현금/증거금 확인 — PositionSizer 안전망 보강 |

### 우선순위 낮음 — 장기 (SEPA 펀더멘털 단계)

| TR ID | API 명 | 용도 |
|-------|--------|------|
| FHKST66430300 / 66430200 / 66430100 | 재무비율 / 손익계산서 / 대차대조표 | SEPA 펀더멘털 필터 (현재 설계 범위 밖) |
| FHKST663300C0 | 종목투자의견 | 보조 참고 |
| FHKST03010230 | 주식일별분봉조회 (과거일 분봉) | 회고 분석/백테스트 |

**결론:** 즉시 수정이 필요한 것은 2-1(주문 TR)과 2-2(미체결 조회 모의 미지원) 확인뿐이다. 갭 후보 도입은 라이브 테스트 완료 후 별도 결정 사항.
