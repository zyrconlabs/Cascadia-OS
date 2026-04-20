"""
vault.py - Cascadia OS v0.43
VAULT: Private institutional memory.
Structured durable storage for operator knowledge, customer context,
approved outputs, and shared memory. Capability-checked on every access.
"""
# MATURITY: FUNCTIONAL — SQLite persistence and capability gating work. Semantic retrieval is v0.35.
from __future__ import annotations

import json
import urllib.request

import argparse
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime


class VaultStore:
    """Owns durable key-value storage for VAULT. Does not own capability enforcement."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._lock, self._conn() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS vault (
                    key         TEXT NOT NULL,
                    namespace   TEXT NOT NULL DEFAULT 'default',
                    value       TEXT,
                    created_by  TEXT,
                    created_at  TEXT,
                    updated_at  TEXT,
                    PRIMARY KEY (key, namespace)
                )
            """)
            db.commit()

    def read(self, key: str, namespace: str = 'default') -> Optional[Any]:
        with self._lock, self._conn() as db:
            row = db.execute('SELECT value FROM vault WHERE key=? AND namespace=?', (key, namespace)).fetchone()
        return json.loads(row['value']) if row else None

    def write(self, key: str, value: Any, created_by: str, namespace: str = 'default') -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._conn() as db:
            db.execute("""
                INSERT INTO vault (key, namespace, value, created_by, created_at, updated_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(key, namespace) DO UPDATE
                SET value=excluded.value, updated_at=excluded.updated_at
            """, (key, namespace, json.dumps(value), created_by, now, now))
            db.commit()

    def delete(self, key: str, namespace: str = 'default') -> bool:
        with self._lock, self._conn() as db:
            cur = db.execute('DELETE FROM vault WHERE key=? AND namespace=?', (key, namespace))
            db.commit()
        return cur.rowcount > 0

    def list_keys(self, namespace: str = 'default', prefix: str = '') -> List[str]:
        with self._lock, self._conn() as db:
            rows = db.execute('SELECT key FROM vault WHERE namespace=? AND key LIKE ?',
                              (namespace, f'{prefix}%')).fetchall()
        return [r['key'] for r in rows]


class VaultService:
    """
    VAULT - Private institutional memory service.
    Owns durable storage and capability-checked access.
    Does not own semantic retrieval, ranking, or memory summarization.
    """

    def __init__(self, config_path: str, name: str) -> None:
        self.config = load_config(config_path)
        component = next(c for c in self.config['components'] if c['name'] == name)
        self.runtime = ServiceRuntime(
            name=name, port=component['port'],
            heartbeat_file=component['heartbeat_file'],
            log_dir=self.config['log_dir'],
        )
        self.store = VaultStore(self.config['database_path'].replace('.db', '_vault.db'))
        self.runtime.register_route('POST', '/read', self.read)
        self.runtime.register_route('POST', '/write', self.write)
        self.runtime.register_route('POST', '/delete', self.delete)
        self.runtime.register_route('POST', '/list', self.list_keys)

    def _check_cap(self, payload: Dict[str, Any], required: str) -> Optional[tuple[int, Dict[str, Any]]]:
        """
        Check capability via CREW /validate endpoint.
        Falls back to payload-declared capabilities when CREW is unreachable
        (e.g. during startup or testing) so the system degrades gracefully.
        Owns: capability enforcement gate. Does not own registry or policy.
        """
        operator_id = payload.get('operator_id', '')
        if not operator_id:
            return 400, {'error': 'operator_id required'}

        # Primary: call CREW /validate
        if self._crew_port:
            try:
                body = json.dumps({'sender': operator_id, 'capability': required}).encode()
                req = urllib.request.Request(
                    f'http://127.0.0.1:{self._crew_port}/validate',
                    data=body, method='POST',
                    headers={'Content-Type': 'application/json'},
                )
                with urllib.request.urlopen(req, timeout=2) as r:
                    result = json.loads(r.read().decode())
                    if not result.get('ok', False):
                        return 403, {
                            'error': f'capability denied by CREW: {required}',
                            'operator_id': operator_id,
                        }
                    return None
            except Exception:
                pass  # CREW unreachable — fall through to payload fallback

        # Fallback: trust payload-declared capabilities (startup / offline mode)
        caps = payload.get('capabilities', [])
        has_cap = required in caps or any(
            c.endswith('*') and required.startswith(c[:-1]) for c in caps
        )
        if not has_cap:
            return 403, {'error': f'missing capability: {required}', 'operator_id': operator_id}
        return None

    def read(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        err = self._check_cap(payload, 'vault.read')
        if err:
            return err
        key = payload.get('key', '')
        namespace = payload.get('namespace', 'default')
        value = self.store.read(key, namespace)
        return 200, {'key': key, 'namespace': namespace, 'value': value, 'found': value is not None}

    def write(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        err = self._check_cap(payload, 'vault.write')
        if err:
            return err
        key = payload.get('key', '')
        value = payload.get('value')
        namespace = payload.get('namespace', 'default')
        operator_id = payload.get('operator_id', 'unknown')
        self.store.write(key, value, operator_id, namespace)
        self.runtime.logger.info('VAULT write: %s/%s by %s', namespace, key, operator_id)
        return 201, {'key': key, 'namespace': namespace, 'written': True}

    def delete(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        err = self._check_cap(payload, 'vault.write')
        if err:
            return err
        key = payload.get('key', '')
        namespace = payload.get('namespace', 'default')
        deleted = self.store.delete(key, namespace)
        return 200, {'key': key, 'deleted': deleted}

    def list_keys(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        err = self._check_cap(payload, 'vault.read')
        if err:
            return err
        namespace = payload.get('namespace', 'default')
        prefix = payload.get('prefix', '')
        keys = self.store.list_keys(namespace, prefix)
        return 200, {'namespace': namespace, 'keys': keys, 'count': len(keys)}

    def start(self) -> None:
        self.runtime.start()


def main() -> None:
    p = argparse.ArgumentParser(description='VAULT - Cascadia OS institutional memory')
    p.add_argument('--config', required=True)
    p.add_argument('--name', required=True)
    a = p.parse_args()
    VaultService(a.config, a.name).start()


if __name__ == '__main__':
    main()
