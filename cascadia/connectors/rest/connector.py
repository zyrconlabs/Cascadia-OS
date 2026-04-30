"""
cascadia/connectors/rest/connector.py — CON-108
REST / OpenAPI Connector · Zyrcon Labs · v1.0.0

Owns: authenticated HTTP calls to external REST APIs, retry logic,
      response normalization to Cascadia envelope.
Does not own: workflow routing, approval decisions, credential storage (Vault does that).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import http.server
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    import nats
    _NATS_AVAILABLE = True
except ImportError:
    _NATS_AVAILABLE = False

NAME = "rest-connector"
VERSION = "1.0.0"
PORT = 9980

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [rest-connector] %(message)s',
)
log = logging.getLogger(NAME)

# Write methods that require an approval gate before execution
_WRITE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}

# Max retries and backoff for transient failures
_MAX_RETRIES = 3
_RETRY_STATUSES = {429, 500, 502, 503, 504}


# ── Auth helpers ─────────────────────────────────────────────────────────────

def _apply_auth(headers: Dict[str, str], params: Dict[str, str],
                auth_type: str, credentials: Dict[str, Any]) -> None:
    """Mutate headers/params in-place to add the appropriate auth."""
    if auth_type == 'bearer':
        token = credentials.get('token', '')
        headers['Authorization'] = f'Bearer {token}'

    elif auth_type == 'api_key':
        key = credentials.get('key', '')
        location = credentials.get('location', 'header')  # 'header' or 'query'
        name = credentials.get('name', 'X-API-Key')
        if location == 'query':
            params[name] = key
        else:
            headers[name] = key

    elif auth_type == 'basic':
        user = credentials.get('username', '')
        pwd = credentials.get('password', '')
        encoded = base64.b64encode(f'{user}:{pwd}'.encode()).decode()
        headers['Authorization'] = f'Basic {encoded}'

    elif auth_type == 'hmac':
        secret = credentials.get('secret', '').encode()
        body_str = credentials.get('_body', '')
        sig = hmac.new(secret, body_str.encode(), hashlib.sha256).hexdigest()
        header_name = credentials.get('header', 'X-Signature')
        headers[header_name] = f'sha256={sig}'


# ── Core HTTP caller ─────────────────────────────────────────────────────────

def _make_request(
    method: str,
    url: str,
    headers: Dict[str, str],
    body: Optional[bytes],
    timeout: float = 30.0,
    retries: int = _MAX_RETRIES,
) -> Dict[str, Any]:
    """
    Execute one HTTP request with retry on transient errors.
    Returns normalized Cascadia response envelope.
    """
    last_error: Optional[str] = None

    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                resp_headers = dict(resp.headers)
                raw = resp.read()
                try:
                    data = json.loads(raw)
                except Exception:
                    data = raw.decode(errors='replace')
                return {
                    'ok': True,
                    'status': status,
                    'headers': resp_headers,
                    'data': data,
                    'connector': NAME,
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                }
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()
            try:
                err_data = json.loads(raw)
            except Exception:
                err_data = raw.decode(errors='replace')

            if status in _RETRY_STATUSES and attempt < retries:
                backoff = 2 ** attempt
                log.warning('HTTP %s — retrying in %ss (attempt %s/%s)', status, backoff, attempt + 1, retries)
                time.sleep(backoff)
                last_error = f'HTTP {status}'
                continue

            return {
                'ok': False,
                'status': status,
                'error': f'HTTP {status}',
                'data': err_data,
                'connector': NAME,
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }
        except urllib.error.URLError as exc:
            last_error = str(exc.reason)
            if attempt < retries:
                backoff = 2 ** attempt
                log.warning('URLError: %s — retrying in %ss', last_error, backoff)
                time.sleep(backoff)
                continue
            return {
                'ok': False,
                'status': 0,
                'error': f'Connection error: {last_error}',
                'connector': NAME,
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            return {
                'ok': False,
                'status': 0,
                'error': str(exc),
                'connector': NAME,
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }

    return {
        'ok': False,
        'status': 0,
        'error': f'All retries exhausted: {last_error}',
        'connector': NAME,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }


# ── Call dispatcher ──────────────────────────────────────────────────────────

def execute_call(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute an API call from a structured payload.

    Expected payload keys:
      method       — GET, POST, PUT, PATCH, DELETE (required)
      url          — full URL (required)
      headers      — dict of extra headers (optional)
      params       — dict of query params (optional)
      body         — dict or string body (optional)
      auth_type    — bearer | api_key | basic | hmac | none (optional)
      credentials  — dict with auth credentials (optional)
      timeout      — seconds (optional, default 30)
      retries      — int (optional, default 3)
    """
    method = payload.get('method', 'GET').upper()
    url = payload.get('url', '')
    if not url:
        return {'ok': False, 'error': 'url is required', 'status': 0}

    headers: Dict[str, str] = {'Content-Type': 'application/json', **(payload.get('headers') or {})}
    params: Dict[str, str] = dict(payload.get('params') or {})
    auth_type = payload.get('auth_type', 'none')
    credentials = dict(payload.get('credentials') or {})
    timeout = float(payload.get('timeout', 30))
    retries = int(payload.get('retries', _MAX_RETRIES))

    # Serialize body
    body_raw: Optional[bytes] = None
    body_in = payload.get('body')
    if body_in is not None:
        if isinstance(body_in, (dict, list)):
            body_str = json.dumps(body_in)
        else:
            body_str = str(body_in)
        body_raw = body_str.encode()
        if auth_type == 'hmac':
            credentials['_body'] = body_str

    # Apply auth
    if auth_type != 'none':
        _apply_auth(headers, params, auth_type, credentials)

    # Append query params to URL
    if params:
        url = url + ('&' if '?' in url else '?') + urllib.parse.urlencode(params)

    return _make_request(method, url, headers, body_raw, timeout, retries)


