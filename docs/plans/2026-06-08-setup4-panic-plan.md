# Setup 4 Panic Reversal Plan

**Goal:** MQK-v2가 `status=RED` 환경에서도 극단적 과매도 구간에서는 제한된 평균회귀 셋업(Setup 4)만 탐지·평가·집행할 수 있도록 구조를 확장한다.

**Non-Goal:** RED 상태의 일반 추세추종 매매 허용, 물타기 허용, 손실 한도 완화.

---

## 현재 상태 진단

현재 MQK-v2는 하락장에서 "기회를 놓친다"기보다 "의도적으로 완전 정지"하도록 설계되어 있다.

핵심 병목:

1. `orchestrator.py`
   - `status == "RED"`면 스캔 자체를 중단한다.
2. `codes/scanner.py`
   - 신고가/VCP/박스돌파 중심이라 낙주 후보를 구조적으로 찾지 못한다.
3. `prompts/agents/portfolio_manager.md`
   - RED면 신규 BUY를 강하게 제한한다.
4. `codes/risk_officer.py`
   - 전략 타입을 모르므로 Setup 4 전용 더 보수적인 리스크 프로파일을 적용할 수 없다.

정리하면 문제는 "Regime Agent가 RED를 준다"가 아니라, `RED == 전 전략 영구 중단`이라는 단일 해석에 있다.

---

## 설계 원칙

1. `RED`는 유지한다.
   - 시장 리스크가 높다는 의미를 흐리지 않는다.
2. `PANIC`은 상태가 아니라 기회 모드다.
   - `status`와 별도 축으로 분리한다.
3. 추세추종 스캐너와 낙주 스캐너는 분리한다.
   - 같은 점수 함수에 억지로 공존시키지 않는다.
4. Setup 4는 일반 전략보다 더 작고 짧게 운용한다.
   - 예외 허용이 아니라, 예외 상황 전용 제한 전략이다.
5. 리뷰/로그에서 일반 전략과 분리 추적한다.
   - 성과와 실패 원인이 섞이면 개선이 불가능하다.

---

## 목표 아키텍처

장전 판단 결과를 다음처럼 확장한다.

```json
{
  "status": "GREEN|YELLOW|RED",
  "regime": "UPTREND|DOWNTREND|SIDEWAYS|THEME_MARKET|POLICY_MARKET|EARNINGS_MARKET|RISK_OFF",
  "confidence": 0,
  "reason": "",
  "risk_notes": [],
  "opportunity_mode": "NORMAL|SETUP4_PANIC",
  "scanner_mode": "TREND|REVERSAL_ONLY"
}
```

의미:

- `status`
  - 시장 리스크 신호
- `opportunity_mode`
  - 허용 가능한 전략 종류
- `scanner_mode`
  - 스캐너 실행 경로

핵심 해석:

- `GREEN/YELLOW + NORMAL + TREND`
  - 기존 전략 유지
- `RED + NORMAL + TREND`
  - 스캔 중단
- `RED + SETUP4_PANIC + REVERSAL_ONLY`
  - Setup 4 전용 스캔/평가 허용

---

## 구현 범위

### 1. Regime Agent

**Files**
- `agents/regime_agent.py`
- `prompts/agents/regime_agent.md`
- `tests/test_orchestrator.py`

**변경**
- `RegimeJudgment`에 `opportunity_mode`, `scanner_mode` 추가
- 프롬프트에 다음 판단 기준 추가:
  - 단순 약세와 극단적 공포를 구분
  - `status=RED`여도 Setup 4 허용 여부를 별도 판단
- 출력 JSON 스키마 확장

**판단 기준 초안**
- 전일 또는 최근 2거래일 기준:
  - KOSPI/KOSDAQ 급락
  - 하락 종목 수가 상승 종목 수를 현저히 초과
  - 거래대금 확대와 동반된 투매
  - 섹터 전반 동반 급락

**중요**
- 당장 신용융자 잔고 같은 외부 데이터가 없으므로 v1 기준에서 제외한다.
- v1은 현재 이미 보유한 지수/시장 breadth 데이터만 사용한다.

### 2. Orchestrator 게이트

**Files**
- `orchestrator.py`
- `tests/test_orchestrator.py`

**변경**
- `run_premarket()` 저장 JSON에 신규 필드 기록
- `run_scan()` 분기 수정:
  - `RED + NORMAL` -> 기존처럼 차단
  - `SETUP4_PANIC` -> `scan_reversal(...)` 실행

