# US Trading Minervini 스윙매매 봇 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** KIS 해외주식 API + yfinance 기반으로 미네르비니 SEPA 페르소나를 주입한 2단계 LLM 스윙매매 봇 구축

**Architecture:** MQK-v2를 fork해 MQK-US 독립 레포 생성. 1단계 LLM(ScreenerAgent)이 S&P500+Nasdaq100 전 종목에서 Trend Template으로 워치리스트 20~30개 추출, 2단계 LLM(TraderAgent)이 VCP 패턴 판단 후 KIS 해외주식 API로 주문 실행. 3-phase 스케줄(ET 기준, DST 자동 처리).

**Tech Stack:** Python 3.11+, yfinance, anthropic (또는 openai), pandas, pandas_market_calendars, python-dotenv, pytest, PM2

## Global Constraints

- 레포 경로: `/mnt/c/Users/gocho/MQK-US/`
- 소스 참조: MQK-v2 (`/mnt/c/Users/gocho/MQK-v2/`) — 복사 후 미장 전용으로 개조
- 최대 3포지션, 포지션당 ~33% 자본
- 코드 가드 없음 — LLM이 매매 판단 90%+, 코드는 데이터/호출/실행만
- DST 처리: `zoneinfo.ZoneInfo("America/New_York")` — ET 기준 시간창 가드
- 모든 주문은 초기에 `DRY_RUN=true` 환경변수로 보호
- 테스트: 외부 API 호출은 `unittest.mock.patch`로 목킹
- 커밋: 태스크 완료 시마다 커밋

---

## 파일 구조

```
MQK-US/
├── orchestrator_us.py          # 3-phase 스케줄러 + DST 시간창 가드
├── run_schedule_us.py          # PM2 진입점 (phase 인자 받아 orchestrator 호출)
├── agents/
│   ├── __init__.py
│   ├── screener_agent.py       # 1단계 LLM: Trend Template 스크린
│   └── trader_agent.py         # 2단계 LLM: VCP 판단 + 진입/청산
├── data/
│   ├── __init__.py
│   └── market_data.py          # yfinance 래퍼 (유니버스, 지표, 뉴스)
├── broker/
│   ├── __init__.py
│   ├── kis_us_api.py           # KIS 해외주식 API (현재가/주문/잔고)
│   └── telegram.py             # Telegram 알림 (MQK-v2에서 간소화 복사)
├── llm/
│   ├── __init__.py
│   └── client.py               # LLM 클라이언트 (MQK-v2에서 복사)
├── config/
│   ├── __init__.py
│   └── settings.py             # 전역 설정 (포지션 한도, 모델 등)
├── prompts/
│   ├── screener_persona.md     # 미네르비니 스크리너 페르소나
│   └── trader_persona.md       # 미네르비니 트레이더 페르소나
├── data_store/
│   └── .gitkeep
├── tests/
│   ├── conftest.py
│   ├── test_market_data.py
│   ├── test_screener_agent.py
│   ├── test_trader_agent.py
│   ├── test_kis_us_api.py
│   └── test_orchestrator_us.py
├── docs/
│   └── kis_us_api_inventory.md
├── .env.example
├── requirements.txt
└── ecosystem.config.cjs
```

---

## Task 1: 레포 Fork + 프로젝트 스캐폴딩

**Files:**
- Create: `/mnt/c/Users/gocho/MQK-US/` (전체 디렉토리)
- Create: `requirements.txt`
- Create: `config/settings.py`
- Create: `.env.example`
- Copy: `llm/client.py` (MQK-v2에서)

**Interfaces:**
- Produces: `config.settings.Settings` — `max_positions=3`, `dry_run=bool`

- [ ] **Step 1: MQK-v2 기반으로 새 레포 생성**

```bash
cp -r /mnt/c/Users/gocho/MQK-v2 /mnt/c/Users/gocho/MQK-US
cd /mnt/c/Users/gocho/MQK-US
```

- [ ] **Step 2: 미장과 무관한 파일 제거**

```bash
cd /mnt/c/Users/gocho/MQK-US
rm -rf agents/ market_intelligence/ backtest/ codes/ prompts/ run_psearch_watcher.py
rm -rf orchestrator.py orchestrator_v3.py run_schedule.py run_schedule_v3.py
rm -rf PROJECT_MASTER_SPEC.md
# 테스트도 초기화
rm -rf tests/__pycache__ tests/test_*.py
```

- [ ] **Step 3: 디렉토리 구조 생성**

```bash
cd /mnt/c/Users/gocho/MQK-US
mkdir -p agents data broker llm config prompts data_store tests docs
touch agents/__init__.py data/__init__.py broker/__init__.py
touch llm/__init__.py config/__init__.py tests/__init__.py
touch data_store/.gitkeep
```

- [ ] **Step 4: requirements.txt 작성**

`/mnt/c/Users/gocho/MQK-US/requirements.txt`:
```
yfinance>=0.2.50
pandas>=2.0.0
pandas_market_calendars>=4.3.0
anthropic>=0.40.0
python-dotenv>=1.0.0
requests>=2.31.0
pytest>=8.0.0
pytest-mock>=3.12.0
```

- [ ] **Step 5: config/settings.py 작성**

`/mnt/c/Users/gocho/MQK-US/config/settings.py`:
```python
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent
DATA_STORE = BASE_DIR / "data_store"
PROMPTS_DIR = BASE_DIR / "prompts"


@dataclass(frozen=True)
class Settings:
    max_positions: int = 3
    position_size_pct: float = 33.0       # 포지션당 포트폴리오 비율(%)
    stop_loss_pct: float = 10.0           # 하드 스탑 -10%
    breakeven_trigger_pct: float = 10.0   # 본전 이동 트리거 +10%
    trailing_trigger_pct: float = 20.0    # 트레일링 스탑 트리거 +20%
    dry_run: bool = os.environ.get("DRY_RUN", "true").lower() == "true"
    llm_model: str = os.environ.get("LLM_MODEL", "claude-sonnet-4-6")
    screener_max_output: int = 30         # 스크리너 최대 출력 종목 수


SETTINGS = Settings()
```

- [ ] **Step 6: .env.example 작성**

`/mnt/c/Users/gocho/MQK-US/.env.example`:
```
# KIS 해외주식
KIS_REAL_APP_KEY=
KIS_REAL_APP_SECRET=
KIS_REAL_ACCOUNT=
KIS_PAPER_APP_KEY=
KIS_PAPER_APP_SECRET=
KIS_PAPER_ACCOUNT=
KIS_MODE=paper

# LLM
ANTHROPIC_API_KEY=
LLM_MODEL=claude-sonnet-4-6

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# 주문 보호 (운영 전 반드시 true)
DRY_RUN=true
```

- [ ] **Step 7: llm/client.py 복사 후 Anthropic SDK로 교체**

`/mnt/c/Users/gocho/MQK-US/llm/client.py`:
```python
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def load_prompt(name: str) -> str:
    """prompts/ 디렉토리에서 마크다운 파일 로드"""
    path = Path(__file__).parent.parent / "prompts" / f"{name}.md"
    return path.read_text(encoding="utf-8")


def call_llm(system: str, user: str, model: str | None = None) -> str:
    """LLM 호출 — 응답 텍스트 반환"""
    from config.settings import SETTINGS
    m = model or SETTINGS.llm_model
    client = _get_client()
    resp = client.messages.create(
        model=m,
        max_tokens=4096,
        messages=[{"role": "user", "content": user}],
        system=system,
    )
    return resp.content[0].text


def call_llm_json(system: str, user: str, model: str | None = None) -> dict[str, Any]:
    """LLM 호출 — JSON 파싱 결과 반환. 실패 시 1회 재시도."""
    raw = call_llm(system, user, model)
    try:
        return _parse_json(raw)
    except ValueError:
        logger.warning("JSON 파싱 실패, 1회 재시도")
        raw2 = call_llm(system, user + "\n\n반드시 JSON만 출력하세요.", model)
        return _parse_json(raw2)


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 파싱 불가: {e}\n원문: {text[:200]}")
```

- [ ] **Step 8: git 초기화 및 첫 커밋**

```bash
cd /mnt/c/Users/gocho/MQK-US
git init
echo ".env" >> .gitignore
echo "__pycache__/" >> .gitignore
echo "*.pyc" >> .gitignore
echo "data_store/*.json" >> .gitignore
git add .
git commit -m "chore: MQK-US 레포 초기화 (MQK-v2 fork 기반)"
```

Expected: `main (root-commit) ...` 커밋 성공

---

## Task 2: DataLayer — yfinance 래퍼 + 종목 유니버스

**Files:**
- Create: `data/market_data.py`
- Create: `tests/test_market_data.py`

