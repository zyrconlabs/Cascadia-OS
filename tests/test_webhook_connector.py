"""Tests for CON-109 Webhook Broker Connector."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import threading
import urllib.request
import urllib.error
from http.server import HTTPServer
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cascadia.connectors.webhook.connector import (
    NAME,
    VERSION,
    PORT,
    _SOURCES,
    _verify_hmac,
    validate_signature,
    route_event,
    build_envelope,
    handle_event,
    _WebhookHandler,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sig(secret: str, body: bytes, prefix: str = 'sha256=') -> str:
    return prefix + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ── HMAC verification ─────────────────────────────────────────────────────────

def test_verify_hmac_correct():
    body = b'{"event":"push"}'
    sig = _sig('secret123', body)
    assert _verify_hmac('secret123', body, sig) is True


def test_verify_hmac_wrong_secret():
    body = b'{"event":"push"}'
    sig = _sig('right_secret', body)
    assert _verify_hmac('wrong_secret', body, sig) is False


def test_verify_hmac_tampered_body():
    body = b'{"event":"push"}'
    sig = _sig('secret', body)
    assert _verify_hmac('secret', b'tampered', sig) is False


def test_verify_hmac_custom_prefix():
    body = b'payload'
    sig = 'v1=' + hmac.new(b'secret', body, hashlib.sha256).hexdigest()
    assert _verify_hmac('secret', body, sig, prefix='v1=') is True


# ── Signature validation ──────────────────────────────────────────────────────

def test_validate_no_secret_passes():
    assert validate_signature({}, b'anything', {}) is True


def test_validate_with_matching_signature():
    body = b'{"ref":"main"}'
    secret = 'mysecret'
    sig = _sig(secret, body)
    cfg = {'secret': secret, 'sig_header': 'X-Hub-Signature-256', 'sig_prefix': 'sha256='}
    assert validate_signature(cfg, body, {'X-Hub-Signature-256': sig}) is True


def test_validate_missing_header_fails():
    body = b'body'
    cfg = {'secret': 'sec', 'sig_header': 'X-Hub-Signature-256', 'sig_prefix': 'sha256='}
    assert validate_signature(cfg, body, {}) is False


def test_validate_wrong_signature_fails():
    body = b'body'
    cfg = {'secret': 'sec', 'sig_header': 'X-Sig', 'sig_prefix': 'sha256='}
    assert validate_signature(cfg, body, {'X-Sig': 'sha256=deadbeef'}) is False


# ── Routing ───────────────────────────────────────────────────────────────────

def test_route_event_default():
    _SOURCES.clear()
    subject = route_event('unknown_src', 'push', {})
    assert 'webhook-broker' in subject


def test_route_event_custom_target():
    _SOURCES['gh'] = {'target_subject': 'cascadia.events.github'}
    subject = route_event('gh', 'push', {})
    assert subject == 'cascadia.events.github.push'
    _SOURCES.clear()


def test_route_event_empty_event_type():
    _SOURCES['src'] = {'target_subject': 'cascadia.test'}
    subject = route_event('src', '', {})
    assert subject == 'cascadia.test'
    _SOURCES.clear()


# ── Envelope builder ──────────────────────────────────────────────────────────

def test_build_envelope_json_body():
    body = b'{"ref":"main","commits":[]}'
    env = build_envelope('github', 'push', body, {'x-github-event': 'push', 'content-type': 'application/json'})
    assert env['connector'] == NAME
    assert env['source'] == 'github'
    assert env['event_type'] == 'push'
    assert env['data']['ref'] == 'main'
    assert 'timestamp' in env


def test_build_envelope_non_json_body():
    body = b'plain text payload'
    env = build_envelope('stripe', 'charge', body, {})
    assert isinstance(env['data'], str)
    assert env['data'] == 'plain text payload'


def test_build_envelope_filters_headers():
    headers = {
        'x-custom': 'val',
        'content-type': 'application/json',
        'authorization': 'Bearer tok',  # should be excluded
    }
    env = build_envelope('src', 'ev', b'{}', headers)
    assert 'x-custom' in env['headers']
    assert 'content-type' in env['headers']
    assert 'authorization' not in env['headers']


# ── HTTP server integration ───────────────────────────────────────────────────

@pytest.fixture(scope='module')
def webhook_server():
    """Start the webhook HTTP server on a free port."""
    server = HTTPServer(('127.0.0.1', 0), _WebhookHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{port}'
    server.shutdown()


def _post(url: str, body: bytes, headers: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=body, method='POST',
                                 headers=headers or {'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _get(url: str) -> tuple[int, dict]:
    with urllib.request.urlopen(url) as resp:
        return resp.status, json.loads(resp.read())


def test_health_endpoint(webhook_server):
    status, body = _get(f'{webhook_server}/health')
    assert status == 200
    assert body['status'] == 'healthy'
    assert body['connector'] == NAME


def test_register_source(webhook_server):
    _SOURCES.clear()
    payload = json.dumps({
        'source_id': 'test-src',
        'secret': 'abc',
        'target_subject': 'cascadia.test',
    }).encode()
    status, body = _post(f'{webhook_server}/sources', payload)
    assert status == 201
    assert body['ok'] is True
    assert 'test-src' in _SOURCES


def test_register_missing_source_id(webhook_server):
    payload = json.dumps({'secret': 'abc'}).encode()
    status, body = _post(f'{webhook_server}/sources', payload)
    assert status == 400
    assert 'source_id' in body['error']


def test_webhook_open_source(webhook_server):
    _SOURCES['open'] = {'secret': '', 'target_subject': 'cascadia.test', 'sig_header': 'X-Sig', 'sig_prefix': 'sha256='}
    payload = json.dumps({'event': 'ping'}).encode()
    status, body = _post(f'{webhook_server}/webhook/open/ping', payload)
    assert status == 200
    assert body['ok'] is True


def test_webhook_valid_signature(webhook_server):
    secret = 'mysecret'
    body = json.dumps({'event': 'push'}).encode()
    sig = _sig(secret, body)
    _SOURCES['signed-src'] = {
        'secret': secret,
        'target_subject': 'cascadia.events',
        'sig_header': 'X-Hub-Signature-256',
        'sig_prefix': 'sha256=',
    }
    status, resp = _post(
        f'{webhook_server}/webhook/signed-src/push',
        body,
        headers={'Content-Type': 'application/json', 'X-Hub-Signature-256': sig},
    )
    assert status == 200
    assert resp['ok'] is True


def test_webhook_invalid_signature(webhook_server):
    _SOURCES['strict-src'] = {
        'secret': 'correct',
        'target_subject': 'cascadia.events',
        'sig_header': 'X-Hub-Signature-256',
        'sig_prefix': 'sha256=',
    }
    body = b'{"event":"push"}'
    status, resp = _post(
        f'{webhook_server}/webhook/strict-src/push',
        body,
        headers={'Content-Type': 'application/json', 'X-Hub-Signature-256': 'sha256=badhash'},
    )
    assert status == 401
    assert 'signature' in resp['error']


def test_webhook_bad_path(webhook_server):
    status, body = _post(f'{webhook_server}/webhook', b'{}')
    assert status == 400


def test_get_unknown_path(webhook_server):
    try:
        urllib.request.urlopen(f'{webhook_server}/unknown')
    except urllib.error.HTTPError as e:
        assert e.code == 404


# ── NATS handler ──────────────────────────────────────────────────────────────

def test_nats_register_source():
    _SOURCES.clear()
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append((subject, json.loads(payload)))

    nc.publish = mock_publish

    payload = json.dumps({
        'source_id': 'nats-src',
        'secret': 'nats-secret',
        'target_subject': 'cascadia.nats.events',
    }).encode()
    asyncio.run(handle_event(nc, 'cascadia.connectors.webhook-broker.register', payload))

    assert 'nats-src' in _SOURCES
    assert _SOURCES['nats-src']['secret'] == 'nats-secret'
    assert any('registered' in s for s, _ in published)


def test_nats_deregister_source():
    _SOURCES['to-remove'] = {'secret': '', 'target_subject': '', 'sig_header': '', 'sig_prefix': ''}
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append((subject, json.loads(payload)))

    nc.publish = mock_publish

    payload = json.dumps({'source_id': 'to-remove'}).encode()
    asyncio.run(handle_event(nc, 'cascadia.connectors.webhook-broker.deregister', payload))

    assert 'to-remove' not in _SOURCES
    assert any('deregistered' in s for s, _ in published)


def test_nats_invalid_json():
    nc = MagicMock()
    nc.publish = AsyncMock()
    asyncio.run(handle_event(nc, 'cascadia.connectors.webhook-broker.register', b'not json'))
    nc.publish.assert_not_called()


# ── Metadata ──────────────────────────────────────────────────────────────────

def test_connector_metadata():
    assert NAME == 'webhook-broker'
    assert VERSION == '1.0.0'
    assert PORT == 9981
