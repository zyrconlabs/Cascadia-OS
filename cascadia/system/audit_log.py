"""
audit_log.py — Cascadia OS v0.46
Immutable approval audit trail with SHA-256 chain hashing.
Owns: append-only audit event storage, chain integrity verification,
      export (CSV, JSON), and querying.
Does not own: event generation (ApprovalStore), display (PRISM),
              encryption (CURTAIN — future enhancement).
Design: Each audit event gets a SHA-256 chain hash linking it to the
        previous event. Tampering breaks the chain.
"""
# MATURITY: PRODUCTION — Append-only, chain-hashed, exportable.
from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from cascadia.shared.logger import get_logger

logger = get_logger('audit_log')

AUDIT_DB = Path('./data/runtime/audit.db')


class AuditLog:
    """Immutable, chain-hashed approval audit log."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or AUDIT_DB
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._last_hash = self._get_last_hash()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    approval_id INTEGER,
                    run_id TEXT,
                    actor TEXT,
                    decision TEXT,
                    action_key TEXT,
                    risk_level TEXT,
                    edited INTEGER DEFAULT 0,
                    edit_summary TEXT,
                    ts TEXT NOT NULL,
                    chain_hash TEXT NOT NULL
                )
            ''')

    def _get_last_hash(self) -> str:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                'SELECT chain_hash FROM audit_events ORDER BY id DESC LIMIT 1'
            ).fetchone()
        return row[0] if row else 'genesis'

    def _compute_hash(self, event: Dict[str, Any], prev_hash: str) -> str:
        content = json.dumps(event, sort_keys=True) + prev_hash
        return hashlib.sha256(content.encode()).hexdigest()

    def record(self, event_type: str, approval_id: Optional[int] = None,
               run_id: Optional[str] = None, actor: Optional[str] = None,
               decision: Optional[str] = None, action_key: Optional[str] = None,
               risk_level: Optional[str] = None, edited: bool = False,
               edit_summary: Optional[str] = None) -> None:
        """Append an audit event. Chain hash links to previous event."""
        ts = datetime.now(timezone.utc).isoformat()
        event = {
            'event_type': event_type, 'approval_id': approval_id,
            'run_id': run_id, 'actor': actor, 'decision': decision,
            'action_key': action_key, 'risk_level': risk_level,
            'edited': edited, 'edit_summary': edit_summary, 'ts': ts,
        }
        chain_hash = self._compute_hash(event, self._last_hash)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute('''
                INSERT INTO audit_events
                (event_type, approval_id, run_id, actor, decision,
                 action_key, risk_level, edited, edit_summary, ts, chain_hash)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ''', (event_type, approval_id, run_id, actor, decision,
                  action_key, risk_level, int(edited), edit_summary, ts, chain_hash))
        self._last_hash = chain_hash
        logger.info('AuditLog: recorded %s for approval %s', event_type, approval_id)

    def query(self, days: int = 30, actor: Optional[str] = None,
              decision: Optional[str] = None) -> List[Dict[str, Any]]:
        """Query audit events with optional filters."""
        where = [f"ts >= datetime('now', '-{days} days')"]
        params: list = []
        if actor:
            where.append('actor = ?')
            params.append(actor)
        if decision:
            where.append('decision = ?')
            params.append(decision)
        sql = f'SELECT * FROM audit_events WHERE {" AND ".join(where)} ORDER BY id DESC'
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def verify_chain(self) -> bool:
        """Verify the chain hash integrity. Returns False if any record was tampered."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                'SELECT * FROM audit_events ORDER BY id ASC'
            ).fetchall()
        prev = 'genesis'
        for row in rows:
            event = {k: row[k] for k in row.keys() if k not in ('chain_hash', 'id')}
            # SQLite returns edited as int; restore bool to match what record() hashed.
            event['edited'] = bool(event['edited'])
            expected = hashlib.sha256(
                (json.dumps(event, sort_keys=True) + prev).encode()
            ).hexdigest()
            if expected != row['chain_hash']:
                return False
            prev = row['chain_hash']
        return True

    def export_csv(self, days: int = 30) -> str:
        """Return CSV string of audit events."""
        rows = self.query(days=days)
        if not rows:
            return ''
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        return buf.getvalue()
