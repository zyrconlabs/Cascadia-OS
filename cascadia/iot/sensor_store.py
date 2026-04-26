"""
sensor_store.py — Cascadia OS v0.47
SensorStore: durable SQLite-backed sensor reading persistence.
Owns: sensor reading storage and query. Does not own routing or analysis.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SensorStore:
    """
    Owns sensor reading persistence.
    Does not own adapter lifecycle, trigger evaluation, or VANGUARD routing.
    """

    def __init__(self, db_path: str) -> None:
        self._db = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS sensor_readings (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id   TEXT NOT NULL,
                    topic       TEXT NOT NULL,
                    value       REAL,
                    payload     TEXT,
                    recorded_at TEXT NOT NULL
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_sensor_device ON sensor_readings(device_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_sensor_time ON sensor_readings(recorded_at)')

    def record(self, device_id: str, topic: str, payload: Any, value: Optional[float] = None) -> None:
        """Store a sensor reading."""
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                'INSERT INTO sensor_readings (device_id, topic, value, payload, recorded_at) VALUES (?, ?, ?, ?, ?)',
                (device_id, topic, value, json.dumps(payload), _now()),
            )

    def query(self, device_id: str, hours: int = 24) -> List[Dict[str, Any]]:
        """Return readings for a device in the past N hours."""
        from datetime import timedelta
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                'SELECT * FROM sensor_readings WHERE device_id=? AND recorded_at >= ? ORDER BY recorded_at DESC',
                (device_id, since),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d['payload'] = json.loads(d['payload']) if d['payload'] else None
            except Exception:
                pass
            result.append(d)
        return result

    def latest(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Return the most recent reading for a device."""
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                'SELECT * FROM sensor_readings WHERE device_id=? ORDER BY recorded_at DESC LIMIT 1',
                (device_id,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d['payload'] = json.loads(d['payload']) if d['payload'] else None
        except Exception:
            pass
        return d

    def list_devices(self) -> List[str]:
        """Return all distinct device IDs with recorded readings."""
        with sqlite3.connect(self._db) as conn:
            rows = conn.execute(
                'SELECT DISTINCT device_id FROM sensor_readings ORDER BY device_id'
            ).fetchall()
        return [r[0] for r in rows]

    def purge_old(self, retention_days: int = 90) -> int:
        """Delete readings older than retention_days. Returns count deleted."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        with sqlite3.connect(self._db) as conn:
            cursor = conn.execute(
                'DELETE FROM sensor_readings WHERE recorded_at < ?', (cutoff,)
            )
        return cursor.rowcount