# ── NATS handler ──────────────────────────────────────────────────────────────

async def _publish_event(nc, event_type: str, data: dict) -> None:
    payload = json.dumps({
        'connector': NAME, 'event': event_type,
        'data': data, 'timestamp': datetime.now(timezone.utc).isoformat(),
    }).encode()
    await nc.publish(f'cascadia.connectors.{NAME}.{event_type}', payload)


async def _request_approval(nc, description: str, call_payload: dict) -> None:
    payload = json.dumps({
        'connector': NAME, 'description': description,
        'action': call_payload,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }).encode()
    await nc.publish('cascadia.approvals.request', payload)
    log.info('Approval requested: %s', description)


async def handle_event(nc, subject: str, raw: bytes) -> None:
    try:
        data = json.loads(raw)
    except Exception:
        log.error('Invalid JSON on %s', subject)
        return

    method = data.get('method', 'GET').upper()

    # Write operations require approval gate before execution
    if method in _WRITE_METHODS:
        url = data.get('url', '')
        await _request_approval(
            nc,
            f'{method} {url} — approval required before REST write',
            data,
        )
        # In a full implementation the approval loop resumes here.
        # For now we log and return — the approval listener will re-publish
        # a `cascadia.connectors.rest.approved` event to trigger execution.
        log.info('Write operation queued for approval: %s %s', method, url)
        return

    result = execute_call(data)
    await _publish_event(nc, 'response', {'request': data, 'response': result})
    log.info('%s %s → %s', method, data.get('url', '?'), result.get('status'))


# ── Health HTTP server ────────────────────────────────────────────────────────

_start_time = time.time()


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps({
            'status': 'healthy',
            'connector': NAME,
            'version': VERSION,
            'port': PORT,
            'uptime_seconds': round(time.time() - _start_time),
        }).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        pass


def _start_health_server() -> None:
    server = http.server.HTTPServer(('', PORT), _HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info('%s v%s health endpoint on port %s', NAME, VERSION, PORT)


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    _start_health_server()

    if not _NATS_AVAILABLE:
        log.warning('nats-py not installed — running in HTTP-only mode')
        await asyncio.sleep(float('inf'))
        return

    nc = await nats.connect('nats://localhost:4222')
    await nc.subscribe(
        f'cascadia.connectors.{NAME}.>',
        cb=lambda m: asyncio.create_task(handle_event(nc, m.subject, m.data)),
    )
    log.info('%s v%s connected to NATS, listening on cascadia.connectors.%s.>', NAME, VERSION, NAME)
    await asyncio.sleep(float('inf'))


if __name__ == '__main__':
    asyncio.run(main())