**Interfaces:**
- Produces:
  - `get_universe() -> list[str]` — S&P500 + Nasdaq100 티커 목록
  - `get_screener_data(tickers) -> dict[str, dict]` — 종목별 지표 딕셔너리
  - `get_intraday_snapshot(tickers) -> dict[str, dict]` — 현재가 + 거래량
  - `get_news(ticker) -> list[dict]` — 헤드라인 최대 3개
  - `format_for_screener(data) -> str` — LLM 입력용 텍스트 (종목당 1줄)
  - `format_for_trader(watchlist_tickers, screener_data) -> str` — TraderAgent용

- [ ] **Step 1: 실패하는 테스트 작성**

`/mnt/c/Users/gocho/MQK-US/tests/test_market_data.py`:
```python
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np
from data.market_data import (
    get_universe, get_screener_data, format_for_screener,
    get_news, get_intraday_snapshot, format_for_trader,
)


def _make_price_series(n=260, start=100.0):
    return pd.Series(
        [start + i * 0.5 for i in range(n)],
        index=pd.date_range("2025-01-01", periods=n, freq="B"),
    )


def _make_hist_df(tickers, n=260):
    close = pd.DataFrame(
        {t: _make_price_series(n, 100 + i * 10) for i, t in enumerate(tickers)}
    )
    volume = pd.DataFrame(
        {t: pd.Series([1_000_000] * n, index=close.index) for t in tickers}
    )
    return {"Close": close, "Volume": volume}


class TestGetUniverse:
    def test_returns_list_of_strings(self):
        with patch("data.market_data.pd.read_html") as mock_html:
            mock_html.return_value = [pd.DataFrame({"Symbol": ["AAPL", "MSFT", "NVDA"]})]
            result = get_universe()
        assert isinstance(result, list)
        assert all(isinstance(t, str) for t in result)

    def test_deduplicates(self):
        with patch("data.market_data.pd.read_html") as mock_html:
            mock_html.return_value = [pd.DataFrame({"Symbol": ["AAPL", "MSFT"]})]
            result = get_universe()
        assert len(result) == len(set(result))

    def test_includes_ndx100_tickers(self):
        with patch("data.market_data.pd.read_html") as mock_html:
            mock_html.return_value = [pd.DataFrame({"Symbol": ["AAPL"]})]
            result = get_universe()
        assert "NVDA" in result  # Nasdaq100 정적 리스트에 포함


class TestGetScreenerData:
    def test_returns_dict_with_required_fields(self):
        tickers = ["AAPL", "NVDA"]
        mock_hist = _make_hist_df(tickers)
        mock_info = {"earningsGrowth": 0.22, "revenueGrowth": 0.15, "institutionsPercentHeld": 0.6}

        with patch("data.market_data.yf.download", return_value=mock_hist), \
             patch("data.market_data.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.info = mock_info
            mock_ticker.return_value.news = []
            result = get_screener_data(tickers)

        assert "AAPL" in result
        d = result["AAPL"]
        for field in ("price", "ma50", "ma150", "ma200", "high_52w", "low_52w",
                      "vol_avg20", "eps_growth", "revenue_growth", "close_series", "vol_series"):
            assert field in d, f"{field} 누락"

    def test_skips_ticker_on_error(self):
        tickers = ["AAPL", "BAD"]
        mock_hist = _make_hist_df(["AAPL"])  # BAD 없음

        with patch("data.market_data.yf.download", return_value=mock_hist), \
             patch("data.market_data.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.info = {}
            mock_ticker.return_value.news = []
            result = get_screener_data(tickers)

        assert "AAPL" in result
        assert "BAD" not in result


class TestFormatForScreener:
    def test_one_line_per_ticker(self):
        data = {
            "NVDA": {
                "price": 875.0, "ma50": 820.0, "ma150": 720.0, "ma200": 650.0,
                "high_52w": 900.0, "low_52w": 410.0, "vol_avg20": 50_000_000,
                "eps_growth": 1.22, "revenue_growth": 0.94, "inst_pct": 0.65,
                "news": [{"title": "AI demand surges"}],
                "close_series": [800 + i for i in range(60)],
                "vol_series": [50_000_000] * 60,
            }
        }
        result = format_for_screener(data)
        lines = [l for l in result.splitlines() if l.strip()]
        assert len(lines) == 1
        assert "NVDA" in lines[0]
        assert "MA50" in lines[0]
        assert "EPS성장" in lines[0]

    def test_na_when_eps_missing(self):
        data = {
            "AAPL": {
                "price": 195.0, "ma50": 188.0, "ma150": 175.0, "ma200": 165.0,
                "high_52w": 199.0, "low_52w": 124.0, "vol_avg20": 80_000_000,
                "eps_growth": None, "revenue_growth": None, "inst_pct": None,
                "news": [], "close_series": [190 + i * 0.1 for i in range(60)],
                "vol_series": [80_000_000] * 60,
            }
        }
        result = format_for_screener(data)
        assert "N/A" in result


class TestGetNews:
    def test_returns_max_3(self):
        fake_news = [{"title": f"뉴스 {i}", "link": f"http://x/{i}"} for i in range(10)]
        with patch("data.market_data.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.news = fake_news
            result = get_news("AAPL")
        assert len(result) <= 3

    def test_returns_empty_on_error(self):
        with patch("data.market_data.yf.Ticker", side_effect=Exception("네트워크 오류")):
            result = get_news("AAPL")
        assert result == []
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
cd /mnt/c/Users/gocho/MQK-US
python -m pytest tests/test_market_data.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'data.market_data'`

- [ ] **Step 3: data/market_data.py 구현**