**설계**
- `status`만 보고 차단하지 않는다.
- 실제 실행 분기는 `scanner_mode` 기준으로 통일한다.

예시:

```python
if market_status.get("scanner_mode") == "REVERSAL_ONLY":
    candidates = self._scanner.scan_reversal(...)
elif market_status.get("status") == "RED":
    ...
else:
    candidates = self._scanner.scan(...)
```

### 3. Scanner 분리

**Files**
- `codes/scanner.py`
- `codes/technical.py`
- `config/settings.py`
- `tests/test_scanner.py`

**변경**
- `Scanner.scan()`은 그대로 유지
- `Scanner.scan_reversal()` 신규 추가
- `CandidateScore`에 전략 타입과 reversal 메타데이터 추가

예시 필드:

```python
strategy_type: str = "TREND"
reversal_score: float = 0.0
disparity20_pct: float = 0.0
disparity60_pct: float = 0.0
oversold_reason: str = ""
```

**Setup 4 후보 조건 v1**
- 거래정지/관리 제외
- 최소 거래대금 통과
- RSI <= 30
- 20일선 또는 60일선 대비 음의 이격 확대
- 최근 낙폭이 큰 종목
- 섹터/시장 내 기존 거래대금 상위권 종목 우대

**v1에서 보류**
- 반대매매 추정
- 호가/체결강도 기반 미세 진입
- 분봉 엔트리

**점수 철학**
- 신고가/돌파 점수 제거
- 과매도 강도 + 유동성 + 기존 주도성 + 반등 여지 중심

### 4. Technical 시그널 확장

**Files**
- `codes/technical.py`
- `tests/` 신규 또는 기존 확장

**변경**
- `TechnicalSignals`에 다음 필드 추가:
  - `disparity20_pct`
  - `disparity60_pct`
  - `disparity120_pct`
- 계산식:
  - `(current - ma) / ma * 100`

이 변경으로 Scanner와 PM이 같은 원천 값을 재사용할 수 있다.

### 5. Portfolio Manager 기획 수정

**Files**
- `prompts/agents/portfolio_manager.md`
- `agents/portfolio_manager.py`

**변경**
- Setup 4 판단 규칙 추가
- RED 환경에서의 일반 BUY 제한 문구는 유지
- 단, `opportunity_mode == SETUP4_PANIC`일 때는 예외적으로 다음 기준으로 평가:
  - 차트가 망가진 것이 감점 사유가 아니라 전제 조건
  - 과매도/이격/유동성/반등 여지 우선
  - 목표는 추세 전환이 아니라 짧은 기술적 반등

**프롬프트 입력 확장**
- `strategy_type`
- `opportunity_mode`
- `disparity20_pct`, `disparity60_pct`

### 6. Risk / Exit 분리

**Files**
- `codes/risk_officer.py`
- `codes/position_sizer.py`
- `codes/stop_take_profit.py`
- 관련 테스트

**변경 방향**
- Setup 4 전용 리스크 프로파일 추가
- 같은 엔진을 쓰되 전략 타입 기반 분기 허용

v1 제안:

- 포지션 크기: 일반 전략의 50%
- 동시 보유 수: 최대 1~2개
- 익절 목표: 3~5%
- 최대 보유 기간: 2~4일
- 시간 손절 강제
- 추가매수 금지 유지

**주의**
- `RiskOfficer`는 지금 순수 수학 모듈이라 좋다.
- 전략별 파라미터를 `config/settings.py`로 밀어 넣고, 의사결정은 코드가 하게 유지한다.

### 7. 로깅 / 리뷰 분리

**Files**
- `orchestrator.py`
- `codes/trade_journal.py` 또는 관련 저장 구조
- `agents/review_agent.py` 입력 경로

**변경**
- 거래 로그에 `strategy_type=SETUP4_PANIC` 저장
- 장마감 리뷰에서 일반 전략과 분리 집계 가능하게 한다.

---

## 설정 추가안

`config/settings.py`

```python
@dataclass(frozen=True)
class ReversalConfig:
    enabled: bool = True
    rsi_threshold: float = 30.0
    min_disparity20_pct: float = -8.0
    min_disparity60_pct: float = -12.0
    max_positions: int = 2
    position_size_multiplier: float = 0.5
    take_profit_pct: float = 4.0
    max_holding_days: int = 4
```

