"""
cascadia/settings/store.py
Owns: non-secret operator/connector settings persistence (SQLite).
Does not own: secret values (→ VAULT), engine routing, validation.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from cascadia.shared.manifest_schema import Manifest


_DB_PATH = "data/settings.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SecretFieldError(ValueError):
    """Raised when a caller tries to store a secret=True field in the settings store."""


class SettingsStore:
    """
    Owns non-secret settings persistence.
    Does not own secret values — those belong in VAULT.
    """

    def __init__(self, db_path: str = _DB_PATH) -> None:
        self._db = db_path
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._conn() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    target_type  TEXT NOT NULL,
                    target_id    TEXT NOT NULL,
                    field_name   TEXT NOT NULL,
                    value        TEXT,
                    updated_at   TEXT NOT NULL,
                    PRIMARY KEY (target_type, target_id, field_name)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS settings_revisions (
                    revision_id  INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_type  TEXT NOT NULL,
                    target_id    TEXT NOT NULL,
                    field_name   TEXT NOT NULL,
                    old_value    TEXT,
                    new_value    TEXT,
                    changed_at   TEXT NOT NULL,
                    source       TEXT NOT NULL
                )
            """)

    def get_setting(self, target_type: str, target_id: str, field_name: str) -> Any:
        with self._conn() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE target_type=? AND target_id=? AND field_name=?",
                (target_type, target_id, field_name),
            ).fetchone()
        return json.loads(row["value"]) if row else None

    def get_all_settings(self, target_type: str, target_id: str) -> Dict[str, Any]:
        with self._conn() as db:
            rows = db.execute(
                "SELECT field_name, value FROM settings WHERE target_type=? AND target_id=?",
                (target_type, target_id),
            ).fetchall()
        return {r["field_name"]: json.loads(r["value"]) for r in rows}

    def set_setting(
        self,
        target_type: str,
        target_id: str,
        field_name: str,
        value: Any,
        source: str,
        manifest: Optional[Manifest] = None,
    ) -> bool:
        self._check_not_secret(field_name, manifest)
        now = _now()
        old = self.get_setting(target_type, target_id, field_name)
        encoded = json.dumps(value)
        with self._lock, self._conn() as db:
            db.execute("""
                INSERT INTO settings (target_type, target_id, field_name, value, updated_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(target_type, target_id, field_name)
                DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """, (target_type, target_id, field_name, encoded, now))
            db.execute("""
                INSERT INTO settings_revisions
                  (target_type, target_id, field_name, old_value, new_value, changed_at, source)
                VALUES (?,?,?,?,?,?,?)
            """, (target_type, target_id, field_name,
                  json.dumps(old), encoded, now, source))
        return True

    def set_many_settings(
        self,
        target_type: str,
        target_id: str,
        changes: Dict[str, Any],
        source: str,
        manifest: Optional[Manifest] = None,
    ) -> bool:
        for field_name in changes:
            self._check_not_secret(field_name, manifest)
        now = _now()
        with self._lock, self._conn() as db:
            for field_name, value in changes.items():
                old_row = db.execute(
                    "SELECT value FROM settings WHERE target_type=? AND target_id=? AND field_name=?",
                    (target_type, target_id, field_name),
                ).fetchone()
                old_encoded = old_row["value"] if old_row else json.dumps(None)
                encoded = json.dumps(value)
                db.execute("""
                    INSERT INTO settings (target_type, target_id, field_name, value, updated_at)
                    VALUES (?,?,?,?,?)
                    ON CONFLICT(target_type, target_id, field_name)
                    DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """, (target_type, target_id, field_name, encoded, now))
                db.execute("""
                    INSERT INTO settings_revisions
                      (target_type, target_id, field_name, old_value, new_value, changed_at, source)
                    VALUES (?,?,?,?,?,?,?)
                """, (target_type, target_id, field_name,
                      old_encoded, encoded, now, source))
        return True

    def get_defaults(
        self, target_type: str, target_id: str, manifest: Manifest
    ) -> Dict[str, Any]:
        return {
            f.name: f.default
            for f in manifest.setup_fields
            if not f.secret
        }

    def reset_to_defaults(
        self,
        target_type: str,
        target_id: str,
        manifest: Manifest,
        source: str,
    ) -> Dict[str, Any]:
        defaults = self.get_defaults(target_type, target_id, manifest)
        if defaults:
            self.set_many_settings(target_type, target_id, defaults, source, manifest)
        return defaults

    def get_revisions(
        self, target_type: str, target_id: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        with self._conn() as db:
            rows = db.execute("""
                SELECT revision_id, field_name, old_value, new_value, changed_at, source
                FROM settings_revisions
                WHERE target_type=? AND target_id=?
                ORDER BY revision_id DESC
                LIMIT ?
            """, (target_type, target_id, limit)).fetchall()
        return [
            {
                "revision_id": r["revision_id"],
                "field_name": r["field_name"],
                "old_value": json.loads(r["old_value"]) if r["old_value"] else None,
                "new_value": json.loads(r["new_value"]) if r["new_value"] else None,
                "changed_at": r["changed_at"],
                "source": r["source"],
            }
            for r in rows
        ]

    # ── Internal ─────────────────────────────────────────────────────────────

    def _check_not_secret(
        self, field_name: str, manifest: Optional[Manifest]
    ) -> None:
        if manifest is None:
            return
        for f in manifest.setup_fields:
            if f.name == field_name and f.secret:
                raise SecretFieldError(
                    f"Field '{field_name}' is secret=True — store it in VAULT, not settings store."
                )