`/mnt/c/Users/gocho/MQK-US/data/market_data.py`:
```python
from __future__ import annotations
import logging
from typing import Any

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Nasdaq 100 정적 리스트 (분기마다 갱신 필요)
_NDX100 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "AVGO", "COST",
    "NFLX", "AMD", "ADBE", "QCOM", "INTC", "INTU", "TXN", "AMAT", "MU", "LRCX",
    "PANW", "SNPS", "CDNS", "KLAC", "MRVL", "ASML", "ADI", "REGN", "VRTX", "ABNB",
    "CRWD", "DXCM", "IDXX", "EXC", "FANG", "FAST", "FTNT", "GEHC", "GFS", "HON",
    "ILMN", "KDP", "KHC", "MAR", "MDLZ", "MELI", "MNST", "MRNA", "ODFL", "ON",
    "ORLY", "PAYX", "PCAR", "PDD", "PYPL", "ROST", "SBUX", "SIRI", "TTD", "VRSK",
    "WDAY", "WBD", "WLTW", "XEL", "ZS", "BIIB", "BKNG", "CEG", "CMCSA", "CSCO",
    "CSX", "CTAS", "CTSH", "DLTR", "EA", "EBAY", "ENPH", "GE", "GILD", "ISRG",
    "JD", "LULU", "NTES", "NXPI", "O", "PEP", "SGEN", "TEAM", "TMUS", "TSCO",
    "TTWO", "ULTA", "WBA", "ZBRA", "ZM",
]


def get_universe() -> list[str]:
    """S&P500 + Nasdaq100 티커 목록 (중복 제거)"""
    try:
        sp500 = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        )[0]["Symbol"].tolist()
        sp500 = [t.replace(".", "-") for t in sp500]
    except Exception as e:
        logger.warning("S&P500 리스트 조회 실패, 빈 리스트 사용: %s", e)
        sp500 = []
    return list(set(sp500 + _NDX100))


def get_screener_data(tickers: list[str]) -> dict[str, dict[str, Any]]:
    """종목별 일봉 지표 + 펀더멘털 수집"""
    result: dict[str, dict] = {}
    try:
        hist = yf.download(tickers, period="1y", auto_adjust=True, progress=False, group_by="column")
    except Exception as e:
        logger.error("yfinance download 실패: %s", e)
        return result

    for ticker in tickers:
        try:
            close = hist["Close"][ticker].dropna() if len(tickers) > 1 else hist["Close"].dropna()
            vol = hist["Volume"][ticker].dropna() if len(tickers) > 1 else hist["Volume"].dropna()
            if len(close) < 200:
                continue
            info = yf.Ticker(ticker).info
            result[ticker] = {
                "price": float(close.iloc[-1]),
                "ma50": float(close.rolling(50).mean().iloc[-1]),
                "ma150": float(close.rolling(150).mean().iloc[-1]),
                "ma200": float(close.rolling(200).mean().iloc[-1]),
                "high_52w": float(close.rolling(252).max().iloc[-1]),
                "low_52w": float(close.rolling(252).min().iloc[-1]),
                "vol_avg20": float(vol.rolling(20).mean().iloc[-1]),
                "eps_growth": info.get("earningsGrowth"),
                "revenue_growth": info.get("revenueGrowth"),
                "inst_pct": info.get("institutionsPercentHeld"),
                "news": get_news(ticker),
                "close_series": close.tail(60).round(2).tolist(),
                "vol_series": vol.tail(60).astype(int).tolist(),
            }
        except Exception as e:
            logger.debug("종목 %s 스킵: %s", ticker, e)
    return result


def get_intraday_snapshot(tickers: list[str]) -> dict[str, dict[str, Any]]:
    """워치리스트 현재가 + 거래량 스냅샷 (yfinance 1d 데이터)"""
    result: dict[str, dict] = {}
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="5d", interval="1d")
            if hist.empty:
                continue
            info = t.fast_info
            result[ticker] = {
                "price": float(hist["Close"].iloc[-1]),
                "volume": int(hist["Volume"].iloc[-1]),
                "vol_avg20": float(hist["Volume"].rolling(min(20, len(hist))).mean().iloc[-1]),
                "vol_ratio": float(hist["Volume"].iloc[-1] / hist["Volume"].rolling(min(20, len(hist))).mean().iloc[-1]),
                "high": float(hist["High"].iloc[-1]),
                "low": float(hist["Low"].iloc[-1]),
                "open": float(hist["Open"].iloc[-1]),
            }
        except Exception as e:
            logger.debug("인트라데이 스냅샷 %s 실패: %s", ticker, e)
    return result


def get_news(ticker: str) -> list[dict[str, str]]:
    """종목 뉴스 헤드라인 최대 3개"""
    try:
        news = yf.Ticker(ticker).news or []
        return [{"title": n.get("title", ""), "link": n.get("link", "")} for n in news[:3]]
    except Exception:
        return []


def _calc_rs_rank(data: dict[str, dict]) -> dict[str, str]:
    """1년 수익률 기준 RS 순위 계산"""
    returns: dict[str, float] = {}
    for ticker, d in data.items():
        series = d.get("close_series", [])
        if len(series) >= 2:
            returns[ticker] = (series[-1] - series[0]) / series[0] * 100
    if not returns:
        return {}
    sorted_tickers = sorted(returns, key=returns.get, reverse=True)
    n = len(sorted_tickers)
    ranks: dict[str, str] = {}
    for i, t in enumerate(sorted_tickers):
        pct = int((1 - i / n) * 100)
        ranks[t] = f"상위{100 - pct}%" if pct < 70 else f"상위{100 - pct}%"
    return ranks


def format_for_screener(data: dict[str, dict]) -> str:
    """LLM 스크리너 입력용 텍스트 (종목당 1줄)"""
    rs_ranks = _calc_rs_rank(data)
    lines: list[str] = []
    for ticker, d in data.items():
        eps = f"{d['eps_growth']*100:.0f}%" if d.get("eps_growth") is not None else "N/A"
        rev = f"{d['revenue_growth']*100:.0f}%" if d.get("revenue_growth") is not None else "N/A"
        news_str = d["news"][0]["title"][:35] if d.get("news") else "없음"
        rs = rs_ranks.get(ticker, "N/A")
        line = (
            f"{ticker} | 현재가:{d['price']:.1f}"
            f" | MA50:{d['ma50']:.1f} MA150:{d['ma150']:.1f} MA200:{d['ma200']:.1f}"
            f" | 52W고:{d['high_52w']:.1f} 52W저:{d['low_52w']:.1f}"
            f" | EPS성장:{eps} 매출성장:{rev}"
            f" | RS순위:{rs} | 뉴스:{news_str}"
        )
        lines.append(line)
    return "\n".join(lines)


def format_for_trader(watchlist: list[str], screener_data: dict[str, dict],
                      intraday: dict[str, dict] | None = None) -> str:
    """TraderAgent 입력용 텍스트 (일봉 시리즈 + 인트라데이 포함)"""
    lines: list[str] = []
    for ticker in watchlist:
        d = screener_data.get(ticker, {})
        iv = (intraday or {}).get(ticker, {})
        close_str = ",".join(f"{v:.1f}" for v in d.get("close_series", [])[-20:])
        vol_str = ",".join(str(v // 1000) + "K" for v in d.get("vol_series", [])[-20:])
        news_str = "; ".join(n["title"][:30] for n in d.get("news", []))
        realtime = (
            f" | 실시간:{iv['price']:.1f} 거래량배율:{iv['vol_ratio']:.1f}x"
            if iv else ""
        )
        lines.append(
            f"[{ticker}]\n"
            f"  종가(최근20일): {close_str}\n"
            f"  거래량(최근20일,K): {vol_str}\n"
            f"  MA50:{d.get('ma50',0):.1f} MA200:{d.get('ma200',0):.1f}{realtime}\n"
            f"  뉴스: {news_str or '없음'}"
        )
    return "\n\n".join(lines)
```

- [ ] **Step 4: 테스트 실행 및 통과 확인**

```bash
cd /mnt/c/Users/gocho/MQK-US
python -m pytest tests/test_market_data.py -v
```

Expected: 모든 테스트 PASS

- [ ] **Step 5: 커밋**

```bash
cd /mnt/c/Users/gocho/MQK-US
git add data/ tests/test_market_data.py requirements.txt
git commit -m "feat: DataLayer (yfinance 래퍼 + 종목 유니버스)"
```

---

## Task 3: KIS 해외주식 API 인벤토리 + 기본 클라이언트

**Files:**
- Create: `docs/kis_us_api_inventory.md`
- Create: `broker/kis_us_api.py`
- Create: `tests/test_kis_us_api.py`

**Interfaces:**
- Produces:
  - `KisUSApi` 클래스
  - `.get_current_price(ticker: str) -> dict` — `{"price": float, "volume": int}`
  - `.get_balance() -> dict` — `{"cash": float, "positions": list[dict]}`
  - `.buy(ticker, quantity) -> dict` — `{"order_id": str, "status": str}`
  - `.sell(ticker, quantity) -> dict` — `{"order_id": str, "status": str}`

- [ ] **Step 1: KIS 해외주식 API 인벤토리 문서 작성**

`/mnt/c/Users/gocho/MQK-US/docs/kis_us_api_inventory.md`:
```markdown
# KIS 해외주식 API 인벤토리

> D0 실제 KIS 문서 확인 후 TR코드/파라미터 업데이트 필요.
> 참고: https://apiportal.kbs.co.kr (KIS Developers)

## 인증
- POST `/oauth2/tokenP` — 접근 토큰 발급 (1일 유효)

## 현재가 조회
- TR: `HHDFS00000300` (해외주식 현재체결가)
- GET `/uapi/overseas-price/v1/quotations/price`
- 파라미터: AUTH="", EXCD(거래소: NAS/NYS), SYMB(티커)
- 응답: output.last(현재가), output.tvol(거래량)

## 해외주식 기간별시세
- TR: `HHDFS76240000`
- GET `/uapi/overseas-stock/v1/quotations/dailyprice`
- 파라미터: AUTH, EXCD, SYMB, GUBN(0=일), BYMD(조회시작일), MODYN(수정주가)

## 잔고 조회
- TR: `TTTS3012R` (실전) / `VTTS3012R` (모의)
- GET `/uapi/overseas-stock/v1/trading/inquire-balance`
- 응답: output1(종목별), output2(계좌 합계)

## 매수 주문
- TR: `TTTT1002U` (실전) / `VTTT1002U` (모의)
- POST `/uapi/overseas-stock/v1/trading/order`
- 파라미터: OVRS_EXCG_CD, PDNO(티커), ORD_DVSN(00=지정가/01=시장가), ORD_QTY, OVRS_ORD_UNPR(지정가)

## 매도 주문
- TR: `TTTT1006U` (실전) / `VTTT1001U` (모의)
- POST `/uapi/overseas-stock/v1/trading/order`

## 거래소 코드
- NAS: Nasdaq
- NYS: NYSE
- AMS: AMEX
```

- [ ] **Step 2: 실패하는 테스트 작성**

