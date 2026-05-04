"""
dead_letter.py — Cascadia OS 2026.5
Dead-letter queue (DLQ) for unresolvable operator failures.
Owns: DLQ record creation, listing, and manual resolution.
Does not own: retry policy, escalation routing, or run execution.
"""
# MATURITY: PRODUCTION — Session E DLQ.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from cascadia.shared.db import connect, ensure_database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_RECOMMENDED_FIXES: Dict[str, str] = {
    "missing_connector":   "Connect the required integration in PRISM → Settings → Connectors.",
    "permission_denied":   "Review operator permissions in PRISM → Operators → Capabilities.",
    "insufficient_data":   "Provide the missing data via the Approvals queue and requeue.",
    "llm_timeout":         "Check AI model availability in PRISM → Settings → AI.",
    "external_api_failure":"Verify third-party service status and API credentials.",
    "operator_crash":      "Check operator logs in data/logs/ and restart the operator.",
    "heartbeat_stale":     "Check operator process health. Restart via start.sh.",
    "step_timeout":        "Increase step timeout in config or simplify the step payload.",
    "requires_decision":   "Review and action the pending decision in PRISM → Approvals.",
    "unknown":             "Inspect run_trace for this run_id and contact support.",
}


class DeadLetterQueue:
    """
    Manages dead-letter records for runs that have exhausted all retries.
    Does not own retry decisions — called only by Supervisor.
    """

    def __init__(self, database_path: str) -> None:
        self.database_path = str(Path(database_path))
        ensure_database(self.database_path)

    def _conn(self) -> sqlite3.Connection:
        return connect(self.database_path)

    def promote(
        self,
        run_id: str,
        step_id: str,
        failure_event: Any,
    ) -> str:
        """
        Write a DLQ record and mark the run as dead_letter.
        Returns the DLQ record id.
        Does not requeue or retry.
        """
        dlq_id = str(uuid4())
        ft = getattr(failure_event, "failure_type", "unknown")
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO dead_letters
                  (id, run_id, step_id, operator, failure_type, context,
                   attempts, last_error, recommended_fix, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    dlq_id,
                    run_id,
                    step_id,
                    getattr(failure_event, "operator", ""),
                    ft,
                    getattr(failure_event, "context", ""),
                    getattr(failure_event, "attempted", 0),
                    getattr(failure_event, "context", ""),
                    _RECOMMENDED_FIXES.get(ft, _RECOMMENDED_FIXES["unknown"]),
                    _now(),
                ),
            )
            # Mark run as dead_letter
            conn.execute(
                "UPDATE runs SET run_state='dead_letter', dead_letter_at=?, "
                "dead_letter_reason=? WHERE run_id=?",
                (_now(), getattr(failure_event, "context", ft), run_id),
            )
            conn.commit()
        finally:
            conn.close()
        return dlq_id

    def list_unresolved(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all unresolved DLQ records for PRISM display."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT dl.*, r.tenant_id, r.goal "
                "FROM dead_letters dl "
                "LEFT JOIN runs r ON r.run_id = dl.run_id "
                "WHERE dl.resolved = 0 "
                "ORDER BY dl.created_at DESC"
            ).fetchall()
        finally:
            conn.close()
        result = [dict(row) for row in rows]
        if tenant_id:
            result = [r for r in result if r.get("tenant_id") == tenant_id]
        return result

    def resolve(self, dlq_id: str, resolution_note: str) -> None:
        """
        Mark a DLQ record as manually resolved.
        Does NOT auto-resume the run — manual requeue required.
        """
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE dead_letters SET resolved=1, resolution_note=?, resolved_at=? "
                "WHERE id=?",
                (resolution_note, _now(), dlq_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get(self, dlq_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM dead_letters WHERE id=?", (dlq_id,)
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None
