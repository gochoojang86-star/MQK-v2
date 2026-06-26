"""
Improvement Manager - 전략 개선 제안 관리
저장 → 텔레그램 통보 → 사용자 승인·거부 → 상태 추적
auto_apply는 항상 False — 예외 없음
"""
from __future__ import annotations

import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from agents.self_improvement_agent import ImprovementProposal
from config.runtime_overrides import write_runtime_overrides

_DEFAULT_DB = Path(__file__).parent.parent / "data" / "improvements.db"
_DEFAULT_OVERRIDE_PATH = Path(__file__).parent.parent / "data" / "approved_settings.json"
_ALLOWED_PATCH_KEYS = {
    "RISK": {
        "risk_per_trade_pct",
        "max_daily_loss_pct",
        "max_positions",
        "max_theme_exposure_pct",
        "max_single_position_pct",
        "stop_loss_method",
        "atr_multiplier",
        "max_stop_loss_pct",
        "allow_averaging_down",
        "require_telegram_approval",
    },
    "SCANNER": {
        "universe_size",
        "candidate_count",
        "final_candidates",
        "min_trading_value_krw",
    },
    "LLM_CONFIG": {
        "provider",
        "model_reasoning",
        "model_standard",
        "model_fast",
        "openrouter_base_url",
        "openrouter_http_referer",
        "openrouter_app_title",
        "openrouter_model_reasoning",
        "openrouter_model_standard",
        "openrouter_model_fast",
        "max_tokens",
        "temperature",
        "max_llm_calls_per_day",
    },
    "EXECUTION": {
        "order_dry_run",
    },
}


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
                    settings_patch  TEXT DEFAULT '[]',
                    requires_backtest INTEGER DEFAULT 1,
                    status          TEXT DEFAULT 'PENDING',
                    reject_reason   TEXT,
                    created_at      TEXT DEFAULT (datetime('now','localtime')),
                    updated_at      TEXT
                )
            """)
            # migration: add settings_patch if created before this column existed
            existing = {row[1] for row in conn.execute("PRAGMA table_info(proposals)")}
            if "settings_patch" not in existing:
                conn.execute("ALTER TABLE proposals ADD COLUMN settings_patch TEXT DEFAULT '[]'")

    def save(self, proposal: ImprovementProposal) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO proposals
                   (title, hypothesis, change_type, expected_effect, risk, settings_patch, requires_backtest)
                   VALUES (?,?,?,?,?,?,?)""",
                (proposal.title, proposal.hypothesis, proposal.change_type.value,
                 proposal.expected_effect, proposal.risk,
                 json.dumps(proposal.settings_patch, ensure_ascii=False),
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
                f"수동 명령 fallback: /approve {pid} | /reject {pid}"
            )
            if hasattr(self._telegram, "notify_improvement_proposal"):
                self._telegram.notify_improvement_proposal(pid, msg)
            else:
                self._telegram.notify(msg)
        return pid

    def approve(self, proposal_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE proposals SET status='APPROVED', updated_at=? WHERE id=?",
                (datetime.now().isoformat(), proposal_id),
            )
        self.apply_approved_settings()

    def reject(self, proposal_id: int, reason: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE proposals SET status='REJECTED', reject_reason=?, updated_at=? WHERE id=?",
                (reason, datetime.now().isoformat(), proposal_id),
            )
        self.apply_approved_settings()

    def process_telegram_actions(self) -> int:
        """텔레그램 인라인 버튼으로 들어온 개선 제안 승인/거부를 반영한다."""
        if not self._telegram or not hasattr(self._telegram, "poll_improvement_actions"):
            return 0
        processed = 0
        for action, proposal_id in self._telegram.poll_improvement_actions():
            pending_ids = {row["id"] for row in self.get_pending()}
            if proposal_id not in pending_ids:
                continue
            if action == "approve_proposal":
                self.approve(proposal_id)
                processed += 1
            elif action == "reject_proposal":
                self.reject(proposal_id, reason="텔레그램 인라인 거부")
                processed += 1
        return processed

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

    def apply_approved_settings(self, path: Path | None = None) -> Path:
        """승인된 개선안 중 실제 설정으로 해석 가능한 것만 JSON override로 저장한다."""
        override_path = Path(path or _DEFAULT_OVERRIDE_PATH)
        overrides: dict[str, dict[str, Any]] = {
            "RISK": {},
            "SCANNER": {},
            "LLM_CONFIG": {},
            "EXECUTION": {},
        }
        for proposal in self.get_approved():
            patches = proposal.get("settings_patch") or "[]"
            try:
                patch_items = json.loads(patches)
            except json.JSONDecodeError:
                continue
            if not isinstance(patch_items, list):
                continue
            for item in patch_items:
                if not isinstance(item, dict):
                    continue
                section = str(item.get("section", "")).strip()
                key = str(item.get("key", "")).strip()
                if section not in _ALLOWED_PATCH_KEYS or key not in _ALLOWED_PATCH_KEYS[section]:
                    continue
                overrides.setdefault(section, {})[key] = item.get("value")

        cleaned = {section: values for section, values in overrides.items() if values}
        write_runtime_overrides(cleaned, path=override_path)
        return override_path