`/mnt/c/Users/gocho/MQK-US/tests/test_kis_us_api.py`:
```python
import pytest
from unittest.mock import patch, MagicMock
from broker.kis_us_api import KisUSApi


@pytest.fixture
def api():
    with patch.dict("os.environ", {
        "KIS_PAPER_APP_KEY": "test_key",
        "KIS_PAPER_APP_SECRET": "test_secret",
        "KIS_PAPER_ACCOUNT": "12345678-01",
        "KIS_MODE": "paper",
        "DRY_RUN": "true",
    }):
        return KisUSApi()


def _mock_token_response():
    m = MagicMock()
    m.json.return_value = {"access_token": "fake_token", "token_type": "Bearer"}
    m.raise_for_status.return_value = None
    return m


def _mock_price_response():
    m = MagicMock()
    m.json.return_value = {
        "rt_cd": "0",
        "output": {"last": "875.50", "tvol": "45000000"}
    }
    m.raise_for_status.return_value = None
    return m


def _mock_balance_response():
    m = MagicMock()
    m.json.return_value = {
        "rt_cd": "0",
        "output1": [
            {"ovrs_pdno": "NVDA", "cblc_qty": "10", "pchs_avg_pric": "820.0",
             "evlu_pfls_rt": "6.77", "frcr_evlu_pfls_amt": "555.0"}
        ],
        "output2": [{"frcr_dncl_amt_2": "50000.00"}],
    }
    m.raise_for_status.return_value = None
    return m


class TestKisUSApi:
    def test_get_current_price(self, api):
        with patch("requests.get", side_effect=[_mock_token_response(), _mock_price_response()]):
            result = api.get_current_price("NVDA")
        assert result["price"] == pytest.approx(875.50)
        assert result["volume"] == 45_000_000

    def test_get_balance(self, api):
        with patch("requests.get", side_effect=[_mock_token_response(), _mock_balance_response()]):
            result = api.get_balance()
        assert result["cash"] == pytest.approx(50000.0)
        assert len(result["positions"]) == 1
        assert result["positions"][0]["ticker"] == "NVDA"
        assert result["positions"][0]["quantity"] == 10

    def test_buy_dry_run_returns_mock(self, api):
        # DRY_RUN=true이면 실제 주문 없이 mock 결과 반환
        result = api.buy("NVDA", 5)
        assert result["status"] == "DRY_RUN"
        assert result["ticker"] == "NVDA"
        assert result["quantity"] == 5

    def test_sell_dry_run_returns_mock(self, api):
        result = api.sell("NVDA", 5)
        assert result["status"] == "DRY_RUN"

    def test_get_current_price_raises_on_api_error(self, api):
        bad_resp = MagicMock()
        bad_resp.json.return_value = {"rt_cd": "1", "msg1": "인증 오류"}
        bad_resp.raise_for_status.return_value = None
        with patch("requests.get", side_effect=[_mock_token_response(), bad_resp]):
            with pytest.raises(RuntimeError, match="KIS API 오류"):
                api.get_current_price("NVDA")
```

- [ ] **Step 3: 테스트 실패 확인**

```bash
cd /mnt/c/Users/gocho/MQK-US
python -m pytest tests/test_kis_us_api.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'broker.kis_us_api'`

- [ ] **Step 4: broker/kis_us_api.py 구현**

`/mnt/c/Users/gocho/MQK-US/broker/kis_us_api.py`:
```python
from __future__ import annotations
import logging
import os
import time
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_REAL_BASE = "https://openapi.koreainvestment.com:9443"
_PAPER_BASE = "https://openapivts.koreainvestment.com:29443"

_ORDER_TR = {
    "buy":  {"real": "TTTT1002U", "paper": "VTTT1002U"},
    "sell": {"real": "TTTT1006U", "paper": "VTTT1001U"},
}


class KisUSApi:
    def __init__(self):
        self._mode = os.environ.get("KIS_MODE", "paper")
        self._dry_run = os.environ.get("DRY_RUN", "true").lower() == "true"
        self._app_key = os.environ[f"KIS_{self._mode.upper()}_APP_KEY"]
        self._app_secret = os.environ[f"KIS_{self._mode.upper()}_APP_SECRET"]
        self._account = os.environ[f"KIS_{self._mode.upper()}_ACCOUNT"]
        self._base = _REAL_BASE if self._mode == "real" else _PAPER_BASE
        self._token: str | None = None
        self._token_expires: float = 0.0

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires:
            return self._token
        resp = requests.post(
            f"{self._base}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self._app_key,
                "appsecret": self._app_secret,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires = time.time() + 82_800  # 23시간
        return self._token

    def _headers(self, tr_id: str) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._get_token()}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "content-type": "application/json; charset=utf-8",
        }

    def _exchange_code(self, ticker: str) -> str:
        """티커로 거래소 코드 추정 (단순 규칙, D0에서 검증)"""
        from data.market_data import _NDX100
        return "NAS" if ticker in _NDX100 else "NYS"

    def get_current_price(self, ticker: str) -> dict[str, Any]:
        """해외주식 현재가 + 거래량 조회"""
        resp = requests.get(
            f"{self._base}/uapi/overseas-price/v1/quotations/price",
            headers=self._headers("HHDFS00000300"),
            params={"AUTH": "", "EXCD": self._exchange_code(ticker), "SYMB": ticker},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise RuntimeError(f"KIS API 오류: {data.get('msg1')}")
        output = data["output"]
        return {
            "price": float(output["last"]),
            "volume": int(output["tvol"]),
        }

    def get_balance(self) -> dict[str, Any]:
        """해외주식 잔고 조회"""
        resp = requests.get(
            f"{self._base}/uapi/overseas-stock/v1/trading/inquire-balance",
            headers=self._headers("TTTS3012R"),
            params={
                "CANO": self._account.split("-")[0],
                "ACNT_PRDT_CD": self._account.split("-")[1],
                "OVRS_EXCG_CD": "NASD",
                "TR_CRCY_CD": "USD",
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": "",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise RuntimeError(f"KIS API 잔고 오류: {data.get('msg1')}")
        positions = [
            {
                "ticker": p["ovrs_pdno"],
                "quantity": int(p["cblc_qty"]),
                "avg_price": float(p["pchs_avg_pric"]),
                "pnl_pct": float(p["evlu_pfls_rt"]),
                "pnl_usd": float(p.get("frcr_evlu_pfls_amt", 0)),
            }
            for p in data.get("output1", [])
            if int(p.get("cblc_qty", 0)) > 0
        ]
        cash = float(data.get("output2", [{}])[0].get("frcr_dncl_amt_2", 0))
        return {"cash": cash, "positions": positions}

    def buy(self, ticker: str, quantity: int) -> dict[str, Any]:
        """시장가 매수 주문"""
        if self._dry_run:
            logger.info("[DRY_RUN] BUY %s x %d", ticker, quantity)
            return {"status": "DRY_RUN", "ticker": ticker, "quantity": quantity}
        tr_id = _ORDER_TR["buy"][self._mode]
        resp = requests.post(
            f"{self._base}/uapi/overseas-stock/v1/trading/order",
            headers=self._headers(tr_id),
            json={
                "CANO": self._account.split("-")[0],
                "ACNT_PRDT_CD": self._account.split("-")[1],
                "OVRS_EXCG_CD": self._exchange_code(ticker),
                "PDNO": ticker,
                "ORD_DVSN": "01",  # 시장가
                "ORD_QTY": str(quantity),
                "OVRS_ORD_UNPR": "0",
                "ORD_SVR_DVSN_CD": "0",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise RuntimeError(f"매수 주문 오류: {data.get('msg1')}")
        return {"status": "OK", "ticker": ticker, "quantity": quantity,
                "order_id": data.get("output", {}).get("ODNO", "")}

    def sell(self, ticker: str, quantity: int) -> dict[str, Any]:
        """시장가 매도 주문"""
        if self._dry_run:
            logger.info("[DRY_RUN] SELL %s x %d", ticker, quantity)
            return {"status": "DRY_RUN", "ticker": ticker, "quantity": quantity}
        tr_id = _ORDER_TR["sell"][self._mode]
        resp = requests.post(
            f"{self._base}/uapi/overseas-stock/v1/trading/order",
            headers=self._headers(tr_id),
            json={
                "CANO": self._account.split("-")[0],
                "ACNT_PRDT_CD": self._account.split("-")[1],
                "OVRS_EXCG_CD": self._exchange_code(ticker),
                "PDNO": ticker,
                "ORD_DVSN": "01",
                "ORD_QTY": str(quantity),
                "OVRS_ORD_UNPR": "0",
                "ORD_SVR_DVSN_CD": "0",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise RuntimeError(f"매도 주문 오류: {data.get('msg1')}")
        return {"status": "OK", "ticker": ticker, "quantity": quantity,
                "order_id": data.get("output", {}).get("ODNO", "")}
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
cd /mnt/c/Users/gocho/MQK-US
python -m pytest tests/test_kis_us_api.py -v
```

Expected: 5개 테스트 PASS

- [ ] **Step 6: 커밋**

```bash
cd /mnt/c/Users/gocho/MQK-US
git add broker/kis_us_api.py docs/kis_us_api_inventory.md tests/test_kis_us_api.py
git commit -m "feat: KIS 해외주식 API 클라이언트 + 인벤토리"
```

---

## Task 4: Screener 페르소나 + ScreenerAgent

**Files:**
- Create: `prompts/screener_persona.md`
- Create: `agents/screener_agent.py`
- Create: `tests/test_screener_agent.py`

