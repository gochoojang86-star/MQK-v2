"""
Telegram News Collector - 텔레그램 채널 실시간 뉴스 수집
blacker/telegram_news_collector.py 기반.

사용하려면 .env에 두 값만 채우면 됩니다:
  TELEGRAM_API_ID=<숫자>
  TELEGRAM_API_HASH=<문자열>
발급: https://my.telegram.org/auth → API development tools

실행:
  python -m broker.telegram_news

orchestrator는 get_recent_news() 동기 함수로 DB에서 직접 읽습니다.
"""
from __future__ import annotations

import asyncio
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

_KST = timezone(timedelta(hours=9))
_OPERATE_START = 6   # KST 06:00
_OPERATE_END   = 21  # KST 21:00 (이 시각 이후 종료)

# ── 모니터링 채널 ──────────────────────────────────────────────────────────────
CHANNELS = [
    "FastStockNews",
    "realtime_stock_news",
    "moneythemestock",
    "stock_messenger",
]

# ── 감성 분류 키워드 ───────────────────────────────────────────────────────────
_POSITIVE = ["수주", "흑자", "급등", "상승", "호재", "신고가", "매수", "돌파", "계약", "공급",
             "실적", "흑전", "상향", "출시", "특허", "FDA", "승인"]
_NEGATIVE = ["하락", "적자", "급락", "손실", "악재", "하한가", "매도", "부도", "조사", "리콜",
             "소송", "손상", "취소", "철회", "감소", "실적부진"]
_SKIP = ["대통령", "날씨", "연예", "부동산", "스포츠", "야구", "축구", "농구", "배구", "선거"]

# ── 회사명 → 종목코드 매핑 ────────────────────────────────────────────────────
COMPANY_MAP: dict[str, str] = {
    "삼성전자": "005930", "SK하이닉스": "000660", "LG에너지솔루션": "373220",
    "삼성바이오로직스": "207940", "현대차": "005380", "기아": "000270",
    "NAVER": "035420", "카카오": "035720", "포스코홀딩스": "005490",
    "LG화학": "051910", "삼성SDI": "006400", "현대모비스": "012330",
    "KB금융": "105560", "신한지주": "055550", "하나금융지주": "086790",
    "셀트리온": "068270", "한국전력": "015760", "KT&G": "033780",
    "SK텔레콤": "017670", "LG전자": "066570", "에코프로비엠": "247540",
    "에코프로": "086520", "포스코퓨처엠": "003670", "두산에너빌리티": "034020",
    "한화에어로스페이스": "012450", "한화오션": "042660", "HD현대중공업": "329180",
    "삼성물산": "028260", "SK이노베이션": "096770", "롯데케미칼": "011170",
    "산일전기": "062040", "효성중공업": "298040", "HD현대일렉트릭": "267260",
    "LS일렉트릭": "010120", "현대로템": "064350", "LIG넥스원": "079550",
    "한화시스템": "272210", "이수페타시스": "007660", "고영": "098460",
}

DB_PATH = Path(__file__).parent.parent / "data" / "trader.db"


# ── DB 초기화 ──────────────────────────────────────────────────────────────────

