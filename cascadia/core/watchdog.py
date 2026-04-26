"""
cascadia/core/watchdog.py — Cascadia OS
OperatorWatchdog: monitors autostart operators and restarts them on failure.
Owns: health polling, restart triggering, restart count tracking.
Does not own: operator configuration, execution, or routing.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OperatorWatchdog:
    """
    Monitors autostart operators from registry.json.
    Polls each operator's health endpoint every 30s.
    If an operator goes offline, triggers its start_cmd (if configured).
    """

    POLL_INTERVAL = 30

    def __init__(self, config: Dict[str, Any], logger: Any) -> None:
        self._config = config
        self._logger = logger
        self._status: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._watch_loop, daemon=True, name='OperatorWatchdog'
        )
        self._thread.start()
        self._logger.info('OperatorWatchdog started (interval=%ds)', self.POLL_INTERVAL)

    def stop(self) -> None:
        self._running = False

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                'operators': {k: dict(v) for k, v in self._status.items()},
                'generated_at': _now(),
                'poll_interval_seconds': self.POLL_INTERVAL,
            }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _watch_loop(self) -> None:
        while self._running:
            try:
                self._check_all()
            except Exception as exc:
                self._logger.warning('OperatorWatchdog check error: %s', exc)
            time.sleep(self.POLL_INTERVAL)

    def _load_registry(self) -> List[Dict[str, Any]]:
        configured = self._config.get('operators_registry_path', '')
        try:
            if configured:
                path = Path(configured).expanduser()
            else:
                path = Path(__file__).parent.parent / 'operators' / 'registry.json'
            return json.loads(path.read_text()).get('operators', [])
        except Exception:
            return []

    def _check_all(self) -> None:
        for op in self._load_registry():
            if not op.get('autostart'):
                continue
            self._check_operator(op)

    def _check_operator(self, op: Dict[str, Any]) -> None:
        op_id = op.get('id', '')
        port = op.get('port')
        if not port:
            return

        health_path = op.get('health_path', '/api/health')
        healthy = self._ping(port, health_path)

        with self._lock:
            prev = self._status.get(op_id, {'status': 'unknown', 'restart_count': 0})
            was_online = prev.get('status') == 'online'

            if not healthy and was_online:
                restart_count = prev.get('restart_count', 0) + 1
                self._logger.warning(
                    'OperatorWatchdog: %s went offline — restart attempt %d', op_id, restart_count
                )
                self._status[op_id] = {
                    'status': 'restarting',
                    'last_check': _now(),
                    'restart_count': restart_count,
                    'last_restart': _now(),
                    'port': port,
                }
                # Release lock before subprocess call
                self._lock.release()
                try:
                    self._restart_operator(op, restart_count)
                finally:
                    self._lock.acquire()
            else:
                new_status = 'online' if healthy else 'offline'
                if new_status != prev.get('status') and new_status == 'online':
                    self._logger.info('OperatorWatchdog: %s is back online', op_id)
                self._status[op_id] = {
                    'status': new_status,
                    'last_check': _now(),
                    'restart_count': prev.get('restart_count', 0),
                    'last_restart': prev.get('last_restart'),
                    'port': port,
                }

    def _ping(self, port: int, path: str) -> bool:
        try:
            with urllib.request.urlopen(
                f'http://127.0.0.1:{port}{path}', timeout=3
            ) as r:
                return r.status == 200
        except Exception:
            return False

    def _restart_operator(self, op: Dict[str, Any], attempt: int) -> None:
        op_id = op.get('id', '')
        start_cmd = op.get('start_cmd', '')
        if not start_cmd:
            self._logger.warning(
                'OperatorWatchdog: %s has no start_cmd — skipping restart', op_id
            )
            return
        try:
            subprocess.Popen(
                start_cmd, shell=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self._logger.info(
                'OperatorWatchdog: restart triggered for %s (attempt %d)', op_id, attempt
            )
        except Exception as exc:
            self._logger.error(
                'OperatorWatchdog: restart failed for %s: %s', op_id, exc
            )
