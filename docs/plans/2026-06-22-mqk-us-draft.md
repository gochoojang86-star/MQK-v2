# MQK-US Draft

**Goal:** `MQK v3`의 soul과 운영 철학을 유지하되, 한국장 특화 로직과 완전히 분리된 미국장 전용 `MQK-US` 독립 버전을 설계한다.

**Execution Broker:** 주문/잔고/체결은 `KIS` 해외주식 API를 사용한다.

**Most Important Constraint:** API 호출 데이터는 `껍데기`를 쓰지 않는다.

- 허용:
  - 공식 API 원문
  - 브로커 원시 시세/호가/분봉/순위/잔고
  - 공식 공시/공식 원문 피드
- 금지:
  - 웹페이지 요약 HTML 파싱 위주의 의사 데이터
  - 제3자가 가공한 “요약 카드”만 받아오는 래퍼
  - 시세 원천이 불명확한 무료 BFF/스크래핑 결과
  - LLM이 기사 제목만 보고 “earnings beat” 같은 사실을 추정하는 흐름

---

## 1. 왜 독립 버전이어야 하나

`MQK v3`는 soul 자체는 재사용 가치가 높지만, 구현은 국장 특화가 너무 강하다.

- `KIS psearch`, `키움 테마`, `국장 breadth`, `정책 테마`, `투자경고/단기과열` 의존성이 큼
- 장중 리더 판단도 한국형 거래대금/테마 순환을 강하게 전제함
- 장전/장중/장마감 시간 구조도 미장과 다름

따라서 `MQK-US`는 `v3`를 포팅하는 게 아니라, 아래만 재사용하는 독립 버전이 맞다.

- `soul.md`의 핵심 철학
- LLM 기반 `Regime -> Scan -> Intraday -> Close` 흐름
- 리스크/주문/로그 구조

버려야 할 것:

- 국장 전용 조건검색 전제
- 국장 테마 API 결합
- 국장 투자주의/정책 모멘텀 중심의 프롬프트

---

## 2. MQK-US 핵심 철학

미국장에서는 아래 순서가 더 중요하다.

1. 오늘 돈이 어디에 붙는가
2. 그 흐름이 `섹터 ETF/메가캡/실적 촉매` 기반인가
3. 섹터 안에서 진짜 리더가 누구인가
4. 지금 자리가 `확장 끝 추격`인가, `첫 눌림/재확장`인가
5. overnight risk와 event risk를 감수할 가치가 있는가

즉 `MQK-US`는:

- 단순 모멘텀 추격 봇이 아니라
- `섹터 리더 + 이벤트 품질 + 적정 진입가`
를 같이 보는 구조여야 한다.

---

## 3. v1 범위

### 포함

- 미국 주식 현물 매매
- 장중/장전 레짐 판단
- 섹터/서브그룹 리더 탐색
- 리더주 눌림/재확장 진입 판단
- 장마감 복기와 다음날 prior 생성

### 제외

- 옵션
- 프리마켓/애프터마켓 자동주문
- 저유동성 소형주 스캘핑
- 뉴스 해석만으로 하는 초단타
- 비공식 무료 스크래핑 기반 earnings calendar

---

## 4. 데이터 소스 원칙

### A. 브로커/시세 원천: KIS 해외주식 API

KIS 문서에서 미국장 v1에 직접 필요한 공식 시트:

- `해외주식 현재체결가`
- `해외주식 현재가상세`
- `해외주식 복수종목 시세조회`
- `해외주식분봉조회`
- `해외주식 기간별시세`
- `해외주식 현재가 호가`
- `해외주식 실시간호가`
- `해외주식 실시간지연체결가`
- `해외주식 업종별코드조회`
- `해외주식 업종별시세`
- `해외주식 거래대금순위`
- `해외주식 거래량순위`
- `해외주식 거래량급증`
- `해외주식 거래증가율순위`
- `해외주식 시가총액순위`
- `해외주식 상승율_하락율`
- `해외주식 가격급등락`
- `해외주식 신고_신저가`
- `해외주식 매수체결강도상위`
- `해외주식조건검색`
- `해외주식 상품기본정보`
- `해외주식 매수가능금액조회`
- `해외주식 잔고`
- `해외주식 체결기준현재잔고`
- `해외주식 주문`
- `해외주식 정정취소주문`
- `해외주식 미체결내역`
- `해외주식 주문체결내역`
- `해외지수분봉조회`
- `해외뉴스종합(제목)`
- `해외속보(제목)`

**v1 원칙**

- 시세/호가/분봉/주문/잔고/랭킹은 가능하면 KIS 단일 원천으로 간다.
- 랭킹/조건검색이 단순 껍데기가 아니라 브로커 원문이면 사용 가능하다.
- `KIS가 안 주는 데이터`만 외부 원천을 추가한다.

### B. 기업 이벤트 원천: SEC EDGAR

미국장에선 `실적/가이던스/8-K`가 너무 중요하다.

따라서 뉴스 요약 대신 아래 공식 원문 계층이 필요하다.

- SEC `submissions`
- SEC `companyfacts`
- SEC `filings` (`8-K`, `10-Q`, `10-K`, 필요 시 `6-K`)

**왜 필요한가**

- “실적 발표 전후인지”
- “가이던스/공급계약/리콜/조달 이슈가 있는지”
- “뉴스 제목 과열인지 실제 공시인지”
를 구분해야 한다.

**v1 적용 범위**

- 종목별 최근 8-K/10-Q/10-K 존재 여부
- 장전/장후 실적 발표 직후 공시 여부
- 최근 중대 이벤트 플래그

### C. 거시 레짐 보조 원천

가능하면 v1에서는 외부 매크로 API를 최소화한다.

