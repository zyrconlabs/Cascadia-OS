"""
cascadia/connectors/approval/connector.py — CON-116
Approval Gate Connector · Zyrcon Labs · v1.0.0

Owns: approval request queuing, timeout tracking, outcome publishing,
      HTTP API for approvers to view pending requests and submit decisions.
Does not own: notification delivery (Slack/email connectors do that),
              credential storage, final action execution.
"""
from __future__ import annotations

import asyncio
import http.server
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

try:
    import nats
    _NATS_AVAILABLE = True
except ImportError:
    _NATS_AVAILABLE = False

NAME = "approval-gate"
VERSION = "1.0.0"
PORT = 9988

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [approval-gate] %(message)s',
)
log = logging.getLogger(NAME)

_start_time = time.time()
_nc: Any = None
_loop: Optional[asyncio.AbstractEventLoop] = None

DEFAULT_TIMEOUT_SECONDS = 3600  # 1 hour


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None:
        _loop = asyncio.new_event_loop()
        threading.Thread(target=_loop.run_forever, daemon=True).start()
    return _loop


# ── Decision model ────────────────────────────────────────────────────────────

class Decision(str, Enum):
    PENDING = 'pending'
    APPROVED = 'approved'
    DENIED = 'denied'
    TIMED_OUT = 'timed_out'


@dataclass
class ApprovalRequest:
    request_id: str
    connector: str                   # connector/operator that requested approval
    description: str                 # human-readable description of the action
    action: Dict[str, Any]           # the payload to re-publish on approval
    reply_subject: str               # NATS subject to publish outcome to
    timeout_seconds: float
    decision: str = Decision.PENDING
    decided_by: Optional[str] = None
    reason: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    decided_at: Optional[float] = None

    @property
    def expired(self) -> bool:
        return (self.decision == Decision.PENDING and
                time.time() > self.created_at + self.timeout_seconds)


# ── Request registry ──────────────────────────────────────────────────────────

_requests: Dict[str, ApprovalRequest] = {}
_requests_lock = threading.Lock()


def _pending_requests() -> List[ApprovalRequest]:
    with _requests_lock:
        return [r for r in _requests.values() if r.decision == Decision.PENDING and not r.expired]


# ── Request lifecycle ─────────────────────────────────────────────────────────

