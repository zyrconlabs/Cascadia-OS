"""
health_alert.py — Cascadia OS
Monitors critical operators every 5 minutes. Emits FailureEvent and sends
email alert when an operator has been down for 2+ consecutive checks (10+ min).
Does not replace watchdog.py — watchdog owns FLINT liveness; this owns
operator-level health across the full stack.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger('health_alert')

PORT      = int(os.environ.get('HEALTH_ALERT_PORT', 6209))
HOST      = os.environ.get('HEALTH_ALERT_HOST', '127.0.0.1')
CHECK_SEC = int(os.environ.get('HEALTH_CHECK_INTERVAL', 300))   # 5 minutes
DOWN_THRESHOLD = 2                                               # 10+ min down → alert

_start_time = datetime.now(timezone.utc)

# Operators to monitor: (id, port, health_path)
WATCHED_OPERATORS = [
    ('CHIEF',  8006,  '/health'),
    ('SOCIAL', 8011,  '/api/health'),
    ('PRISM',  6300,  '/health'),
    ('EMAIL',  8010,  '/api/health'),
]

# Mutable state — protected by _lock
_down_counts: Dict[str, int] = {op[0]: 0 for op in WATCHED_OPERATORS}
_last_alert:  Dict[str, Optional[str]] = {op[0]: None for op in WATCHED_OPERATORS}
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_operator(name: str, port: int, path: str) -> bool:
    """Return True if the operator responds 200 on its health endpoint."""
    try:
        url = f'http://127.0.0.1:{port}{path}'
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _send_email_alert(name: str, down_since: str) -> None:
    """Send alert via the email operator (port 8010) if reachable."""
    try:
        from cascadia.shared.config import load_config
        config = load_config()
        from_email = config.get('email', {}).get('from_email', '')
        to_email   = config.get('email', {}).get('alert_email', from_email)
        if not to_email:
            return
        payload = json.dumps({
            'to':      to_email,
            'subject': f'[Zyrcon Alert] {name} is down',
            'body': (
                f'{name} went offline at {down_since}.\n'
                'Automatic restart has been attempted.\n'
                'Please check: http://localhost:6300'
            ),
        }).encode()
        req = urllib.request.Request(
            'http://127.0.0.1:8010/api/email/send',
            data=payload,
            headers={'Content-Type': 'application/json'},
        )
        urllib.request.urlopen(req, timeout=5)
        log.info('[HealthAlert] Alert email sent for %s', name)
    except Exception as exc:
        log.warning('[HealthAlert] Could not send alert email for %s: %s', name, exc)


def _emit_failure_event(name: str) -> None:
    """Emit FailureEvent via failure_event.py so supervisor.py can react."""
    try:
        from cascadia.automation.failure_event import (
            FailureEvent, publish_failure_event,
        )
        event = FailureEvent.from_operator_crash(operator=name.lower())
        publish_failure_event(event)
        log.info('[HealthAlert] FailureEvent emitted for %s', name)
    except Exception as exc:
        log.warning('[HealthAlert] Could not emit FailureEvent for %s: %s', name, exc)


def _log_alert(name: str) -> None:
    """Append a line to health_alerts.log."""
    log_path = Path('data/logs/health_alerts.log')
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, 'a') as f:
            f.write(f'{_now()} | DOWN | {name} has been unreachable for '
                    f'{CHECK_SEC * DOWN_THRESHOLD // 60}+ minutes\n')
    except Exception:
        pass


def _monitor_loop() -> None:
    """Background thread — checks all operators every CHECK_SEC seconds."""
    log.info('[HealthAlert] Monitor loop started — %d operators, interval %ds',
             len(WATCHED_OPERATORS), CHECK_SEC)
    while True:
        for name, port, path in WATCHED_OPERATORS:
            alive = _check_operator(name, port, path)
            with _lock:
                if alive:
                    if _down_counts[name] > 0:
                        log.info('[HealthAlert] %s recovered (was down %d checks)',
                                 name, _down_counts[name])
                    _down_counts[name] = 0
                else:
                    _down_counts[name] += 1
                    count = _down_counts[name]
                    log.warning('[HealthAlert] %s DOWN (check %d)', name, count)
                    if count >= DOWN_THRESHOLD:
                        down_since = _last_alert[name] or _now()
                        _last_alert[name] = down_since
                        _log_alert(name)
                        _emit_failure_event(name)
                        _send_email_alert(name, down_since)
        time.sleep(CHECK_SEC)


# ── Minimal HTTP health endpoint ──────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    def _json(self, code: int, body: Dict[str, Any]) -> None:
        data = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path.rstrip('/')
        if path in ('/health', '/api/health'):
            uptime = int((datetime.now(timezone.utc) - _start_time).total_seconds())
            with _lock:
                status = {op: cnt for op, cnt in _down_counts.items()}
            self._json(200, {
                'status':         'ok',
                'service':        'health_alert',
                'port':           PORT,
                'check_interval': CHECK_SEC,
                'down_threshold': DOWN_THRESHOLD,
                'operator_down_counts': status,
                'uptime_seconds': uptime,
                'generated_at':   _now(),
            })
        else:
            self._json(404, {'error': 'not_found'})

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s [health_alert] %(message)s',
    )
    threading.Thread(target=_monitor_loop, daemon=True, name='health_monitor').start()
    log.info('[HealthAlert] HTTP endpoint on port %d', PORT)
    server = HTTPServer((HOST, PORT), _Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info('[HealthAlert] Stopped')


if __name__ == '__main__':
    main()
