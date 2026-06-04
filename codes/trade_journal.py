"""
Trade Journal - 거래 생애주기 관리 (SQLite)
LLM 미사용.

스키마:
  trades: 진입부터 청산까지 단일 레코드
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

_DEFAULT_DB = Path(__file__).parent.parent / "data" / "trades.db"


class TradeJournal:
    def __init__(self, db_path: Path = _DEFAULT_DB) -> None:
        self._db = Path(db_path)
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self._db)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker          TEXT NOT NULL,
                    name            TEXT NOT NULL,
                    entry_date      TEXT NOT NULL,
                    entry_price     REAL NOT NULL,
                    quantity        INTEGER NOT NULL,
                    stop_loss_price REAL NOT NULL,
                    entry_reason    TEXT,
                    confidence      INTEGER,
                    order_no        TEXT,
                    exit_date       TEXT,
                    exit_price      REAL,
                    exit_reason     TEXT,
                    pnl             REAL,
                    pnl_pct         REAL,
                    result          TEXT,
                    created_at      TEXT DEFAULT (datetime('now','localtime'))
                )
            """)

    def open_trade(
        self,
        ticker: str,
        name: str,
        entry_date: str,
        entry_price: float,
        quantity: int,
        stop_loss_price: float,
        entry_reason: str,
        confidence: int,
        order_no: str = "",
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO trades
                   (ticker, name, entry_date, entry_price, quantity,
                    stop_loss_price, entry_reason, confidence, order_no)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    ticker,
                    name,
                    entry_date,
                    entry_price,
                    quantity,
                    stop_loss_price,
                    entry_reason,
                    confidence,
                    order_no,
                ),
            )

    def close_trade(
        self,
        ticker: str,
        exit_date: str,
        exit_price: float,
        exit_reason: str,
    ) -> None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, entry_price, quantity FROM trades "
                "WHERE ticker=? AND exit_date IS NULL ORDER BY id DESC LIMIT 1",
                (ticker,),
            ).fetchone()
            if not row:
                return
            pnl = (exit_price - row["entry_price"]) * row["quantity"]
            pnl_pct = (
                (exit_price - row["entry_price"]) / row["entry_price"] * 100
            )
            result = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")
            conn.execute(
                """UPDATE trades SET exit_date=?, exit_price=?, exit_reason=?,
                   pnl=?, pnl_pct=?, result=? WHERE id=?""",
                (
                    exit_date,
                    exit_price,
                    exit_reason,
                    round(pnl, 0),
                    round(pnl_pct, 2),
                    result,
                    row["id"],
                ),
            )

    def get_open_positions(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE exit_date IS NULL ORDER BY entry_date"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_closed_trades(self, days: int = 30) -> list[dict]:
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE exit_date >= ? ORDER BY exit_date DESC",
                (since,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_daily_summary(self, date: str) -> dict:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE exit_date=?", (date,)
            ).fetchall()
        trades = [dict(r) for r in rows]
        wins = [t for t in trades if t.get("result") == "WIN"]
        losses = [t for t in trades if t.get("result") == "LOSS"]
        return {
            "date": date,
            "total_trades": len(trades),
            "win_trades": len(wins),
            "loss_trades": len(losses),
            "total_pnl": sum(t.get("pnl", 0) for t in trades),
            "win_rate": (
                round(len(wins) / len(trades) * 100, 1) if trades else 0.0
            ),
        }