def create_request(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a new approval request.
    Required: connector, description, action, reply_subject
    Optional: timeout_seconds (default 3600), request_id
    """
    connector = data.get('connector', '')
    description = data.get('description', '')
    action = data.get('action', {})
    reply_subject = data.get('reply_subject', '')

    if not connector:
        return {'ok': False, 'error': 'connector is required'}
    if not description:
        return {'ok': False, 'error': 'description is required'}
    if not reply_subject:
        return {'ok': False, 'error': 'reply_subject is required'}

    request_id = data.get('request_id') or str(uuid.uuid4())
    timeout = float(data.get('timeout_seconds', DEFAULT_TIMEOUT_SECONDS))

    req = ApprovalRequest(
        request_id=request_id,
        connector=connector,
        description=description,
        action=action,
        reply_subject=reply_subject,
        timeout_seconds=timeout,
    )

    with _requests_lock:
        _requests[request_id] = req

    log.info('Approval request queued: %s — %s (timeout=%ss)', request_id, description, timeout)
    return {'ok': True, 'request_id': request_id, 'status': Decision.PENDING}


def decide(request_id: str, decision: str, decided_by: str = '',
           reason: str = '') -> Dict[str, Any]:
    """
    Record an approval or denial decision.
    decision must be 'approved' or 'denied'.
    """
    if decision not in (Decision.APPROVED, Decision.DENIED):
        return {'ok': False, 'error': f'decision must be approved or denied, got {decision!r}'}

    with _requests_lock:
        req = _requests.get(request_id)

    if req is None:
        return {'ok': False, 'error': f'request {request_id!r} not found'}
    if req.decision != Decision.PENDING:
        return {'ok': False, 'error': f'request already {req.decision}'}
    if req.expired:
        req.decision = Decision.TIMED_OUT
        return {'ok': False, 'error': 'request has expired'}

    req.decision = decision
    req.decided_by = decided_by
    req.reason = reason
    req.decided_at = time.time()

    log.info('Request %s → %s by %s', request_id, decision, decided_by or 'system')

    # Publish outcome to the requester's reply subject
    if _nc is not None:
        outcome = _build_outcome(req)
        asyncio.run_coroutine_threadsafe(
            _nc.publish(req.reply_subject, json.dumps(outcome).encode()),
            _get_loop(),
        )

    return {'ok': True, 'request_id': request_id, 'decision': decision}


def _build_outcome(req: ApprovalRequest) -> Dict[str, Any]:
    return {
        'connector': NAME,
        'request_id': req.request_id,
        'decision': req.decision,
        'decided_by': req.decided_by,
        'reason': req.reason,
        'action': req.action if req.decision == Decision.APPROVED else None,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }


# ── Timeout watcher ───────────────────────────────────────────────────────────

def _expire_timed_out() -> None:
    with _requests_lock:
        expired = [r for r in _requests.values()
                   if r.decision == Decision.PENDING and r.expired]

    for req in expired:
        req.decision = Decision.TIMED_OUT
        req.decided_at = time.time()
        log.warning('Request %s timed out after %ss', req.request_id, req.timeout_seconds)

        if _nc is not None:
            outcome = _build_outcome(req)
            asyncio.run_coroutine_threadsafe(
                _nc.publish(req.reply_subject, json.dumps(outcome).encode()),
                _get_loop(),
            )


def _timeout_watcher() -> None:
    while True:
        try:
            _expire_timed_out()
        except Exception as exc:
            log.error('Timeout watcher error: %s', exc)
        time.sleep(30)


# ── HTTP server ───────────────────────────────────────────────────────────────

class _ApprovalHandler(http.server.BaseHTTPRequestHandler):

    def _json(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> bytes:
        n = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(n) if n else b''

    def do_GET(self) -> None:
        path = self.path.split('?')[0].rstrip('/')

        if path == '/health':
            pending = len(_pending_requests())
            self._json(200, {
                'status': 'healthy', 'connector': NAME, 'version': VERSION,
                'port': PORT, 'pending_requests': pending,
                'uptime_seconds': round(time.time() - _start_time),
            })
        elif path == '/requests':
            with _requests_lock:
                all_reqs = [asdict(r) for r in _requests.values()]
            self._json(200, {'ok': True, 'requests': all_reqs})
        elif path == '/requests/pending':
            reqs = [asdict(r) for r in _pending_requests()]
            self._json(200, {'ok': True, 'requests': reqs})
        elif path.startswith('/requests/'):
            request_id = path[len('/requests/'):]
            with _requests_lock:
                req = _requests.get(request_id)
            if req:
                self._json(200, {'ok': True, 'request': asdict(req)})
            else:
                self._json(404, {'error': f'request {request_id!r} not found'})
        else:
            self._json(404, {'error': 'not found'})

    def do_POST(self) -> None:
        path = self.path.split('?')[0].rstrip('/')

        if path == '/requests':
            try:
                data = json.loads(self._body())
            except Exception:
                self._json(400, {'error': 'invalid JSON'})
                return
            result = create_request(data)
            self._json(201 if result['ok'] else 400, result)

        elif path.startswith('/requests/') and path.endswith('/approve'):
            request_id = path[len('/requests/'):-len('/approve')]
            try:
                data = json.loads(self._body()) if self.headers.get('Content-Length', '0') != '0' else {}
            except Exception:
                data = {}
            result = decide(request_id, Decision.APPROVED,
                            data.get('decided_by', ''), data.get('reason', ''))
            self._json(200 if result['ok'] else 400, result)

        elif path.startswith('/requests/') and path.endswith('/deny'):
            request_id = path[len('/requests/'):-len('/deny')]
            try:
                data = json.loads(self._body()) if self.headers.get('Content-Length', '0') != '0' else {}
            except Exception:
                data = {}
            result = decide(request_id, Decision.DENIED,
                            data.get('decided_by', ''), data.get('reason', ''))
            self._json(200 if result['ok'] else 400, result)

        else:
            self._json(404, {'error': 'not found'})

    def log_message(self, *_args: Any) -> None:
        pass


# ── NATS handler ──────────────────────────────────────────────────────────────

async def handle_event(nc, subject: str, raw: bytes) -> None:
    try:
        data = json.loads(raw)
    except Exception:
        log.error('Invalid JSON on %s', subject)
        return

    if subject == 'cascadia.approvals.request':
        # Inbound approval request from a connector
        result = create_request({
            'connector': data.get('connector', 'unknown'),
            'description': data.get('description', ''),
            'action': data.get('action', {}),
            'reply_subject': data.get('reply_subject',
                                      f'cascadia.connectors.{data.get("connector", "unknown")}.approved'),
            'timeout_seconds': data.get('timeout_seconds', DEFAULT_TIMEOUT_SECONDS),
        })
        await nc.publish(
            'cascadia.approvals.queued',
            json.dumps({**result, 'connector': NAME,
                        'timestamp': datetime.now(timezone.utc).isoformat()}).encode(),
        )

    elif subject == 'cascadia.approvals.decide':
        request_id = data.get('request_id', '')
        decision = data.get('decision', '')
        result = decide(request_id, decision,
                        data.get('decided_by', ''), data.get('reason', ''))
        await nc.publish(
            'cascadia.approvals.outcome',
            json.dumps({**result, 'connector': NAME,
                        'timestamp': datetime.now(timezone.utc).isoformat()}).encode(),
        )


# ── Entry point ───────────────────────────────────────────────────────────────

def _start_http_server() -> None:
    server = http.server.HTTPServer(('', PORT), _ApprovalHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info('%s v%s HTTP on port %s', NAME, VERSION, PORT)


async def main() -> None:
    global _nc
    _start_http_server()
    threading.Thread(target=_timeout_watcher, daemon=True).start()

    if not _NATS_AVAILABLE:
        log.warning('nats-py not installed — running in HTTP-only mode')
        await asyncio.sleep(float('inf'))
        return

    _nc = await nats.connect('nats://localhost:4222')
    await _nc.subscribe(
        'cascadia.approvals.>',
        cb=lambda m: asyncio.create_task(handle_event(_nc, m.subject, m.data)),
    )
    log.info('%s connected to NATS, listening on cascadia.approvals.>', NAME)
    await asyncio.sleep(float('inf'))


if __name__ == '__main__':
    asyncio.run(main())
