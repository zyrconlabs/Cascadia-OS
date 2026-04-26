# MATURITY: PRODUCTION — Durable run records. Schema v2.1 complete.
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from cascadia.shared.db import connect, ensure_database


class RunStore:
    """Owns database access for runs and core query tables. Does not own policy or routing decisions."""

    def __init__(self, database_path: str) -> None:
        self.database_path = str(Path(database_path))
        ensure_database(self.database_path)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Owns transaction-scoped SQLite connections. Does not own cross-request pooling."""
        conn = connect(self.database_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def dump_json(value: Any) -> str:
        """Owns JSON serialization for SQLite TEXT columns. Does not own schema design."""
        return json.dumps(value if value is not None else {}, sort_keys=True)

    @staticmethod
    def load_json(value: Optional[str]) -> Any:
        """Owns JSON deserialization for SQLite TEXT columns. Does not own caller defaults."""
        return json.loads(value) if value else {}

    def create_run(self, record: Dict[str, Any]) -> None:
        """Owns insertion of run records. Does not own workflow planning."""
        with self.connection() as conn:
            conn.execute(
                '''
                INSERT INTO runs (
                    run_id, operator_id, tenant_id, goal, current_step,
                    input_snapshot, state_snapshot, retry_count, last_checkpoint,
                    process_state, run_state, blocked_reason, blocking_entity,
                    dependency_request, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ''',
                (
                    record['run_id'], record['operator_id'], record.get('tenant_id', 'default'), record.get('goal', ''), record.get('current_step', 'pending'),
                    self.dump_json(record.get('input_snapshot', {})), self.dump_json(record.get('state_snapshot', {})), record.get('retry_count', 0), record.get('last_checkpoint'),
                    record.get('process_state', 'starting'), record.get('run_state', 'pending'), record.get('blocked_reason'), record.get('blocking_entity'),
                    self.dump_json(record.get('dependency_request')) if record.get('dependency_request') is not None else None, record['created_at'], record['updated_at'],
                ),
            )

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Owns loading one run by ID. Does not own absent-run recovery beyond None."""
        with self.connection() as conn:
            row = conn.execute('SELECT * FROM runs WHERE run_id = ?', (run_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item['input_snapshot'] = self.load_json(item['input_snapshot'])
        item['state_snapshot'] = self.load_json(item['state_snapshot'])
        item['dependency_request'] = self.load_json(item['dependency_request']) if item['dependency_request'] else None
        return item

    def update_run(self, run_id: str, **updates: Any) -> None:
        """Owns partial run updates. Does not own semantic validation beyond field persistence."""
        if not updates:
            return
        parts, values = [], []
        for key, value in updates.items():
            if key in {'input_snapshot', 'state_snapshot', 'dependency_request'}:
                value = self.dump_json(value) if value is not None else None
            parts.append(f'{key} = ?')
            values.append(value)
        values.append(run_id)
        with self.connection() as conn:
            conn.execute(f"UPDATE runs SET {', '.join(parts)} WHERE run_id = ?", values)

    def set_blocked(self, run_id: str, reason: str, entity: str, request_payload: Dict[str, Any]) -> None:
        """Owns blocked-state persistence. Does not own dependency discovery itself."""
        self.update_run(run_id, run_state='blocked', blocked_reason=reason, blocking_entity=entity, dependency_request=request_payload)

    def clear_blocked(self, run_id: str) -> None:
        """Owns clearing blocked fields. Does not own wake policies."""
        self.update_run(run_id, blocked_reason=None, blocking_entity=None, dependency_request=None)

    def insert_approval(self, row: Dict[str, Any]) -> int:
        """Owns approval row insertion. Does not own approval UI or policy decisions."""
        with self.connection() as conn:
            cur = conn.execute('INSERT INTO approvals (run_id, step_index, action_key, decision, actor, reason, created_at, decided_at) VALUES (?,?,?,?,?,?,?,?)', (row['run_id'], row['step_index'], row['action_key'], row['decision'], row.get('actor'), row.get('reason', ''), row['created_at'], row.get('decided_at')))
            return int(cur.lastrowid)

    def update_approval(self, approval_id: int, **updates: Any) -> None:
        """Owns approval row updates. Does not own transition policy beyond caller intent."""
        if not updates:
            return
        parts = [f'{key} = ?' for key in updates]
        values = list(updates.values()) + [approval_id]
        with self.connection() as conn:
            conn.execute(f"UPDATE approvals SET {', '.join(parts)} WHERE id = ?", values)

    def latest_approval(self, run_id: str, action_key: str) -> Optional[Dict[str, Any]]:
        """Owns lookup of the latest approval for one action. Does not own merge semantics across actions."""
        with self.connection() as conn:
            row = conn.execute('SELECT * FROM approvals WHERE run_id = ? AND action_key = ? ORDER BY id DESC LIMIT 1', (run_id, action_key)).fetchone()
        return dict(row) if row else None

    def pending_approvals(self, run_id: str) -> List[Dict[str, Any]]:
        """Owns pending approval queries. Does not own notification or UI behavior."""
        with self.connection() as conn:
            rows = conn.execute('SELECT * FROM approvals WHERE run_id = ? AND decision = ? ORDER BY id ASC', (run_id, 'pending')).fetchall()
        return [dict(row) for row in rows]

    def trace_event(self, run_id: str, event_type: str, step_index: int | None, payload: Dict[str, Any], created_at: str) -> None:
        """Owns insertion into run_trace. Does not own metrics aggregation."""
        with self.connection() as conn:
            conn.execute('INSERT INTO run_trace (run_id, event_type, step_index, payload, created_at) VALUES (?,?,?,?,?)', (run_id, event_type, step_index, self.dump_json(payload), created_at))

    def avg_response_time_minutes(self, limit: int = 100) -> Optional[float]:
        """Average minutes from lead_received_at to run completion for recent completed runs."""
        try:
            from datetime import datetime as _dt
            with self.connection() as conn:
                rows = conn.execute(
                    "SELECT lead_received_at, updated_at FROM runs "
                    "WHERE run_state = 'completed' AND lead_received_at IS NOT NULL "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            if not rows:
                return None
            deltas = []
            for r in rows:
                try:
                    t0 = _dt.fromisoformat(r['lead_received_at'])
                    t1 = _dt.fromisoformat(r['updated_at'])
                    deltas.append((t1 - t0).total_seconds() / 60)
                except Exception:
                    pass
            return round(sum(deltas) / len(deltas), 1) if deltas else None
        except Exception:
            return None

    def record_outcome(self, run_id: str, outcome: str, recorded_at: str) -> None:
        """Record win/loss outcome for a completed run. outcome must be 'won' | 'lost' | 'no_decision'."""
        if outcome not in ('won', 'lost', 'no_decision'):
            raise ValueError(f'Invalid outcome: {outcome!r}')
        self.update_run(run_id, outcome=outcome, outcome_recorded_at=recorded_at)

    def approval_analytics(self, days: int = 30) -> Dict[str, Any]:
        """Owns approval analytics aggregation. Does not own UI rendering or policy decisions."""
        try:
            with self.connection() as conn:
                rows = conn.execute(
                    "SELECT decision, actor, risk_level, edited_content, created_at, decided_at "
                    "FROM approvals "
                    "WHERE created_at >= datetime('now', ? || ' days')",
                    (f'-{days}',),
                ).fetchall()
        except Exception:
            rows = []

        total = len(rows)
        approved = sum(1 for r in rows if r['decision'] == 'approved' and r['actor'] != 'system:timeout')
        rejected = sum(1 for r in rows if r['decision'] == 'denied')
        timed_out = sum(1 for r in rows if r['actor'] == 'system:timeout')
        edited = sum(1 for r in rows if r['edited_content'])

        decision_minutes: list = []
        for r in rows:
            if r['created_at'] and r['decided_at']:
                try:
                    from datetime import datetime as _dt
                    t0 = _dt.fromisoformat(r['created_at'])
                    t1 = _dt.fromisoformat(r['decided_at'])
                    decision_minutes.append((t1 - t0).total_seconds() / 60)
                except Exception:
                    pass
        avg_decision_minutes = round(sum(decision_minutes) / len(decision_minutes), 1) if decision_minutes else None

        by_risk: Dict[str, int] = {}
        for r in rows:
            level = (r['risk_level'] or 'MEDIUM').upper()
            by_risk[level] = by_risk.get(level, 0) + 1

        return {
            'total': total,
            'approved': approved,
            'rejected': rejected,
            'edited': edited,
            'timed_out': timed_out,
            'avg_decision_minutes': avg_decision_minutes,
            'by_risk': by_risk,
            'days': days,
        }
