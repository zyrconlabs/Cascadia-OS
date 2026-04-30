"""
cascadia/connectors/webhook/connector.py — CON-109
Webhook Broker Connector · Zyrcon Labs · v1.0.0

Owns: inbound HTTP webhook reception, HMAC signature validation,
      payload routing to Cascadia NATS subjects.
Does not own: outbound HTTP calls (REST connector), approval decisions.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import http.server
import json
import logging
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    import nats
    _NATS_AVAILABLE = True
except ImportError:
    _NATS_AVAILABLE = False

NAME = "webhook-broker"
VERSION = "1.0.0"
PORT = 9981

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [webhook-broker] %(message)s',
)
log = logging.getLogger(NAME)

# Registered webhook sources: source_id → {secret, target_subject, sig_header, sig_prefix}
# In production this registry is loaded from Vault / NATS KV at startup.
_SOURCES: Dict[str, Dict[str, Any]] = {}

_start_time = time.time()

# Shared NATS connection injected at startup
_nc: Any = None


# ── Signature validation ─────────────────────────────────────────────────────

def _verify_hmac(secret: str, body: bytes, signature: str, prefix: str = 'sha256=') -> bool:
    """Return True if the HMAC-SHA256 signature matches the body."""
    expected = prefix + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def validate_signature(source_cfg: Dict[str, Any], body: bytes, headers: Dict[str, str]) -> bool:
    """
    Validate inbound webhook signature.
    Returns True when no secret is configured (open webhook) or signature matches.
    """
    secret = source_cfg.get('secret', '')
    if not secret:
        return True
    sig_header = source_cfg.get('sig_header', 'X-Hub-Signature-256')
    prefix = source_cfg.get('sig_prefix', 'sha256=')
    signature = headers.get(sig_header, headers.get(sig_header.lower(), ''))
    if not signature:
        return False
    return _verify_hmac(secret, body, signature, prefix)


# ── Routing ──────────────────────────────────────────────────────────────────

def route_event(source_id: str, event_type: str, payload: Dict[str, Any]) -> str:
    """
    Determine NATS subject for an inbound webhook event.
    Falls back to cascadia.connectors.webhook-broker.unrouted if no source config.
    """
    cfg = _SOURCES.get(source_id, {})
    base = cfg.get('target_subject', f'cascadia.connectors.{NAME}.events')
    return f'{base}.{event_type}' if event_type else base


def build_envelope(source_id: str, event_type: str, body: bytes,
                   headers: Dict[str, str]) -> Dict[str, Any]:
    """Wrap raw webhook payload in a Cascadia envelope."""
    try:
        data = json.loads(body)
    except Exception:
        data = body.decode(errors='replace')

    return {
        'connector': NAME,
        'source': source_id,
        'event_type': event_type,
        'data': data,
        'headers': {k: v for k, v in headers.items()
                    if k.lower().startswith('x-') or k.lower() == 'content-type'},
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }


# ── HTTP inbound server ───────────────────────────────────────────────────────

class _WebhookHandler(http.server.BaseHTTPRequestHandler):
    """
    Handles POST /webhook/{source_id}[/{event_type}]
    GET  /health
    POST /sources  (register a new webhook source at runtime)
    """

    def _json_response(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self) -> bytes:
        length = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(length) if length else b''

    def do_GET(self) -> None:
        if self.path == '/health':
            self._json_response(200, {
                'status': 'healthy',
                'connector': NAME,
                'version': VERSION,
                'port': PORT,
                'sources_registered': len(_SOURCES),
                'uptime_seconds': round(time.time() - _start_time),
            })
        else:
            self._json_response(404, {'error': 'not found'})

    def do_POST(self) -> None:
        path = self.path.split('?')[0].rstrip('/')

        # Source registration: POST /sources
        if path == '/sources':
            self._handle_register()
            return

        # Webhook ingest: POST /webhook/{source_id} or /webhook/{source_id}/{event_type}
        parts = [p for p in path.split('/') if p]
        if not parts or parts[0] != 'webhook' or len(parts) < 2:
            self._json_response(400, {'error': 'path must be /webhook/{source_id}[/{event_type}]'})
            return

        source_id = parts[1]
        event_type = parts[2] if len(parts) > 2 else 'event'
        body = self._read_body()
        headers = {k: v for k, v in self.headers.items()}

        source_cfg = _SOURCES.get(source_id, {})
        if not validate_signature(source_cfg, body, headers):
            log.warning('Signature validation failed for source=%s', source_id)
            self._json_response(401, {'error': 'invalid signature'})
            return

        envelope = build_envelope(source_id, event_type, body, headers)
        subject = route_event(source_id, event_type, envelope)

        if _nc is not None:
            asyncio.run_coroutine_threadsafe(
                _nc.publish(subject, json.dumps(envelope).encode()),
                _get_event_loop(),
            )
            log.info('Routed %s/%s → %s', source_id, event_type, subject)
            self._json_response(200, {'ok': True, 'subject': subject})
        else:
            # No NATS — store for polling or drop
            log.warning('NATS unavailable — webhook from %s dropped', source_id)
            self._json_response(200, {'ok': True, 'subject': subject, 'queued': False})

    def _handle_register(self) -> None:
        body = self._read_body()
        try:
            cfg = json.loads(body)
        except Exception:
            self._json_response(400, {'error': 'invalid JSON'})
            return

        source_id = cfg.get('source_id', '')
        if not source_id:
            self._json_response(400, {'error': 'source_id required'})
            return

        _SOURCES[source_id] = {
            'secret': cfg.get('secret', ''),
            'target_subject': cfg.get('target_subject', f'cascadia.connectors.{NAME}.events'),
            'sig_header': cfg.get('sig_header', 'X-Hub-Signature-256'),
            'sig_prefix': cfg.get('sig_prefix', 'sha256='),
        }
        log.info('Registered webhook source: %s', source_id)
        self._json_response(201, {'ok': True, 'source_id': source_id})

    def log_message(self, *_args: Any) -> None:
        pass


# ── Event loop reference for thread-safe NATS publish ────────────────────────

_loop: Optional[asyncio.AbstractEventLoop] = None


def _get_event_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None:
        _loop = asyncio.new_event_loop()
        threading.Thread(target=_loop.run_forever, daemon=True).start()
    return _loop


# ── NATS handler (operator-initiated webhook registration) ───────────────────

async def handle_event(nc, subject: str, raw: bytes) -> None:
    """
    Handle NATS control messages:
      cascadia.connectors.webhook-broker.register  — register a new source
      cascadia.connectors.webhook-broker.deregister — remove a source
    """
    try:
        data = json.loads(raw)
    except Exception:
        log.error('Invalid JSON on %s', subject)
        return

    if subject.endswith('.register'):
        source_id = data.get('source_id', '')
        if source_id:
            _SOURCES[source_id] = {
                'secret': data.get('secret', ''),
                'target_subject': data.get('target_subject',
                                           f'cascadia.connectors.{NAME}.events'),
                'sig_header': data.get('sig_header', 'X-Hub-Signature-256'),
                'sig_prefix': data.get('sig_prefix', 'sha256='),
            }
            log.info('NATS-registered source: %s', source_id)
            await nc.publish(
                f'cascadia.connectors.{NAME}.registered',
                json.dumps({'ok': True, 'source_id': source_id,
                            'connector': NAME,
                            'timestamp': datetime.now(timezone.utc).isoformat()}).encode(),
            )

    elif subject.endswith('.deregister'):
        source_id = data.get('source_id', '')
        removed = _SOURCES.pop(source_id, None) is not None
        log.info('Deregistered source %s (found=%s)', source_id, removed)
        await nc.publish(
            f'cascadia.connectors.{NAME}.deregistered',
            json.dumps({'ok': removed, 'source_id': source_id,
                        'connector': NAME,
                        'timestamp': datetime.now(timezone.utc).isoformat()}).encode(),
        )


# ── Entry point ───────────────────────────────────────────────────────────────

def _start_http_server() -> None:
    server = http.server.HTTPServer(('', PORT), _WebhookHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info('%s v%s listening on port %s', NAME, VERSION, PORT)


async def main() -> None:
    global _nc
    _start_http_server()

    if not _NATS_AVAILABLE:
        log.warning('nats-py not installed — running in HTTP-only mode (NATS routing disabled)')
        await asyncio.sleep(float('inf'))
        return

    _nc = await nats.connect('nats://localhost:4222')
    await _nc.subscribe(
        f'cascadia.connectors.{NAME}.>',
        cb=lambda m: asyncio.create_task(handle_event(_nc, m.subject, m.data)),
    )
    log.info('%s connected to NATS, routing inbound webhooks', NAME)
    await asyncio.sleep(float('inf'))


if __name__ == '__main__':
    asyncio.run(main())