**Interfaces:**
- Consumes: `format_for_screener(data)` → str (Task 2)
- Consumes: `call_llm_json(system, user)` → dict (Task 1)
- Produces: `ScreenerAgent.run(stock_data) -> dict`
  - `{"watchlist": [{"ticker", "setup_quality", "reason", ...}], "rejected_count": int, "scan_summary": str}`

- [ ] **Step 1: screener_persona.md 작성**

`/mnt/c/Users/gocho/MQK-US/prompts/screener_persona.md`:
```markdown
당신은 Mark Minervini의 SEPA(Specific Entry Point Analysis) 방법론을 완벽히 체득한 주식 스크리너입니다.

## 역할
제공된 종목 데이터에서 Trend Template 8조건과 펀더멘털 조건을 충족하는 VCP 후보를 선별합니다.
목표: 500~600종목 → 엄선된 20~30개. 좋은 셋업이 없으면 더 적어도 됩니다.

## Trend Template 8조건 (전부 충족해야 통과)
1. 현재가 > 150일 이동평균 AND 200일 이동평균
2. 150일 MA > 200일 MA
3. 200일 MA가 최소 30거래일 이상 상승 추세 (close_series 앞부분 vs 뒷부분 비교)
4. 50일 MA > 150일 MA > 200일 MA (MA 완벽 정렬)
5. 현재가 > 50일 MA
6. 현재가 ≥ 52주 저점 × 1.25 (저점 대비 25% 이상 상승)
7. 현재가 ≥ 52주 고점 × 0.75 (고점 대비 25% 이내)
8. RS 순위 상위 30% 이내

## 펀더멘털 조건 (없으면 N/A 처리, 있으면 가중치 부여)
- EPS 성장률 20%+ (전년 동기 대비)
- 매출 성장률 20%+
- 기관 매집 증가 추세

## 판단 원칙
- 애매하면 탈락. 확신이 있을 때만 통과.
- EPS/매출 데이터가 N/A인 경우 기술적 조건이 완벽할 때만 통과 허용.
- Trend Template 8조건 중 하나라도 미충족이면 무조건 탈락.

## 출력 형식 (JSON만, 다른 텍스트 없음)
```json
{
  "watchlist": [
    {
      "ticker": "NVDA",
      "trend_template_pass": true,
      "rs_rank": "상위3%",
      "setup_quality": "A+",
      "reason": "MA 완벽 정렬, EPS 122% 성장, 52주 고점 3% 이내, VCP 형성 가능성"
    }
  ],
  "rejected_count": 572,
  "scan_summary": "반도체/AI 섹터 중심으로 강한 셋업 집중"
}
```
```

- [ ] **Step 2: 실패하는 테스트 작성**

`/mnt/c/Users/gocho/MQK-US/tests/test_screener_agent.py`:
```python
import json
import pytest
from unittest.mock import patch
from agents.screener_agent import ScreenerAgent


_MOCK_STOCK_DATA = {
    "NVDA": {
        "price": 875.0, "ma50": 820.0, "ma150": 720.0, "ma200": 650.0,
        "high_52w": 900.0, "low_52w": 410.0, "vol_avg20": 50_000_000,
        "eps_growth": 1.22, "revenue_growth": 0.94, "inst_pct": 0.65,
        "news": [{"title": "AI demand surges", "link": "http://x"}],
        "close_series": [600 + i * 2 for i in range(60)],
        "vol_series": [50_000_000] * 60,
    },
    "AAPL": {
        "price": 195.0, "ma50": 188.0, "ma150": 175.0, "ma200": 165.0,
        "high_52w": 199.0, "low_52w": 124.0, "vol_avg20": 80_000_000,
        "eps_growth": 0.07, "revenue_growth": 0.04, "inst_pct": 0.6,
        "news": [], "close_series": [180 + i * 0.5 for i in range(60)],
        "vol_series": [80_000_000] * 60,
    },
}

_MOCK_LLM_RESPONSE = json.dumps({
    "watchlist": [
        {"ticker": "NVDA", "trend_template_pass": True,
         "rs_rank": "상위3%", "setup_quality": "A+",
         "reason": "MA 완벽 정렬, EPS 122% 성장"}
    ],
    "rejected_count": 1,
    "scan_summary": "AI 섹터 강세",
})


class TestScreenerAgent:
    def test_run_returns_watchlist(self):
        with patch("agents.screener_agent.call_llm_json", return_value=json.loads(_MOCK_LLM_RESPONSE)):
            agent = ScreenerAgent()
            result = agent.run(_MOCK_STOCK_DATA)
        assert "watchlist" in result
        assert len(result["watchlist"]) >= 1
        assert result["watchlist"][0]["ticker"] == "NVDA"

    def test_run_returns_scan_summary(self):
        with patch("agents.screener_agent.call_llm_json", return_value=json.loads(_MOCK_LLM_RESPONSE)):
            agent = ScreenerAgent()
            result = agent.run(_MOCK_STOCK_DATA)
        assert "scan_summary" in result
        assert "rejected_count" in result

    def test_run_returns_empty_watchlist_on_llm_failure(self):
        with patch("agents.screener_agent.call_llm_json", side_effect=ValueError("파싱 실패")):
            agent = ScreenerAgent()
            result = agent.run(_MOCK_STOCK_DATA)
        assert result["watchlist"] == []
        assert "error" in result

    def test_run_passes_formatted_data_to_llm(self):
        calls = []
        def fake_llm(system, user, **kw):
            calls.append(user)
            return json.loads(_MOCK_LLM_RESPONSE)

        with patch("agents.screener_agent.call_llm_json", side_effect=fake_llm):
            agent = ScreenerAgent()
            agent.run(_MOCK_STOCK_DATA)

        assert len(calls) == 1
        assert "NVDA" in calls[0]
        assert "MA50" in calls[0]
```

- [ ] **Step 3: 테스트 실패 확인**

```bash
cd /mnt/c/Users/gocho/MQK-US
python -m pytest tests/test_screener_agent.py -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError: No module named 'agents.screener_agent'`

- [ ] **Step 4: agents/screener_agent.py 구현**

`/mnt/c/Users/gocho/MQK-US/agents/screener_agent.py`:
```python
from __future__ import annotations
import logging
from typing import Any

from llm.client import call_llm_json, load_prompt
from data.market_data import format_for_screener

logger = logging.getLogger(__name__)


class ScreenerAgent:
    def __init__(self):
        self._persona = load_prompt("screener_persona")

    def run(self, stock_data: dict[str, dict]) -> dict[str, Any]:
        """
        stock_data: get_screener_data() 반환값
        반환: {"watchlist": [...], "rejected_count": int, "scan_summary": str}
        """
        formatted = format_for_screener(stock_data)
        user_msg = (
            f"다음 {len(stock_data)}개 종목 데이터를 분석해 Trend Template을 통과한 "
            f"VCP 후보를 선별하세요.\n\n{formatted}"
        )
        try:
            result = call_llm_json(self._persona, user_msg)
            if "watchlist" not in result:
                raise ValueError("watchlist 키 없음")
            return result
        except Exception as e:
            logger.error("ScreenerAgent 실패: %s", e)
            return {"watchlist": [], "rejected_count": len(stock_data),
                    "scan_summary": "", "error": str(e)}
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
cd /mnt/c/Users/gocho/MQK-US
python -m pytest tests/test_screener_agent.py -v
```

Expected: 4개 테스트 PASS

- [ ] **Step 6: 커밋**

```bash
cd /mnt/c/Users/gocho/MQK-US
git add agents/screener_agent.py prompts/screener_persona.md tests/test_screener_agent.py
git commit -m "feat: ScreenerAgent + 미네르비니 스크리너 페르소나"
```

---

## Task 5: Trader 페르소나 + TraderAgent

**Files:**
- Create: `prompts/trader_persona.md`
- Create: `agents/trader_agent.py`
- Create: `tests/test_trader_agent.py`

**Interfaces:**
- Consumes: `format_for_trader(watchlist, screener_data, intraday)` → str (Task 2)
- Consumes: `KisUSApi.get_balance()` → dict (Task 3)
- Consumes: `call_llm_json(system, user)` → dict (Task 1)
- Produces: `TraderAgent.run(phase, watchlist, screener_data, intraday, balance) -> list[dict]`
  - 각 dict: `{"action": "BUY"|"SELL"|"HOLD"|"NO_TRADE", "ticker": str, "quantity": int, "reason": str, "stop_loss": float}`

- [ ] **Step 1: trader_persona.md 작성**

