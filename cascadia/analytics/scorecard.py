"""
scorecard.py — Cascadia OS
SCORECARD: Daily business metrics tracking with idempotent upsert storage.

Owns: recording daily metrics, querying current/last month, date-range queries.
Does not own: display (PRISM), tier gating (license_gate), PDF generation (prism.py).

SQLite storage at data/scorecard.db.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCORECARD_DB = Path('./data/scorecard.db')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Scorecard:
    """Daily business metrics store. One row per date, idempotent upsert."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or SCORECARD_DB
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS daily_metrics (
                    date                      TEXT UNIQUE,
                    leads_captured            INTEGER DEFAULT 0,
                    proposals_drafted         INTEGER DEFAULT 0,
                    emails_sent               INTEGER DEFAULT 0,
                    approvals_completed       INTEGER DEFAULT 0,
                    approvals_rejected        INTEGER DEFAULT 0,
                    operator_runs             INTEGER DEFAULT 0,
                    failed_runs               INTEGER DEFAULT 0,
                    avg_response_time_seconds INTEGER DEFAULT 0,
                    created_at                TEXT,
                    updated_at                TEXT
                )
            ''')

    def record_today(self, metrics: Dict[str, Any]) -> None:
        """Upsert today's metrics row. Only provided fields are updated."""
        today = date.today().isoformat()
        now   = _now()
        with self._connect() as conn:
            existing = conn.execute(
                'SELECT * FROM daily_metrics WHERE date = ?', (today,)
            ).fetchone()
            if existing:
                sets = ', '.join(
                    f'{k} = ?' for k in metrics
                    if k not in ('date', 'created_at', 'updated_at')
                )
                vals = [
                    metrics[k] for k in metrics
                    if k not in ('date', 'created_at', 'updated_at')
                ]
                if sets:
                    conn.execute(
                        f'UPDATE daily_metrics SET {sets}, updated_at = ? WHERE date = ?',
                        vals + [now, today],
                    )
            else:
                fields = ['date', 'created_at', 'updated_at']
                values: List[Any] = [today, now, now]
                for k, v in metrics.items():
                    if k not in ('date', 'created_at', 'updated_at'):
                        fields.append(k)
                        values.append(v)
                placeholders = ', '.join('?' * len(fields))
                conn.execute(
                    f'INSERT INTO daily_metrics ({", ".join(fields)}) VALUES ({placeholders})',
                    values,
                )

    def _sum_rows(self, rows: List[sqlite3.Row]) -> Dict[str, Any]:
        """Sum all numeric metric columns across rows."""
        keys = [
            'leads_captured', 'proposals_drafted', 'emails_sent',
            'approvals_completed', 'approvals_rejected', 'operator_runs',
            'failed_runs', 'avg_response_time_seconds',
        ]
        result: Dict[str, Any] = {k: 0 for k in keys}
        count = 0
        rt_total = 0
        for row in rows:
            d = dict(row)
            for k in keys:
                if k == 'avg_response_time_seconds':
                    rt_total += d.get(k, 0) or 0
                else:
                    result[k] += d.get(k, 0) or 0
            count += 1
        result['avg_response_time_seconds'] = round(rt_total / count) if count else 0
        return result

    def get_current_month(self) -> Dict[str, Any]:
        """Return summed metrics for the current calendar month."""
        today = date.today()
        start = today.replace(day=1).isoformat()
        end   = today.isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT * FROM daily_metrics WHERE date >= ? AND date <= ?',
                (start, end),
            ).fetchall()
        return self._sum_rows(rows)

    def get_last_month(self) -> Dict[str, Any]:
        """Return summed metrics for the previous calendar month."""
        today = date.today()
        # First day of current month
        first_this = today.replace(day=1)
        # Last day of previous month
        last_prev  = first_this.replace(day=1) - __import__('datetime').timedelta(days=1)
        first_prev = last_prev.replace(day=1)
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT * FROM daily_metrics WHERE date >= ? AND date <= ?',
                (first_prev.isoformat(), last_prev.isoformat()),
            ).fetchall()
        return self._sum_rows(rows)

    def get_range(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """Return list of daily rows in [start_date, end_date] inclusive."""
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT * FROM daily_metrics WHERE date >= ? AND date <= ? ORDER BY date ASC',
                (start_date, end_date),
            ).fetchall()
        return [dict(r) for r in rows]