장점:
- Setup 4 정책을 일반 리스크 설정과 분리 가능
- 런타임 오버라이드 구조와 잘 맞음

---

## 단계별 작업 순서

### Phase A. 스키마와 게이트

- [ ] `RegimeJudgment` 확장
- [ ] `market_status.json` 신규 필드 저장
- [ ] `run_scan()` 분기 추가
- [ ] 오케스트레이터 테스트 갱신

**완료 기준**
- RED더라도 `scanner_mode=REVERSAL_ONLY`면 스캔이 호출된다.

### Phase B. 데이터 준비

- [ ] `TechnicalSignals`에 이격도 필드 추가
- [ ] 관련 테스트 작성

**완료 기준**
- 스캐너와 PM이 동일한 이격도 값을 사용한다.

### Phase C. reversal 스캐너

- [ ] `scan_reversal()` 구현
- [ ] reversal score 설계
- [ ] 일반 스캐너와 테스트 분리

**완료 기준**
- 추세 스캐너와 낙주 스캐너가 독립적으로 동작한다.

### Phase D. PM 예외 정책

- [ ] PM 프롬프트에 Setup 4 규칙 추가
- [ ] context에 `strategy_type`, `opportunity_mode` 전달

**완료 기준**
- PM이 RED 환경에서도 Setup 4 후보를 일관되게 평가한다.

### Phase E. 리스크/청산

- [ ] Setup 4 전용 포지션 제한
- [ ] 시간 손절/보유일 제한
- [ ] 익절 로직 분기

**완료 기준**
- Setup 4는 일반 전략보다 항상 더 보수적으로 실행된다.

### Phase F. 리뷰/로그

- [ ] 거래 로그에 전략 타입 저장
- [ ] 리뷰 집계 분리

**완료 기준**
- Setup 4 성과를 일반 전략과 분리 분석할 수 있다.

---

## 테스트 계획

### 오케스트레이터

- [ ] `RED + NORMAL`이면 스캔 중단
- [ ] `RED + SETUP4_PANIC`이면 `scan_reversal()` 호출
- [ ] `market_status.json`에 신규 필드 저장

### Technical

- [ ] MA 존재 시 이격도 계산 검증
- [ ] MA 부재 시 0.0 또는 안전한 기본값 처리

### Scanner

- [ ] RSI/이격도 미달 종목 제외
- [ ] 과매도지만 유동성 부족 종목 제외
- [ ] 기존 거래대금/주도성 높은 종목 우선
- [ ] 일반 `scan()` 동작 회귀 없음

### Risk / Exit

- [ ] Setup 4 포지션 크기 축소 적용
- [ ] 최대 보유일 초과 시 청산
- [ ] 추가매수 금지 유지

---

## 리스크와 대응

1. 너무 이른 칼날받기
   - 대응: v1은 스캔까지만 허용하고, 실제 진입은 PM + Risk 이중 필터 유지

2. 잡주/유동성 함정 편입
   - 대응: 기존 거래대금 기준 유지, 기존 주도성 우대

3. RED 의미 훼손
   - 대응: `status`는 유지하고 `opportunity_mode`로 분리

4. 일반 전략 성과와 혼합
   - 대응: 거래 로그에 전략 타입 저장

---

## 우선순위 제안

가장 먼저 손댈 곳:

1. `orchestrator.py`
2. `agents/regime_agent.py` + 프롬프트
3. `codes/technical.py`
4. `codes/scanner.py`

이 순서가 맞는 이유:

- 게이트를 먼저 열지 않으면 나머지 설계가 시스템에 반영되지 않는다.
- 이격도/과매도 데이터가 준비돼야 reversal 스캐너와 PM이 같은 사실을 본다.

---

## 이번 기획의 결론

정답은 "RED를 약하게 만들기"가 아니다.

정답은:

- `RED`는 그대로 두고
- `Setup 4 허용 여부`를 별도 축으로 분리하고
- `추세추종 파이프라인`과 `낙주 평균회귀 파이프라인`을 분리하고
- 리스크는 일반 전략보다 더 강하게 묶는 것이다.

이 구조면 MQK-v2는 하락장에서 무작정 멈추는 시스템이 아니라, 공포 구간에서 제한된 전술만 허용하는 시스템으로 진화할 수 있다.