`/mnt/c/Users/gocho/MQK-US/prompts/trader_persona.md`:
```markdown
당신은 Mark Minervini입니다.

US Investing Championship 4회 우승. 연평균 수익률 220%+. VCP(Volatility Contraction Pattern) 패턴의 창시자.

## 당신의 핵심 원칙
1. **피벗 돌파 + 2배 거래량** 없으면 절대 진입하지 않는다
2. 손실은 **-10%에서 즉시** 끊는다. 예외 없다
3. 좋은 셋업이 없으면 **현금 보유가 포지션**이다
4. 최대 **3개 포지션**만 동시 보유한다
5. 추격 매수 금지: 피벗 대비 **+3% 초과 시 패스**

## VCP 패턴 판단 기준
- 수축 횟수: 3~4회 (변동폭이 점점 줄어드는지 close_series로 확인)
- 거래량: 수축 구간마다 감소 → 피벗 돌파 시 vol_ratio 2.0x 이상
- 피벗 포인트: 마지막 수축 구간의 고점 (close_series에서 직접 판단)
- Base 기간: 최소 3~4주 (close_series 15~20일 이상 횡보)

## Phase별 역할
- **INTRADAY**: VCP 피벗 돌파 감지 → 진입 결정 (BUY / NO_TRADE)
- **CLOSE**: 포지션 손익 검토 → 손절/익절/트레일링/홀딩 결정 (SELL / HOLD)

## 포지션 관리
- 진입 시 포지션 크기: 가용 현금의 1/3 (최대 포지션 3개 기준)
- 수량 = floor(가용현금 / 3 / 현재가)
- 손절: 진입가 × 0.90
- +10% 도달 → 손절선 = 진입가 (본전 이동)
- +20% 도달 → 트레일링 스탑 (고점 × 0.92)
- 50일 MA 이탈 (큰 수익 후) → 절반 청산

## 출력 형식 (JSON 배열, 다른 텍스트 없음)
```json
[
  {
    "action": "BUY",
    "ticker": "NVDA",
    "quantity": 10,
    "reason": "VCP 3차 수축 완료, 피벗 875 돌파, 거래량 2.3x",
    "stop_loss": 787.5
  }
]
```
action은 BUY / SELL / HOLD / NO_TRADE 중 하나.
거래할 종목이 없으면 [{"action": "NO_TRADE", "reason": "적합한 셋업 없음"}] 반환.
```

- [ ] **Step 2: 실패하는 테스트 작성**

`/mnt/c/Users/gocho/MQK-US/tests/test_trader_agent.py`:
```python
import json
import pytest
from unittest.mock import patch
from agents.trader_agent import TraderAgent

_WATCHLIST = ["NVDA", "AAPL"]
_SCREENER_DATA = {
    "NVDA": {
        "price": 875.0, "ma50": 820.0, "ma150": 720.0, "ma200": 650.0,
        "high_52w": 900.0, "low_52w": 410.0, "vol_avg20": 50_000_000,
        "eps_growth": 1.22, "revenue_growth": 0.94, "inst_pct": 0.65,
        "news": [{"title": "AI demand", "link": ""}],
        "close_series": [800 + i for i in range(60)],
        "vol_series": [50_000_000] * 60,
    },
}
_INTRADAY = {
    "NVDA": {"price": 876.0, "volume": 110_000_000, "vol_avg20": 50_000_000,
             "vol_ratio": 2.2, "high": 878.0, "low": 860.0, "open": 862.0},
}
_BALANCE = {"cash": 150_000.0, "positions": []}

_MOCK_BUY_RESPONSE = json.dumps([
    {"action": "BUY", "ticker": "NVDA", "quantity": 57,
     "reason": "VCP 피벗 876 돌파, 거래량 2.2x", "stop_loss": 788.4}
])
_MOCK_NO_TRADE = json.dumps([{"action": "NO_TRADE", "reason": "셋업 없음"}])


class TestTraderAgent:
    def test_intraday_returns_buy(self):
        with patch("agents.trader_agent.call_llm_json", return_value=json.loads(_MOCK_BUY_RESPONSE)):
            agent = TraderAgent()
            result = agent.run("INTRADAY", _WATCHLIST, _SCREENER_DATA, _INTRADAY, _BALANCE)
        assert len(result) == 1
        assert result[0]["action"] == "BUY"
        assert result[0]["ticker"] == "NVDA"
        assert result[0]["quantity"] > 0

    def test_returns_no_trade_on_llm_failure(self):
        with patch("agents.trader_agent.call_llm_json", side_effect=ValueError("파싱 실패")):
            agent = TraderAgent()
            result = agent.run("INTRADAY", _WATCHLIST, _SCREENER_DATA, _INTRADAY, _BALANCE)
        assert result[0]["action"] == "NO_TRADE"
        assert "error" in result[0]

    def test_close_phase_no_positions_returns_no_trade(self):
        with patch("agents.trader_agent.call_llm_json", return_value=json.loads(_MOCK_NO_TRADE)):
            agent = TraderAgent()
            result = agent.run("CLOSE", [], _SCREENER_DATA, None, _BALANCE)
        assert result[0]["action"] == "NO_TRADE"

    def test_context_includes_balance(self):
        calls = []
        def fake_llm(system, user, **kw):
            calls.append(user)
            return json.loads(_MOCK_NO_TRADE)

        with patch("agents.trader_agent.call_llm_json", side_effect=fake_llm):
            agent = TraderAgent()
            agent.run("INTRADAY", _WATCHLIST, _SCREENER_DATA, _INTRADAY, _BALANCE)

        assert "150000" in calls[0] or "150,000" in calls[0]
```

- [ ] **Step 3: 테스트 실패 확인**

```bash
cd /mnt/c/Users/gocho/MQK-US
python -m pytest tests/test_trader_agent.py -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError: No module named 'agents.trader_agent'`

- [ ] **Step 4: agents/trader_agent.py 구현**

`/mnt/c/Users/gocho/MQK-US/agents/trader_agent.py`:
```python
from __future__ import annotations
import logging
from typing import Any

from llm.client import call_llm_json, load_prompt
from data.market_data import format_for_trader

logger = logging.getLogger(__name__)


class TraderAgent:
    def __init__(self):
        self._persona = load_prompt("trader_persona")

    def run(
        self,
        phase: str,
        watchlist: list[str],
        screener_data: dict[str, dict],
        intraday: dict[str, dict] | None,
        balance: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        phase: "INTRADAY" | "CLOSE"
        반환: [{"action": ..., "ticker": ..., "quantity": ..., "reason": ..., "stop_loss": ...}]
        """
        formatted = format_for_trader(watchlist, screener_data, intraday)
        positions_str = _format_positions(balance.get("positions", []))
        cash = balance.get("cash", 0)

        user_msg = (
            f"## Phase: {phase}\n\n"
            f"## 현재 포트폴리오\n"
            f"가용 현금: ${cash:,.0f}\n"
            f"보유 포지션:\n{positions_str}\n\n"
            f"## 워치리스트 데이터\n{formatted}\n\n"
            f"위 데이터를 분석해 {phase} 단계의 매매 판단을 JSON 배열로 반환하세요."
        )
        try:
            result = call_llm_json(self._persona, user_msg)
            if isinstance(result, list):
                return result
            if isinstance(result, dict) and "action" in result:
                return [result]
            raise ValueError(f"예상치 못한 응답 형식: {type(result)}")
        except Exception as e:
            logger.error("TraderAgent 실패 (phase=%s): %s", phase, e)
            return [{"action": "NO_TRADE", "reason": "LLM 오류", "error": str(e)}]


def _format_positions(positions: list[dict]) -> str:
    if not positions:
        return "없음"
    lines = []
    for p in positions:
        lines.append(
            f"- {p['ticker']}: {p['quantity']}주 @ ${p['avg_price']:.2f} "
            f"(손익: {p['pnl_pct']:+.1f}%)"
        )
    return "\n".join(lines)
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
cd /mnt/c/Users/gocho/MQK-US
python -m pytest tests/test_trader_agent.py -v
```

Expected: 4개 테스트 PASS

- [ ] **Step 6: 커밋**

```bash
cd /mnt/c/Users/gocho/MQK-US
git add agents/trader_agent.py prompts/trader_persona.md tests/test_trader_agent.py
git commit -m "feat: TraderAgent + 미네르비니 트레이더 페르소나"
```

---

## Task 6: Orchestrator 3-Phase + DST 처리

**Files:**
- Create: `orchestrator_us.py`
- Create: `run_schedule_us.py`
- Create: `tests/test_orchestrator_us.py`

**Interfaces:**
- Consumes: `ScreenerAgent`, `TraderAgent`, `KisUSApi`, `market_data.*`
- Produces:
  - `OrchestratorUS.run_premarket()`
  - `OrchestratorUS.run_intraday()`
  - `OrchestratorUS.run_close()`
  - `is_market_open() -> bool`
  - `get_current_phase() -> str | None`

