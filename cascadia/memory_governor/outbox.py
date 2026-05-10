"""
External action outbox — crash-safe, idempotent.

Prevents duplicate external side-effects after a restart.
OUTBOX_ENABLED=false (default): all methods are no-ops.
OUTBOX_ENABLED=true: SQLite-backed with deterministic keys.
"""

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

_DEFAULT_DB_PATH = "data/outbox.db"


class Outbox:
    """
    Idempotent outbox for external actions.

    Flow:
      enqueue()         → status: pending
      mark_executing()  → status: executing (call just before the action fires)
      mark_completed()  → status: completed
      mark_failed()     → status: failed

    Idempotency: inserting a duplicate idempotency_key is a no-op.
    Returns None when already queued or when OUTBOX_ENABLED=false.
    """

    def __init__(self, db_path: str = _DEFAULT_DB_PATH):
        self._db_path = db_path
        self._enabled = os.environ.get("OUTBOX_ENABLED", "false").lower() == "true"
        self._lock = threading.Lock()
        if self._enabled:
            if not db_path.startswith(":"):
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._conn() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS outbox (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id              TEXT NOT NULL,
                    action_type         TEXT NOT NULL,
                    payload_json        TEXT NOT NULL,
                    payload_hash        TEXT NOT NULL,
                    idempotency_key     TEXT NOT NULL UNIQUE,
                    status              TEXT NOT NULL DEFAULT 'pending',
                    created_at          REAL NOT NULL,
                    executed_at         REAL,
                    external_result_id  TEXT,
                    error_message       TEXT
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status)
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_outbox_run_id ON outbox(run_id)
            """)

    @staticmethod
    def make_idempotency_key(run_id: str, action_type: str, target: str) -> str:
        """
        Deterministic idempotency key from stable inputs.
        Never use random values — key must survive restarts.
        """
        raw = f"{run_id}:{action_type}:{target}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def enqueue(
        self,
        run_id: str,
        action_type: str,
        payload: dict,
        idempotency_key: str,
    ) -> Optional[int]:
        """
        Enqueue a pending external action.

        Returns the outbox row id if newly inserted.
        Returns None if already exists (idempotent) or if disabled.
        """
        if not self._enabled:
            return None

        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()

        with self._lock, self._conn() as db:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO outbox
                  (run_id, action_type, payload_json, payload_hash,
                   idempotency_key, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (run_id, action_type, payload_json, payload_hash,
                 idempotency_key, time.time()),
            )
            if cursor.rowcount > 0:
                return cursor.lastrowid
        return None

    def mark_executing(self, outbox_id: int) -> None:
        """Call just before the external action fires."""
        if not self._enabled:
            return
        self._set_status(outbox_id, "executing")

    def mark_completed(
        self, outbox_id: int, external_result_id: str = ""
    ) -> None:
        """Call after external action succeeds."""
        if not self._enabled:
            return
        with self._lock, self._conn() as db:
            db.execute(
                """
                UPDATE outbox
                SET status = 'completed',
                    executed_at = ?,
                    external_result_id = ?
                WHERE id = ?
                """,
                (time.time(), external_result_id, outbox_id),
            )

    def mark_failed(self, outbox_id: int, error: str) -> None:
        """Call when external action fails."""
        if not self._enabled:
            return
        with self._lock, self._conn() as db:
            db.execute(
                """
                UPDATE outbox
                SET status = 'failed',
                    executed_at = ?,
                    error_message = ?
                WHERE id = ?
                """,
                (time.time(), error, outbox_id),
            )

    def get_pending(self) -> list:
        """Return pending/executing actions for crash recovery on restart."""
        if not self._enabled:
            return []
        with self._lock, self._conn() as db:
            rows = db.execute(
                """
                SELECT * FROM outbox
                WHERE status IN ('pending', 'executing')
                ORDER BY created_at ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def is_completed(self, idempotency_key: str) -> bool:
        """Check if an action already completed successfully."""
        if not self._enabled:
            return False
        with self._lock, self._conn() as db:
            row = db.execute(
                "SELECT status FROM outbox WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            return row is not None and row["status"] == "completed"

    def _set_status(self, outbox_id: int, status: str) -> None:
        with self._lock, self._conn() as db:
            db.execute(
                "UPDATE outbox SET status = ? WHERE id = ?",
                (status, outbox_id),
            )
