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

_UNIVERSE_CSV = Path(__file__).parent.parent / "data" / "universe.csv"
_NAME_MAP: list[tuple[str, str]] | None = None  # (종목명, 코드), 이름 긴 순

# ETF 브랜드 prefix — 한국 ETF는 "브랜드 + 공백 + ..." 명명 규칙
_ETF_BRANDS = (
    "KODEX", "TIGER", "PLUS", "SOL", "ACE", "KBSTAR", "RISE", "HANARO",
    "ARIRANG", "KOSEF", "KIWOOM", "WON", "마이다스", "에셋플러스", "TIMEFOLIO",
    "BNK", "DAISHIN", "FOCUS", "HK", "ITF", "KCGI", "KoAct", "TREX",
    "UNICORN", "VITA", "WOORI", "1Q", "히어로즈", "파워", "마이티",
)


def _is_tradable_common_stock(name: str, ticker: str) -> bool:
    """뉴스 태깅 대상인 보통주만 남긴다 — 우선주/ETF/ETN/스팩 제외.

    우선주는 이름('~우')이 아니라 KRX 코드 규칙(보통주=끝자리 0)으로 거른다 —
    '에코글로우'/'성우'처럼 '우'로 끝나는 보통주가 있어 이름 휴리스틱은 위험하다.
    """
    if ticker[-1] != "0":  # 우선주/전환우선주 등 (KRX 보통주는 끝자리 0)
        return False
    upper = name.upper()
    if "ETN" in upper or "스팩" in name:
        return False
    first_word = upper.split(" ")[0] if " " in upper else ""
    if first_word and first_word in (b.upper() for b in _ETF_BRANDS):
        return False
    return True


def _load_name_map(path: Path = _UNIVERSE_CSV, force: bool = False) -> list[tuple[str, str]]:
    """보통주 종목명→코드 매핑 (universe.csv 기반, 실패 시 COMPANY_MAP 폴백).

    이름이 긴 순으로 정렬해 부분 문자열 오매핑을 방지한다.
    우선주가 사전에서 빠지므로 "삼성전자우 배당" 같은 텍스트는 본주(005930)로
    정규화되어 태깅된다 (v3 매매 대상이 보통주뿐이므로 의도된 동작).
    """
    global _NAME_MAP
    if _NAME_MAP is not None and not force:
        return _NAME_MAP
    import csv

    pairs: list[tuple[str, str]] = []
    try:
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                name = (row.get("name") or "").strip()
                ticker = (row.get("ticker") or "").strip()
                if (len(name) >= 2 and len(ticker) == 6 and ticker.isdigit()
                        and _is_tradable_common_stock(name, ticker)):
                    pairs.append((name, ticker))
    except OSError:
        pass
    if not pairs:
        pairs = list(COMPANY_MAP.items())
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    _NAME_MAP = pairs
    return _NAME_MAP


def _extract_ticker(text: str) -> str:
    m = re.search(r"code=(\d{6})", text)
    if m:
        return m.group(1)
    m = re.search(r"\b[A-Z]?(\d{6})\b", text)
    if m:
        return m.group(1)
    for name, code in _load_name_map():
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


def _is_operating_hour(now: datetime | None = None) -> bool:
    current = now or datetime.now(_KST)
    return _OPERATE_START <= current.hour < _OPERATE_END


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


def _normalize_channel_id(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        raw = abs(int(value))
    except (TypeError, ValueError):
        return None

    # Telethon chat_id는 -1001234567890 형태, entity.id는 1234567890 형태다.
    raw_str = str(raw)
    if raw_str.startswith("100") and len(raw_str) > 10:
        return int(raw_str[3:])
    return raw


def _source_from_event(event, alias_map: dict[int, str] | None = None) -> str:
    """Telethon event에서 채널 식별자를 안전하게 추출한다."""
    alias_map = alias_map or {}
    chat = getattr(event, "chat", None)

    for candidate in (
        getattr(chat, "id", None) if chat is not None else None,
        getattr(event, "chat_id", None),
    ):
        normalized = _normalize_channel_id(candidate)
        if normalized is not None and normalized in alias_map:
            return alias_map[normalized]

    username = getattr(chat, "username", "") if chat is not None else ""
    if username:
        return username

    title = getattr(chat, "title", "") if chat is not None else ""
    if title:
        return title

    chat_id = getattr(event, "chat_id", None)
    if chat_id is not None:
        return str(chat_id)

    return "telegram"


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
    alias_map: dict[int, str] = {}

    @client.on(events.NewMessage(chats=CHANNELS))
    async def handler(event) -> None:
        try:
            if not _is_operating_hour():
                return

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
            source = _source_from_event(event, alias_map)

            with sqlite3.connect(DB_PATH) as conn:
                if not _is_dup(conn, title):
                    conn.execute(
                        "INSERT INTO news_queue"
                        "(ticker,title,content,sentiment,score,source,url,created_at)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (ticker, title, content, sentiment, score,
                         source, url, datetime.now().isoformat()),
                    )
        except Exception as exc:
            print(f"[TelegramNews] handler error: {exc}")

    kst_now = datetime.now(_KST)
    state = "수집 시작" if _is_operating_hour(kst_now) else "대기 시작"
    print(f"[TelegramNews] {state}. 채널: {CHANNELS} ({kst_now.strftime('%H:%M')} KST)")
    await client.start()
    for channel in CHANNELS:
        try:
            entity = await client.get_entity(channel)
            normalized = _normalize_channel_id(getattr(entity, "id", None))
            if normalized is not None:
                alias_map[normalized] = channel
        except Exception as exc:
            print(f"[TelegramNews] channel resolve failed: {channel} -> {exc}")
    await client.run_until_disconnected()


def run() -> None:
    """진입점 — python -m broker.telegram_news"""
    from dotenv import load_dotenv
    load_dotenv()
    asyncio.run(_run())


if __name__ == "__main__":
    run()