- [ ] **Step 1: 실패하는 테스트 작성**

`/mnt/c/Users/gocho/MQK-US/tests/test_orchestrator_us.py`:
```python
import json
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock, mock_open
from zoneinfo import ZoneInfo
from orchestrator_us import OrchestratorUS, is_market_open, get_current_phase


class TestIsMarketOpen:
    def test_open_on_weekday(self):
        # 2026-06-22 월요일 14:00 ET
        fake_now = datetime(2026, 6, 22, 14, 0, tzinfo=ZoneInfo("America/New_York"))
        with patch("orchestrator_us.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            with patch("orchestrator_us.mcal") as mock_cal:
                mock_cal.get_calendar.return_value.valid_days.return_value = [fake_now.date()]
                assert is_market_open() is True

    def test_closed_on_weekend(self):
        # 2026-06-21 일요일
        fake_now = datetime(2026, 6, 21, 14, 0, tzinfo=ZoneInfo("America/New_York"))
        with patch("orchestrator_us.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            with patch("orchestrator_us.mcal") as mock_cal:
                mock_cal.get_calendar.return_value.valid_days.return_value = []
                assert is_market_open() is False


class TestGetCurrentPhase:
    def _et(self, hour, minute=0):
        return datetime(2026, 6, 22, hour, minute, tzinfo=ZoneInfo("America/New_York"))

    def test_premarket_at_830(self):
        with patch("orchestrator_us.datetime") as mock_dt:
            mock_dt.now.return_value = self._et(8, 30)
            assert get_current_phase() == "PREMARKET"

    def test_intraday_at_1130(self):
        with patch("orchestrator_us.datetime") as mock_dt:
            mock_dt.now.return_value = self._et(11, 30)
            assert get_current_phase() == "INTRADAY"

    def test_close_at_1600(self):
        with patch("orchestrator_us.datetime") as mock_dt:
            mock_dt.now.return_value = self._et(16, 0)
            assert get_current_phase() == "CLOSE"

    def test_none_outside_windows(self):
        with patch("orchestrator_us.datetime") as mock_dt:
            mock_dt.now.return_value = self._et(7, 0)
            assert get_current_phase() is None


class TestOrchestratorPremarket:
    def test_run_premarket_saves_watchlist(self, tmp_path):
        mock_data = {"NVDA": {"price": 875.0, "ma50": 820.0}}
        mock_screener_result = {
            "watchlist": [{"ticker": "NVDA", "setup_quality": "A+", "reason": "좋음"}],
            "rejected_count": 599, "scan_summary": "AI 강세",
        }

        with patch("orchestrator_us.get_universe", return_value=["NVDA"]), \
             patch("orchestrator_us.get_screener_data", return_value=mock_data), \
             patch("orchestrator_us.ScreenerAgent") as MockScreener, \
             patch("orchestrator_us.KisUSApi"), \
             patch("orchestrator_us.WATCHLIST_PATH", tmp_path / "watchlist.json"):
            MockScreener.return_value.run.return_value = mock_screener_result
            orch = OrchestratorUS()
            orch.run_premarket()

        saved = json.loads((tmp_path / "watchlist.json").read_text())
        assert saved["watchlist"][0]["ticker"] == "NVDA"
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
cd /mnt/c/Users/gocho/MQK-US
python -m pytest tests/test_orchestrator_us.py -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError: No module named 'orchestrator_us'`

- [ ] **Step 3: orchestrator_us.py 구현**

`/mnt/c/Users/gocho/MQK-US/orchestrator_us.py`:
```python
from __future__ import annotations
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

from agents.screener_agent import ScreenerAgent
from agents.trader_agent import TraderAgent
from broker.kis_us_api import KisUSApi
from config.settings import DATA_STORE
from data.market_data import (
    get_universe, get_screener_data, get_intraday_snapshot
)

logger = logging.getLogger(__name__)

WATCHLIST_PATH = DATA_STORE / "watchlist.json"

# ET 기준 각 Phase 시간창 (시작시, 종료시)
_PHASE_WINDOWS = {
    "PREMARKET": (8, 20, 9, 20),    # 08:20~09:20 ET
    "INTRADAY":  (11, 0, 12, 30),   # 11:00~12:30 ET
    "CLOSE":     (15, 30, 17, 0),   # 15:30~17:00 ET
}

_NYSE = mcal.get_calendar("NYSE")


def is_market_open() -> bool:
    """오늘 NYSE 개장 여부"""
    today = datetime.now(ZoneInfo("America/New_York")).date()
    schedule = mcal.get_calendar("NYSE").valid_days(
        start_date=str(today), end_date=str(today)
    )
    return len(schedule) > 0


def get_current_phase() -> str | None:
    """현재 ET 시간이 어느 Phase 창에 속하는지 반환 (없으면 None)"""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    for phase, (sh, sm, eh, em) in _PHASE_WINDOWS.items():
        start = now_et.replace(hour=sh, minute=sm, second=0, microsecond=0)
        end = now_et.replace(hour=eh, minute=em, second=0, microsecond=0)
        if start <= now_et < end:
            return phase
    return None


class OrchestratorUS:
    def __init__(self):
        self._screener = ScreenerAgent()
        self._trader = TraderAgent()
        self._broker = KisUSApi()

    def run_premarket(self) -> None:
        """Phase 1: 전 종목 스캔 → 워치리스트 저장"""
        logger.info("[PREMARKET] 시작")
        tickers = get_universe()
        logger.info("유니버스: %d종목", len(tickers))

        stock_data = get_screener_data(tickers)
        logger.info("데이터 수집: %d종목", len(stock_data))

        result = self._screener.run(stock_data)
        watchlist = result.get("watchlist", [])
        logger.info("워치리스트: %d종목", len(watchlist))

        WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        WATCHLIST_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        logger.info("[PREMARKET] 완료 → %s", WATCHLIST_PATH)

    def run_intraday(self) -> None:
        """Phase 2: VCP 브레이크아웃 감지 + 진입"""
        logger.info("[INTRADAY] 시작")
        if not WATCHLIST_PATH.exists():
            logger.warning("watchlist.json 없음, PREMARKET 먼저 실행 필요")
            return

        saved = json.loads(WATCHLIST_PATH.read_text())
        watchlist = [w["ticker"] for w in saved.get("watchlist", [])]
        if not watchlist:
            logger.info("[INTRADAY] 워치리스트 비어있음 → 스킵")
            return

        intraday = get_intraday_snapshot(watchlist)
        balance = self._broker.get_balance()
        screener_data = get_screener_data(watchlist)

        decisions = self._trader.run("INTRADAY", watchlist, screener_data, intraday, balance)
        self._execute_decisions(decisions)
        logger.info("[INTRADAY] 완료")

    def run_close(self) -> None:
        """Phase 3: 포지션 검토 + 손절/익절"""
        logger.info("[CLOSE] 시작")
        balance = self._broker.get_balance()
        positions = balance.get("positions", [])

        if not positions:
            logger.info("[CLOSE] 보유 포지션 없음 → 스킵")
            return

        watchlist = [p["ticker"] for p in positions]
        screener_data = get_screener_data(watchlist)

        decisions = self._trader.run("CLOSE", watchlist, screener_data, None, balance)
        self._execute_decisions(decisions)
        logger.info("[CLOSE] 완료")

    def _execute_decisions(self, decisions: list[dict]) -> None:
        for d in decisions:
            action = d.get("action")
            ticker = d.get("ticker", "")
            qty = d.get("quantity", 0)
            reason = d.get("reason", "")
            logger.info("결정: %s %s x%d — %s", action, ticker, qty, reason)
            if action == "BUY" and ticker and qty > 0:
                result = self._broker.buy(ticker, qty)
                logger.info("매수 결과: %s", result)
            elif action == "SELL" and ticker and qty > 0:
                result = self._broker.sell(ticker, qty)
                logger.info("매도 결과: %s", result)
```

- [ ] **Step 4: run_schedule_us.py 작성**

`/mnt/c/Users/gocho/MQK-US/run_schedule_us.py`:
```python
"""PM2 진입점. 인자로 phase 받아 해당 Phase 실행."""
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    from orchestrator_us import OrchestratorUS, is_market_open, get_current_phase

    phase_arg = sys.argv[1].upper() if len(sys.argv) > 1 else None
    force = os.environ.get("MQK_FORCE", "0") == "1"

    if not force and not is_market_open():
        logger.info("오늘 NYSE 휴장 — 스킵")
        return

    if not force:
        current = get_current_phase()
        if current != phase_arg:
            logger.info("현재 시간창(%s)이 요청 Phase(%s)와 불일치 — 스킵", current, phase_arg)
            return

    orch = OrchestratorUS()
    if phase_arg == "PREMARKET":
        orch.run_premarket()
    elif phase_arg == "INTRADAY":
        orch.run_intraday()
    elif phase_arg == "CLOSE":
        orch.run_close()
    else:
        logger.error("알 수 없는 phase: %s", phase_arg)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
cd /mnt/c/Users/gocho/MQK-US
python -m pytest tests/test_orchestrator_us.py -v
```

