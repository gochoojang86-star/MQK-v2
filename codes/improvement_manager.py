"""
Improvement Manager - 전략 개선 제안 관리
저장 → 텔레그램 통보 → 사용자 승인·거부 → 상태 추적
auto_apply는 항상 False — 예외 없음
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from agents.self_improvement_agent import ImprovementProposal

_DEFAULT_DB = Path(__file__).parent.parent / "data" / "improvements.db"


class ImprovementManager:
    def __init__(self, db_path: Path = _DEFAULT_DB, telegram=None):
        self._db = Path(db_path)
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._telegram = telegram
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
                CREATE TABLE IF NOT EXISTS proposals (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    title           TEXT NOT NULL,
                    hypothesis      TEXT,
                    change_type     TEXT,
                    expected_effect TEXT,
                    risk            TEXT,
                    requires_backtest INTEGER DEFAULT 1,
                    status          TEXT DEFAULT 'PENDING',
                    reject_reason   TEXT,
                    created_at      TEXT DEFAULT (datetime('now','localtime')),
                    updated_at      TEXT
                )
            """)

    def save(self, proposal: ImprovementProposal) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO proposals
                   (title, hypothesis, change_type, expected_effect, risk, requires_backtest)
                   VALUES (?,?,?,?,?,?)""",
                (proposal.title, proposal.hypothesis, proposal.change_type.value,
                 proposal.expected_effect, proposal.risk,
                 int(proposal.requires_backtest)),
            )
            pid = cur.lastrowid
        if self._telegram:
            msg = (
                f"💡 전략 개선 제안 (ID: {pid})\n"
                f"제목: {proposal.title}\n"
                f"가설: {proposal.hypothesis}\n"
                f"기대 효과: {proposal.expected_effect}\n"
                f"리스크: {proposal.risk}\n"
                f"백테스트 필요: {'예' if proposal.requires_backtest else '아니오'}\n"
                f"승인: /approve {pid} | 거부: /reject {pid}"
            )
            self._telegram.notify(msg)
        return pid

    def approve(self, proposal_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE proposals SET status='APPROVED', updated_at=? WHERE id=?",
                (datetime.now().isoformat(), proposal_id),
            )

    def reject(self, proposal_id: int, reason: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE proposals SET status='REJECTED', reject_reason=?, updated_at=? WHERE id=?",
                (reason, datetime.now().isoformat(), proposal_id),
            )

    def get_pending(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM proposals WHERE status='PENDING' ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_approved(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM proposals WHERE status='APPROVED' ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
