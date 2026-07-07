"""SQLite store for saved reports, preferences, feedback, and audit rows."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class ReportPreview:
    id: int
    created_at: float
    question: str
    tags: str | None = None


def _escape_like(term: str) -> str:
    """Escape SQLite LIKE wildcards so delete/report filters match literal user text.

    Parameters are still bound normally; this only prevents `%` and `_` from
    broadening the owner-scoped search scope unexpectedly.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class ReportsStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("SQLITE_PATH", "local_data/reports.sqlite3"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                deleted_at REAL,
                question TEXT NOT NULL,
                sql TEXT NOT NULL,
                report_text TEXT NOT NULL,
                tags TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_reports_owner_deleted ON reports(owner_id, deleted_at);
            CREATE INDEX IF NOT EXISTS idx_reports_owner_created ON reports(owner_id, created_at);
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id TEXT PRIMARY KEY,
                format TEXT DEFAULT 'bullets',
                tone TEXT DEFAULT 'concise_executive',
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                rating TEXT NOT NULL,
                note TEXT,
                question TEXT,
                sql TEXT,
                report_text TEXT,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id TEXT NOT NULL,
                action TEXT NOT NULL,
                target_ids TEXT NOT NULL,
                reason TEXT,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cli_session_state (
                thread_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                pending_confirmation_json TEXT,
                last_turn_json TEXT,
                updated_at REAL NOT NULL
            );
            """
        )
        # Backward-compatible migration from earlier skeletons.
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(feedback)").fetchall()}
        if "report_text" not in cols:
            self.conn.execute("ALTER TABLE feedback ADD COLUMN report_text TEXT")
        self.conn.commit()

    def save_report(self, *, owner_id: str, question: str, sql: str, report_text: str, tags: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO reports(owner_id, created_at, question, sql, report_text, tags) VALUES (?, ?, ?, ?, ?, ?)",
            (owner_id, time.time(), question, sql, report_text, tags),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_preferences(self, user_id: str) -> dict[str, str]:
        # Only values the user explicitly set via /prefs are returned. Filling in
        # hardcoded defaults here would permanently mask config/persona.yaml for every
        # user, because the reporter resolves preferences-then-persona-then-default.
        row = self.conn.execute("SELECT format, tone FROM user_preferences WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return {}
        return {k: v for k, v in (("format", row["format"]), ("tone", row["tone"])) if v}

    def update_preferences(self, user_id: str, *, format: str | None = None, tone: str | None = None) -> dict[str, str]:
        current = self.get_preferences(user_id)
        # Keep unset axes as NULL so persona.yaml still governs them.
        new_format = format or current.get("format")
        new_tone = tone or current.get("tone")
        self.conn.execute(
            """
            INSERT INTO user_preferences(user_id, format, tone, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET format=excluded.format, tone=excluded.tone, updated_at=excluded.updated_at
            """,
            (user_id, new_format, new_tone, time.time()),
        )
        self.conn.commit()
        return {k: v for k, v in (("format", new_format), ("tone", new_tone)) if v}

    def add_feedback(self, *, turn_id: str, user_id: str, rating: str, note: str = "", question: str = "", sql: str = "", report_text: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO feedback(turn_id, user_id, rating, note, question, sql, report_text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (turn_id, user_id, rating, note, question, sql, report_text, time.time()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_reports(
        self,
        owner_id: str,
        *,
        keyword: str | None = None,
        tags: str | None = None,
        start_ts: float | None = None,
        end_ts: float | None = None,
        latest: bool = False,
        limit: int = 20,
    ) -> list[ReportPreview]:
        params: list[object] = [owner_id]
        sql = "SELECT id, created_at, question, tags FROM reports WHERE owner_id=? AND deleted_at IS NULL"
        if keyword:
            sql += " AND (question LIKE ? ESCAPE '\\' OR report_text LIKE ? ESCAPE '\\' OR tags LIKE ? ESCAPE '\\')"
            like = f"%{_escape_like(keyword)}%"
            params.extend([like, like, like])
        if tags:
            sql += " AND tags LIKE ? ESCAPE '\\'"
            params.append(f"%{_escape_like(tags)}%")
        if start_ts is not None:
            sql += " AND created_at >= ?"
            params.append(start_ts)
        if end_ts is not None:
            sql += " AND created_at < ?"
            params.append(end_ts)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(1 if latest else limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [ReportPreview(id=row["id"], created_at=row["created_at"], question=row["question"], tags=row["tags"]) for row in rows]

    def resolve_delete_scope(
        self,
        *,
        owner_id: str,
        keyword: str | None = None,
        all_reports: bool = False,
        tags: str | None = None,
        start_ts: float | None = None,
        end_ts: float | None = None,
        latest: bool = False,
    ) -> list[ReportPreview]:
        if not any([all_reports, keyword, tags, start_ts is not None, end_ts is not None, latest]):
            return []
        return self.list_reports(owner_id, keyword=keyword, tags=tags, start_ts=start_ts, end_ts=end_ts, latest=latest, limit=500)

    def save_cli_session_state(
        self,
        *,
        thread_id: str,
        owner_id: str,
        pending_confirmation: dict | None = None,
        last_turn: dict | None = None,
    ) -> None:
        """Persist minimal CLI state so a classic process-per-run CLI is not amnesiac.

        LangGraph checkpoints are persisted separately through SqliteSaver when the
        optional langgraph-checkpoint-sqlite package is installed. This table is the
        local-first companion state for the CLI shell: pending delete confirmation
        payloads and the latest turn for feedback stay available after process restart.
        """
        current = self.load_cli_session_state(thread_id=thread_id, owner_id=owner_id)
        pending = pending_confirmation if pending_confirmation is not None else current.get("pending_confirmation")
        latest = last_turn if last_turn is not None else current.get("last_turn")
        self.conn.execute(
            """
            INSERT INTO cli_session_state(thread_id, owner_id, pending_confirmation_json, last_turn_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                owner_id=excluded.owner_id,
                pending_confirmation_json=excluded.pending_confirmation_json,
                last_turn_json=excluded.last_turn_json,
                updated_at=excluded.updated_at
            """,
            (
                thread_id,
                owner_id,
                json.dumps(pending) if pending else None,
                json.dumps(latest) if latest else None,
                time.time(),
            ),
        )
        self.conn.commit()

    def load_cli_session_state(self, *, thread_id: str, owner_id: str) -> dict:
        row = self.conn.execute(
            "SELECT pending_confirmation_json, last_turn_json FROM cli_session_state WHERE thread_id=? AND owner_id=?",
            (thread_id, owner_id),
        ).fetchone()
        if not row:
            return {"pending_confirmation": None, "last_turn": None}
        def _loads(value: str | None) -> dict | None:
            if not value:
                return None
            try:
                return json.loads(value)
            except Exception:
                return None
        return {"pending_confirmation": _loads(row["pending_confirmation_json"]), "last_turn": _loads(row["last_turn_json"])}

    def clear_pending_confirmation(self, *, thread_id: str, owner_id: str) -> None:
        current = self.load_cli_session_state(thread_id=thread_id, owner_id=owner_id)
        self.save_cli_session_state(thread_id=thread_id, owner_id=owner_id, pending_confirmation={}, last_turn=current.get("last_turn"))

    def soft_delete(self, *, owner_id: str, report_ids: Sequence[int], reason: str = "confirmed by user") -> int:
        if not report_ids:
            return 0
        placeholders = ",".join("?" for _ in report_ids)
        params: list[object] = [time.time(), owner_id, *report_ids]
        cur = self.conn.execute(
            f"UPDATE reports SET deleted_at=? WHERE owner_id=? AND deleted_at IS NULL AND id IN ({placeholders})",
            params,
        )
        deleted = cur.rowcount
        self.conn.execute(
            "INSERT INTO audit_log(actor_id, action, target_ids, reason, created_at) VALUES (?, 'soft_delete_reports', ?, ?, ?)",
            (owner_id, ",".join(map(str, report_ids)), reason, time.time()),
        )
        self.conn.commit()
        return int(deleted)
