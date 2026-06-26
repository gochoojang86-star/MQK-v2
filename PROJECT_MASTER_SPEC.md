# MQK v3 Master Spec

## Scope

현재 운영 기준은 `MQK v3` 단일 런타임이다.

활성 진입점:
- `orchestrator_v3.py`
- `run_schedule_v3.py`
- `ecosystem.config.cjs`

비활성/퇴역 문서는 `docs/legacy/` 및 `docs/superpowers/`의 과거 기록으로 분리한다.

---

## Runtime Principles

- `LLM`은 해석, 판단, 비교, 스토리 클러스터링을 담당한다.
- `Code`는 데이터 수집, 리스크 가드레일, 주문 실행, 상태 저장을 담당한다.
- `Regime`은 하나의 시장 상태 지표일 뿐이며, 각 phase의 매매를 직접 지시하지 않는다.
- `Scan`은 후보 풀을 압축한 뒤 LLM이 동일 테마 내부에서 본류/서브/후발을 해석한다.
- `Intraday`는 분봉, 거래대금 유지, 수급, 테마 지속성을 기반으로 실제 진입 여부를 판단한다.
- `Market Close`는 거래 유무와 무관하게 복기와 자기개선 루프를 수행한다.

---

## Active Schedule

- `holiday_check`: 00:30
- `premarket_early`: 08:45
- `premarket_first`: 08:50
- `premarket`: 09:03 / 11:03 / 13:03
- `scan`: 09:17 / 11:17 / 13:17 / 15:00
- `intraday`: 09:00~14:50, 10분 간격
- `late_intraday`: 15:08 / 15:13
- `close`: 15:18
- `market_close`: 17:00

세부 PM2 설정은 [`ecosystem.config.cjs`](./ecosystem.config.cjs) 기준이다.

---

## Core State Files

- `data/watchlist.json`
- `data/last_regime_v3.json`
- `data/next_day_prior.json`
- `logs/debug/<date>/scan_v3.json`
- `logs/debug/<date>/daily_reflection.json`
- `logs/debug/<date>/self_improvement_review.json`

---

## LLM Provider

기본 provider는 `openai`이며, `openrouter`는 환경변수 스위치로 전환 가능하다.

핵심 설정:
- `LLM_PROVIDER=openai|openrouter`
- `OPENROUTER_API_KEY`
- `OPENROUTER_BASE_URL`
- `OPENROUTER_HTTP_REFERER`
- `OPENROUTER_APP_TITLE`

---

## Docs Policy

- 현재 운영 기준 문서는 루트 `README.md`, 본 문서, `docs/README.md`만 우선 참조한다.
- 과거 설계/실험 문서는 참고용이며, 현재 활성 런타임의 진실의 원천이 아니다.