Expected: 6개 테스트 PASS

- [ ] **Step 6: 커밋**

```bash
cd /mnt/c/Users/gocho/MQK-US
git add orchestrator_us.py run_schedule_us.py tests/test_orchestrator_us.py
git commit -m "feat: Orchestrator 3-Phase + DST 시간창 가드"
```

---

## Task 7: Telegram + PM2 설정

**Files:**
- Create: `broker/telegram.py`
- Create: `ecosystem.config.cjs`

**Interfaces:**
- Consumes: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 환경변수
- Produces: `notify(message: str) -> None`

- [ ] **Step 1: broker/telegram.py 작성 (MQK-v2 간소화)**

`/mnt/c/Users/gocho/MQK-US/broker/telegram.py`:
```python
from __future__ import annotations
import logging
import os
import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
_API = f"https://api.telegram.org/bot{_BOT_TOKEN}"


def notify(message: str) -> None:
    """Telegram 메시지 전송. 실패 시 로그만 남기고 계속."""
    if not _BOT_TOKEN or not _CHAT_ID:
        logger.debug("Telegram 미설정 — 스킵: %s", message[:80])
        return
    try:
        resp = requests.post(
            f"{_API}/sendMessage",
            json={"chat_id": _CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Telegram 전송 실패: %s", e)
```

- [ ] **Step 2: Orchestrator에 Telegram 알림 추가**

`/mnt/c/Users/gocho/MQK-US/orchestrator_us.py` — `run_premarket` 끝에 추가:

기존 `logger.info("[PREMARKET] 완료 → %s", WATCHLIST_PATH)` 줄 다음에:
```python
        from broker.telegram import notify
        summary = result.get("scan_summary", "")
        notify(f"[MQK-US] PREMARKET 완료\n셋업: {len(watchlist)}개\n{summary}")
```

`run_intraday`의 `_execute_decisions` 호출 후:
```python
        from broker.telegram import notify
        buy_list = [d for d in decisions if d.get("action") == "BUY"]
        if buy_list:
            msgs = "\n".join(f"- {d['ticker']} x{d['quantity']}: {d['reason']}" for d in buy_list)
            notify(f"[MQK-US] 진입\n{msgs}")
```

`run_close`의 `_execute_decisions` 호출 후:
```python
        from broker.telegram import notify
        notify(f"[MQK-US] CLOSE 완료 | 포지션: {len(positions)}개")
```

- [ ] **Step 3: ecosystem.config.cjs 작성**

`/mnt/c/Users/gocho/MQK-US/ecosystem.config.cjs`:
```javascript
// DST 2벌 cron (KST 기준)
// 썸머타임 (3월~11월 둘째주): ET+13h = KST
// 윈터타임 (11월~3월):        ET+14h = KST
// MQK_FORCE=1 로 수동 강제 실행
const BASE = "/mnt/c/Users/gocho/MQK-US";
const PYTHON = "python3";

module.exports = {
  apps: [
    // ─── 썸머타임 (DST) ───
    {
      name: "us-premarket-dst",
      script: PYTHON,
      args: `${BASE}/run_schedule_us.py PREMARKET`,
      cwd: BASE,
      cron_restart: "30 21 * * 1-5",  // ET 08:30 = KST 21:30
      autorestart: false,
      watch: false,
    },
    {
      name: "us-intraday-dst",
      script: PYTHON,
      args: `${BASE}/run_schedule_us.py INTRADAY`,
      cwd: BASE,
      cron_restart: "30 0 * * 2-6",   // ET 11:30 = KST 00:30 (다음날)
      autorestart: false,
      watch: false,
    },
    {
      name: "us-close-dst",
      script: PYTHON,
      args: `${BASE}/run_schedule_us.py CLOSE`,
      cwd: BASE,
      cron_restart: "0 5 * * 2-6",    // ET 16:00 = KST 05:00 (다음날)
      autorestart: false,
      watch: false,
    },
    // ─── 윈터타임 (Standard) ───
    {
      name: "us-premarket-std",
      script: PYTHON,
      args: `${BASE}/run_schedule_us.py PREMARKET`,
      cwd: BASE,
      cron_restart: "30 22 * * 1-5",  // ET 08:30 = KST 22:30
      autorestart: false,
      watch: false,
    },
    {
      name: "us-intraday-std",
      script: PYTHON,
      args: `${BASE}/run_schedule_us.py INTRADAY`,
      cwd: BASE,
      cron_restart: "30 1 * * 2-6",   // ET 11:30 = KST 01:30 (다음날)
      autorestart: false,
      watch: false,
    },
    {
      name: "us-close-std",
      script: PYTHON,
      args: `${BASE}/run_schedule_us.py CLOSE`,
      cwd: BASE,
      cron_restart: "0 6 * * 2-6",    // ET 16:00 = KST 06:00 (다음날)
      autorestart: false,
      watch: false,
    },
  ],
};
```

> **운영 지침:** 썸머/윈터 전환 시기(3월, 11월)에 각각 DST 앱 enable / STD 앱 disable:
> `pm2 stop us-premarket-std && pm2 start us-premarket-dst`

- [ ] **Step 4: 커밋**

```bash
cd /mnt/c/Users/gocho/MQK-US
git add broker/telegram.py ecosystem.config.cjs orchestrator_us.py
git commit -m "feat: Telegram 알림 + PM2 DST 2벌 스케줄"
```

---

## Task 8: 전체 테스트 + D0 라이브 스모크

**Files:**
- Create: `tests/conftest.py`

**Interfaces:**
- 전 Task 연결 검증

- [ ] **Step 1: conftest.py 작성**

`/mnt/c/Users/gocho/MQK-US/tests/conftest.py`:
```python
import pytest
import os

@pytest.fixture(autouse=True)
def set_test_env(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("KIS_MODE", "paper")
    monkeypatch.setenv("KIS_PAPER_APP_KEY", "test")
    monkeypatch.setenv("KIS_PAPER_APP_SECRET", "test")
    monkeypatch.setenv("KIS_PAPER_ACCOUNT", "12345678-01")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
```

- [ ] **Step 2: 전체 테스트 스위트 실행**

```bash
cd /mnt/c/Users/gocho/MQK-US
pip install -r requirements.txt
python -m pytest tests/ -v --tb=short
```

Expected: 전 테스트 PASS (19개+)

- [ ] **Step 3: D0 라이브 스모크 — yfinance 연결**

```bash
cd /mnt/c/Users/gocho/MQK-US
python -c "
from data.market_data import get_universe, get_screener_data, get_news
tickers = get_universe()
print(f'유니버스: {len(tickers)}종목')
sample = tickers[:3]
data = get_screener_data(sample)
for t, d in data.items():
    print(f'{t}: 현재가={d[\"price\"]:.1f}, MA50={d[\"ma50\"]:.1f}')
news = get_news('NVDA')
print(f'NVDA 뉴스: {len(news)}개')
"
```

Expected: 유니버스 500+개, 각 종목 데이터 정상 출력

- [ ] **Step 4: D0 라이브 스모크 — KIS 해외주식 API**

`.env` 파일에 실제 KIS 키 설정 후:

```bash
cd /mnt/c/Users/gocho/MQK-US
python -c "
from broker.kis_us_api import KisUSApi
api = KisUSApi()
price = api.get_current_price('NVDA')
print(f'NVDA 현재가: {price}')
balance = api.get_balance()
print(f'잔고: {balance}')
"
```

Expected: NVDA 실시간 가격 및 계좌 잔고 출력

> 실패 시 `docs/kis_us_api_inventory.md` TR코드 확인 후 `broker/kis_us_api.py` 수정

- [ ] **Step 5: 최종 커밋**

```bash
cd /mnt/c/Users/gocho/MQK-US
git add tests/conftest.py
git commit -m "test: conftest + D0 라이브 스모크 완료"
```

---

## 구현 완료 기준

- [ ] `python -m pytest tests/ -v` — 전 테스트 PASS
- [ ] `python run_schedule_us.py PREMARKET MQK_FORCE=1` — watchlist.json 생성 확인
- [ ] KIS 해외주식 API DRY_RUN 매수/매도 정상 응답
- [ ] PM2 ecosystem 6개 앱 등록 (`pm2 start ecosystem.config.cjs`)
- [ ] Telegram 알림 수신 확인