우선은 `KIS 해외지수분봉조회 + KIS 미국주식 시세`로 다음을 커버한다.

- `SPY`, `QQQ`, `IWM`
- `SMH`, `XLF`, `XLV`, `XLI`, `XLE`, `XBI`
- `NVDA`, `MSFT`, `AAPL`, `AMZN`, `META`

즉 broad index + sector ETF + mega-cap leader만으로도
당일 미장 자금 흐름의 상당 부분을 읽을 수 있다.

필요 시 v2에서만 아래 추가를 검토한다.

- FRED 금리/달러/유동성 지표
- CBOE/VIX 공식 데이터

### D. v1에서 의도적으로 안 쓰는 것

- Yahoo Finance HTML scraping
- Finviz scraping
- TradingView web scraping
- 불명확한 무료 earnings calendar aggregator
- X/Twitter 신호
- LLM이 기사 제목만 읽고 실적 사실을 추정하는 흐름

---

## 5. MQK-US 기능 아키텍처

### 5.1 US Regime Agent

목표:

- 오늘이 `broad risk-on`, `mega-cap concentration`, `sector rotation`, `risk-off` 중 무엇인지 판별

핵심 입력:

- `SPY`, `QQQ`, `IWM` 분봉/당일 변화
- `SMH`, `XLF`, `XLV`, `XLI`, `XLE`, `XBI` 상대강도
- `NVDA`, `MSFT`, `AAPL`, `AMZN`, `META` 거래대금/방향성
- 전일 마감 prior
- 필요한 경우 최근 SEC 이벤트 플래그

핵심 출력:

- `status`: `GREEN | YELLOW | RED`
- `regime`: `BROAD_TREND | MEGACAP_CONCENTRATION | SECTOR_ROTATION | EARNINGS_MARKET | RISK_OFF`
- `leadership_map`
- `risk_guidance`

### 5.2 US Scan

목표:

- 오늘 돈이 붙는 상위 섹터/서브그룹을 찾고
- 각 그룹의 진짜 리더와 동행 강세주만 추린다

핵심 입력:

- KIS 해외주식 조건검색
- 거래대금/거래량/체결강도/신고가 계열 랭킹
- sector ETF 상대강도
- KIS 업종별코드/업종별시세
- KIS 상품기본정보
- SEC 최근 이벤트 플래그

핵심 원칙:

- 상위 2~3개 섹터까지만 유지
- 섹터당 `본류 대장 1개`
- 거래대금이 압도적인 본류 섹터만 `동행 강세주 1개` 추가 허용
- 소형주/저유동성 급등주는 되도록 제외

### 5.3 US Intraday

목표:

- 장중에 “강한 종목”이 아니라 “지금 사도 되는 리더”를 고른다

핵심 입력:

- watchlist 메타데이터
- 복수 종목 시세/분봉/호가
- 체결강도/거래량 급증/신고가 근접
- sector ETF와 leader stock 동행성
- SEC 이벤트 플래그

핵심 판단:

- 본류 리더인가
- 확장 끝 추격인가
- 첫 눌림/재확장인가
- 장전 실적/가이던스 이후 continuation인가
- overnight로 가져갈 이유가 있는가

### 5.4 US Close / Review

목표:

- 오늘 리더 섹터가 지속형이었는지 일회성이었는지 기록
- 내일 prior 생성
- 잘못 산 종목이 “후발주”였는지 “대장인데 자리 실수”였는지 구분

---

## 6. v1 구현 우선순위

### Phase 1. 독립 껍데기 만들기

- `mqk_us/` 또는 `us/` 최상위 독립 패키지
- 국장판 `v3`와 import/runtime 분리
- `run_schedule_us.py`
- `orchestrator_us.py`
- `agents/us_regime_agent.py`
- `agents/us_trading_agent.py`

### Phase 2. KIS 해외주식 market layer

- 현재가/상세가/복수종목시세
- 분봉/기간별시세
- 호가
- 업종별코드/업종별시세
- 랭킹군
- 조건검색
- 주문/정정취소/미체결/잔고/매수가능금액

### Phase 3. SEC event layer

- ticker -> cik 매핑
- 최근 filings 캐시
- `recent_material_event`, `recent_earnings_filing`, `recent_guidance_flag`

### Phase 4. US prompts

- `us_regime_agent.md`
- `us_scan.md`
- `us_intraday.md`
- `us_market_close.md`

### Phase 5. paper trading first

- KIS 해외주식 모의/실전 가능 범위 확인
- live smoke
- small dry-run equivalent validation

---

## 7. 절대 타협하지 않을 기준

1. raw source 없으면 기능을 열지 않는다.
2. 뉴스 요약만으로 실적 이벤트를 추정하지 않는다.
3. KIS가 주는 원문 랭킹/조건검색은 사용 가능하지만, 웹 페이지 껍데기 파싱은 금지한다.
4. 섹터 리더 판단은 `ETF/업종/리더주/거래대금` 4개 축을 같이 본다.
5. 국장판 v3 로직을 억지로 재사용하지 않는다.

---

## 8. 추천 시작점

`MQK-US v1`은 아래만 먼저 되면 된다.

- KIS 해외주식 현재가/분봉/복수종목/호가
- KIS 해외주식 랭킹/조건검색
- KIS 해외주식 주문/잔고/매수가능금액
- SEC 최근 공시 플래그
- 미국장 전용 `Regime -> Scan -> Intraday` 프롬프트

이 조합이면 “껍데기 없는 미장 초안”으로는 충분히 의미 있다.

반대로 아래가 준비되기 전엔 열지 않는 게 낫다.

- 실적/중대 이벤트 truth source 부재
- 시세가 원문이 아닌 비공식 scraping
- 주문 가능 금액/잔고 검증 부재