def _init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS news_queue (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker     TEXT    DEFAULT '',
                title      TEXT    NOT NULL,
                content    TEXT    DEFAULT '',
                sentiment  TEXT,
                score      REAL,
                source     TEXT,
                url        TEXT    DEFAULT '',
                created_at TEXT    NOT NULL,
                processed  INTEGER DEFAULT 0
            )
        """)
        # 기존 DB에 content 컬럼 없으면 추가 (마이그레이션)
        try:
            conn.execute("ALTER TABLE news_queue ADD COLUMN content TEXT DEFAULT ''")
        except Exception:
            pass
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_processed ON news_queue(processed)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_created ON news_queue(created_at)"
        )


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _extract_ticker(text: str) -> str:
    m = re.search(r"code=(\d{6})", text)
    if m:
        return m.group(1)
    m = re.search(r"\b[A-Z]?(\d{6})\b", text)
    if m:
        return m.group(1)
    for name, code in COMPANY_MAP.items():
        if name in text:
            return code
    return ""


def _classify(text: str) -> tuple[str, float]:
    pos = sum(text.count(k) for k in _POSITIVE)
    neg = sum(text.count(k) for k in _NEGATIVE)
    if pos == neg:
        return "neutral", 0.5
    if pos > neg:
        return "positive", min(0.5 + pos * 0.1, 0.95)
    return "negative", min(0.5 + neg * 0.1, 0.95)


def _is_dup(conn: sqlite3.Connection, title: str) -> bool:
    cutoff = (datetime.now() - timedelta(minutes=30)).isoformat()
    return conn.execute(
        "SELECT 1 FROM news_queue WHERE substr(title,1,30)=? AND created_at>?",
        (title[:30], cutoff),
    ).fetchone() is not None


# ── 공개 읽기 인터페이스 (orchestrator에서 호출) ───────────────────────────────

def get_recent_news(ticker: str = "", hours: int = 2) -> list[dict]:
    """SQLite에서 최근 뉴스 조회 (동기). 텔레그램 수집기가 없으면 빈 리스트 반환."""
    if not DB_PATH.exists():
        return []
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        if ticker:
            rows = conn.execute(
                "SELECT ticker,title,content,sentiment,score,source,url,created_at "
                "FROM news_queue WHERE ticker=? AND created_at>? "
                "ORDER BY created_at DESC LIMIT 20",
                (ticker, cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ticker,title,content,sentiment,score,source,url,created_at "
                "FROM news_queue WHERE created_at>? "
                "ORDER BY created_at DESC LIMIT 50",
                (cutoff,),
            ).fetchall()
    return [
        {"ticker": r[0], "title": r[1], "content": r[2], "sentiment": r[3],
         "score": r[4], "source": r[5], "url": r[6], "date": r[7]}
        for r in rows
    ]


def _source_from_event(event) -> str:
    """Telethon event에서 채널 username을 안전하게 추출한다."""
    return getattr(getattr(event, "chat", None), "username", "") or ""


# ── 비동기 수집기 ─────────────────────────────────────────────────────────────

async def _run() -> None:
    try:
        from telethon import TelegramClient, events  # type: ignore
    except ImportError:
        print("[TelegramNews] telethon 미설치. 실행: pip install telethon")
        return

    api_id = os.environ.get("TELEGRAM_API_ID", "")
    api_hash = os.environ.get("TELEGRAM_API_HASH", "")
    if not api_id or not api_hash:
        print(
            "[TelegramNews] .env에 TELEGRAM_API_ID / TELEGRAM_API_HASH 를 설정하세요.\n"
            "발급: https://my.telegram.org/auth → API development tools"
        )
        return

    _init_db()
    session_path = str(Path(__file__).parent.parent / "data" / "mqk_news_session")
    client = TelegramClient(session_path, int(api_id), api_hash)

    @client.on(events.NewMessage(chats=CHANNELS))
    async def handler(event) -> None:
        msg = event.message
        text = msg.text or ""
        content = ""
        url = ""

        if msg.web_preview:
            wp = msg.web_preview
            text += " " + (wp.title or "")
            content = (wp.description or "")[:500]   # 기사 요약 최대 500자
            url = wp.url or ""

        if any(k in text for k in _SKIP) or len(text) < 10:
            return

        ticker = _extract_ticker(text)
        sentiment, score = _classify(text)
        title = text[:200]

        with sqlite3.connect(DB_PATH) as conn:
            if not _is_dup(conn, title):
                conn.execute(
                    "INSERT INTO news_queue"
                    "(ticker,title,content,sentiment,score,source,url,created_at)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (ticker, title, content, sentiment, score,
                     _source_from_event(event), url,
                     datetime.now().isoformat()),
                )

    # 시작 시점 운영 시간 확인 (06:00~21:00 KST)
    kst_now = datetime.now(_KST)
    if not (_OPERATE_START <= kst_now.hour < _OPERATE_END):
        print(f"[TelegramNews] 운영 시간 외({kst_now.strftime('%H:%M')} KST) — 시작 안 함")
        return

    print(f"[TelegramNews] 수집 시작. 채널: {CHANNELS} ({kst_now.strftime('%H:%M')} KST)")
    await client.start()

    # 운영 시간 감시 태스크 (1분마다 체크, 21:00 이후 자동 종료)
    async def _watch_hours() -> None:
        while True:
            await asyncio.sleep(60)
            h = datetime.now(_KST).hour
            if h >= _OPERATE_END or h < _OPERATE_START:
                print(f"[TelegramNews] 운영 시간 종료({h}:xx KST) — 연결 해제")
                await client.disconnect()
                return

    await asyncio.gather(
        client.run_until_disconnected(),
        _watch_hours(),
        return_exceptions=True,
    )


def run() -> None:
    """진입점 — python -m broker.telegram_news"""
    from dotenv import load_dotenv
    load_dotenv()
    asyncio.run(_run())


if __name__ == "__main__":
    run()
