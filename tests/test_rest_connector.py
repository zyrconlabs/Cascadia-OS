"""Tests for CON-108 REST / OpenAPI Connector."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
import pytest

from cascadia.connectors.rest.connector import (
    _apply_auth,
    execute_call,
    _make_request,
    _WRITE_METHODS,
    NAME,
    VERSION,
)


# ── Tiny local HTTP server for integration-style tests ───────────────────────

class _EchoHandler(BaseHTTPRequestHandler):
    """Returns a JSON echo of the request."""
    def _respond(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._respond(200, {'method': 'GET', 'path': self.path})

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = body.decode()
        self._respond(201, {'method': 'POST', 'path': self.path, 'body': parsed})

    def do_PUT(self):
        self._respond(200, {'method': 'PUT', 'path': self.path})

    def do_DELETE(self):
        self._respond(200, {'method': 'DELETE', 'path': self.path})

    def log_message(self, *_): pass


@pytest.fixture(scope='module')
def echo_server():
    """Start a real HTTP echo server on a free port."""
    server = HTTPServer(('127.0.0.1', 0), _EchoHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{port}'
    server.shutdown()


# ── Auth helpers ─────────────────────────────────────────────────────────────

def test_apply_bearer_auth():
    headers, params = {}, {}
    _apply_auth(headers, params, 'bearer', {'token': 'tok_abc'})
    assert headers['Authorization'] == 'Bearer tok_abc'
    assert params == {}


def test_apply_api_key_header():
    headers, params = {}, {}
    _apply_auth(headers, params, 'api_key', {'key': 'mykey', 'name': 'X-API-Key', 'location': 'header'})
    assert headers['X-API-Key'] == 'mykey'
    assert params == {}


def test_apply_api_key_query():
    headers, params = {}, {}
    _apply_auth(headers, params, 'api_key', {'key': 'mykey', 'name': 'api_key', 'location': 'query'})
    assert params['api_key'] == 'mykey'
    assert 'X-API-Key' not in headers


def test_apply_basic_auth():
    headers, params = {}, {}
    _apply_auth(headers, params, 'basic', {'username': 'user', 'password': 'pass'})
    expected = 'Basic ' + base64.b64encode(b'user:pass').decode()
    assert headers['Authorization'] == expected


def test_apply_hmac_auth():
    headers, params = {}, {}
    secret = b'mysecret'
    body = '{"amount":100}'
    creds = {'secret': 'mysecret', 'header': 'X-Signature', '_body': body}
    _apply_auth(headers, params, 'hmac', creds)
    expected_sig = 'sha256=' + hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    assert headers['X-Signature'] == expected_sig


def test_auth_none_changes_nothing():
    headers, params = {'existing': 'value'}, {}
    _apply_auth(headers, params, 'none', {})
    assert headers == {'existing': 'value'}
    assert params == {}


# ── execute_call — missing URL ────────────────────────────────────────────────

def test_execute_call_missing_url():
    result = execute_call({'method': 'GET'})
    assert result['ok'] is False
    assert 'url' in result['error']


# ── execute_call — real GET ───────────────────────────────────────────────────

def test_execute_call_get(echo_server):
    result = execute_call({'method': 'GET', 'url': f'{echo_server}/ping'})
    assert result['ok'] is True
    assert result['status'] == 200
    assert result['data']['method'] == 'GET'
    assert result['connector'] == NAME
    assert 'timestamp' in result


def test_execute_call_post_with_body(echo_server):
    result = execute_call({
        'method': 'POST',
        'url': f'{echo_server}/contacts',
        'body': {'name': 'Acme', 'email': 'acme@test.com'},
    })
    assert result['ok'] is True
    assert result['status'] == 201
    assert result['data']['body']['name'] == 'Acme'


def test_execute_call_with_query_params(echo_server):
    result = execute_call({
        'method': 'GET',
        'url': f'{echo_server}/search',
        'params': {'q': 'test', 'page': '1'},
    })
    assert result['ok'] is True
    assert 'q=test' in result['data']['path']


def test_execute_call_with_bearer_auth(echo_server):
    result = execute_call({
        'method': 'GET',
        'url': f'{echo_server}/me',
        'auth_type': 'bearer',
        'credentials': {'token': 'tok_xyz'},
    })
    assert result['ok'] is True


def test_execute_call_delete(echo_server):
    result = execute_call({
        'method': 'DELETE',
        'url': f'{echo_server}/items/1',
    })
    assert result['ok'] is True
    assert result['status'] == 200


# ── Approval gate — write methods require it ──────────────────────────────────

def test_write_methods_set():
    assert 'POST' in _WRITE_METHODS
    assert 'PUT' in _WRITE_METHODS
    assert 'PATCH' in _WRITE_METHODS
    assert 'DELETE' in _WRITE_METHODS
    assert 'GET' not in _WRITE_METHODS


# ── Error handling ────────────────────────────────────────────────────────────

def test_connection_refused_returns_error():
    result = execute_call({'method': 'GET', 'url': 'http://127.0.0.1:19999/nope'})
    assert result['ok'] is False
    assert result['status'] == 0
    assert 'error' in result


def test_http_404_returns_error(echo_server):
    # Echo server returns 200 for anything — patch urllib to simulate 404
    import urllib.error
    with patch('urllib.request.urlopen') as mock_open:
        mock_exc = urllib.error.HTTPError(
            url='http://example.com', code=404,
            msg='Not Found', hdrs={}, fp=None,
        )
        mock_exc.read = lambda: b'{"error":"not found"}'
        mock_open.side_effect = mock_exc
        result = execute_call({'method': 'GET', 'url': 'http://example.com/missing'})
    assert result['ok'] is False
    assert result['status'] == 404


# ── Audit log written ─────────────────────────────────────────────────────────

def test_audit_log_published_on_get():
    """handle_event publishes a response event to NATS after a GET."""
    from cascadia.connectors.rest.connector import handle_event

    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append((subject, json.loads(payload)))

    nc.publish = mock_publish

    payload = json.dumps({'method': 'GET', 'url': 'http://127.0.0.1:19999/x'}).encode()
    asyncio.run(handle_event(nc, 'cascadia.connectors.rest.call', payload))

    assert any('response' in s for s, _ in published)


def test_approval_gate_triggered_for_post():
    """handle_event must publish to cascadia.approvals.request for POST."""
    from cascadia.connectors.rest.connector import handle_event

    nc = MagicMock()
    approval_calls = []

    async def mock_publish(subject, payload):
        if 'approvals' in subject:
            approval_calls.append(json.loads(payload))

    nc.publish = mock_publish

    payload = json.dumps({
        'method': 'POST',
        'url': 'https://api.example.com/leads',
        'body': {'name': 'Test'},
    }).encode()
    asyncio.run(handle_event(nc, 'cascadia.connectors.rest.call', payload))

    assert len(approval_calls) == 1
    assert approval_calls[0]['connector'] == NAME
    assert 'approval required' in approval_calls[0]['description'].lower()


def test_write_blocked_without_approval():
    """POST must not reach the external API — approval gate stops it first."""
    from cascadia.connectors.rest.connector import handle_event

    nc = MagicMock()
    nc.publish = AsyncMock()

    with patch('cascadia.connectors.rest.connector.execute_call') as mock_exec:
        payload = json.dumps({'method': 'POST', 'url': 'https://api.example.com/x'}).encode()
        asyncio.run(handle_event(nc, 'cascadia.connectors.rest.call', payload))
        mock_exec.assert_not_called()


# ── Health check ──────────────────────────────────────────────────────────────

def test_health_check_format():
    from cascadia.connectors.rest.connector import _HealthHandler, _start_time
    import io

    class MockRequest:
        def makefile(self, mode, bufsize=None):
            return io.BytesIO(b'GET /health HTTP/1.0\r\n\r\n')

    # Just verify the handler class is importable and has the right shape
    assert hasattr(_HealthHandler, 'do_GET')


def test_connector_metadata():
    assert NAME == 'rest-connector'
    assert VERSION == '1.0.0'
