# MQK-v2

한국형 테마 스윙 마스터들의 철학을 가진 멀티 Agent 자율 트레이더.

**Agent는 사고하고, Code는 생존을 보장한다.**

## 구조

```
MQK-v2/
├── agents/          # LLM 기반 판단 Agent
├── codes/           # Code 기반 계산/검증 엔진
├── broker/          # KIS API / Telegram
├── llm/             # LLM 클라이언트 유틸
├── config/          # 전역 설정 (리스크 파라미터)
├── data/            # 시장 데이터
├── logs/            # 운영 로그
├── backtest/        # 백테스트 엔진
└── tests/           # 테스트
```

## 설치

```bash
pip install -r requirements.txt
```

## 설정

1. `.env` 생성 (gitignore 적용됨):
```bash
KIS_MODE=paper
KIS_PAPER_APP_KEY=...
KIS_PAPER_APP_SECRET=...
KIS_PAPER_ACCOUNT=...
KIS_REAL_APP_KEY=...
KIS_REAL_APP_SECRET=...
KIS_REAL_ACCOUNT=...
KIS_UNIVERSE=005930,000660
OPENAI_API_KEY=...
NAVER_CLIENT_ID=...
NAVER_CLIENT_SECRET=...
DART_AUTH_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
ORDER_DRY_RUN=true
```

2. 리스크 파라미터: `config/settings.py`

## 스펙

[PROJECT_MASTER_SPEC.md](PROJECT_MASTER_SPEC.md) 참조.
