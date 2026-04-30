"""Tests for CON-116 Approval Gate Connector."""
from __future__ import annotations

import asyncio
import json
import time
import threading
import urllib.request
import urllib.error
from http.server import HTTPServer
from unittest.mock import AsyncMock, MagicMock

import pytest

from cascadia.connectors.approval.connector import (
    NAME,
    VERSION,
    PORT,
    DEFAULT_TIMEOUT_SECONDS,
    Decision,
    ApprovalRequest,
    _requests,
    _requests_lock,
    create_request,
    decide,
    _pending_requests,
    _expire_timed_out,
    _build_outcome,
    handle_event,
    _ApprovalHandler,
)


def _clear():
    with _requests_lock:
        _requests.clear()


def _make_req(**kwargs) -> Dict:
    defaults = {
        'connector': 'test-connector',
        'description': 'do something risky',
        'action': {'method': 'POST', 'url': 'https://example.com'},
        'reply_subject': 'cascadia.test.approved',
        'timeout_seconds': 3600,
    }
    defaults.update(kwargs)
    return create_request(defaults)


# ── create_request ────────────────────────────────────────────────────────────

def test_create_request_success():
    _clear()
    result = _make_req()
    assert result['ok'] is True
    assert 'request_id' in result
    assert result['status'] == Decision.PENDING
    _clear()


def test_create_request_custom_id():
    _clear()
    result = _make_req(request_id='custom-req-1')
    assert result['request_id'] == 'custom-req-1'
    _clear()


def test_create_request_missing_connector():
    result = create_request({'description': 'x', 'action': {}, 'reply_subject': 'y'})
    assert result['ok'] is False
    assert 'connector' in result['error']


def test_create_request_missing_description():
    result = create_request({'connector': 'x', 'action': {}, 'reply_subject': 'y'})
    assert result['ok'] is False
    assert 'description' in result['error']


def test_create_request_missing_reply_subject():
    result = create_request({'connector': 'x', 'description': 'y', 'action': {}})
    assert result['ok'] is False
    assert 'reply_subject' in result['error']


# ── decide ────────────────────────────────────────────────────────────────────

def test_approve_request():
    _clear()
    r = _make_req()
    result = decide(r['request_id'], Decision.APPROVED, 'andy', 'looks good')
    assert result['ok'] is True
    assert result['decision'] == Decision.APPROVED

    with _requests_lock:
        req = _requests[r['request_id']]
    assert req.decided_by == 'andy'
    assert req.reason == 'looks good'
    _clear()


def test_deny_request():
    _clear()
    r = _make_req()
    result = decide(r['request_id'], Decision.DENIED, 'andy', 'not today')
    assert result['ok'] is True
    assert result['decision'] == Decision.DENIED
    _clear()


def test_decide_invalid_decision():
    _clear()
    r = _make_req()
    result = decide(r['request_id'], 'maybe')
    assert result['ok'] is False
    assert 'approved or denied' in result['error']
    _clear()


def test_decide_nonexistent_request():
    result = decide('no-such-id', Decision.APPROVED)
    assert result['ok'] is False
    assert 'not found' in result['error']


def test_decide_already_decided():
    _clear()
    r = _make_req()
    decide(r['request_id'], Decision.APPROVED)
    result = decide(r['request_id'], Decision.DENIED)
    assert result['ok'] is False
    assert 'already' in result['error']
    _clear()


def test_decide_expired_request():
    _clear()
    r = _make_req(timeout_seconds=0)  # instant timeout
    time.sleep(0.01)
    result = decide(r['request_id'], Decision.APPROVED)
    assert result['ok'] is False
    assert 'expired' in result['error']
    _clear()


# ── pending / expire ──────────────────────────────────────────────────────────

def test_pending_requests_excludes_decided():
    _clear()
    r1 = _make_req()
    r2 = _make_req()
    decide(r1['request_id'], Decision.APPROVED)

    pending = _pending_requests()
    ids = {r.request_id for r in pending}
    assert r2['request_id'] in ids
    assert r1['request_id'] not in ids
    _clear()


def test_expire_timed_out():
    _clear()
    r = _make_req(timeout_seconds=0)
    time.sleep(0.01)
    _expire_timed_out()

    with _requests_lock:
        req = _requests[r['request_id']]
    assert req.decision == Decision.TIMED_OUT
    _clear()


def test_expire_does_not_touch_decided():
    _clear()
    r = _make_req(timeout_seconds=0)
    time.sleep(0.01)
    # Manually mark approved before watcher runs
    with _requests_lock:
        _requests[r['request_id']].decision = Decision.APPROVED
    _expire_timed_out()
    with _requests_lock:
        req = _requests[r['request_id']]
    assert req.decision == Decision.APPROVED
    _clear()


# ── outcome envelope ──────────────────────────────────────────────────────────

def test_outcome_approved_includes_action():
    _clear()
    r = _make_req()
    with _requests_lock:
        req = _requests[r['request_id']]
    req.decision = Decision.APPROVED
    outcome = _build_outcome(req)
    assert outcome['decision'] == Decision.APPROVED
    assert outcome['action'] is not None
    _clear()


def test_outcome_denied_nulls_action():
    _clear()
    r = _make_req()
    with _requests_lock:
        req = _requests[r['request_id']]
    req.decision = Decision.DENIED
    outcome = _build_outcome(req)
    assert outcome['decision'] == Decision.DENIED
    assert outcome['action'] is None
    _clear()


# ── HTTP server ───────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def approval_server():
    server = HTTPServer(('127.0.0.1', 0), _ApprovalHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{port}'
    server.shutdown()


def _post(url, body=None):
    raw = json.dumps(body or {}).encode()
    req = urllib.request.Request(url, data=raw, method='POST',
                                 headers={'Content-Type': 'application/json',
                                          'Content-Length': str(len(raw))})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _get(url):
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http_health(approval_server):
    status, body = _get(f'{approval_server}/health')
    assert status == 200
    assert body['connector'] == NAME


def test_http_create_request(approval_server):
    _clear()
    status, body = _post(f'{approval_server}/requests', {
        'connector': 'rest-connector',
        'description': 'POST to CRM',
        'action': {'method': 'POST'},
        'reply_subject': 'cascadia.test',
    })
    assert status == 201
    assert body['ok'] is True
    _clear()


def test_http_create_request_bad_request(approval_server):
    status, body = _post(f'{approval_server}/requests', {'connector': 'x'})
    assert status == 400
    assert body['ok'] is False


def test_http_list_requests(approval_server):
    _clear()
    _make_req()
    status, body = _get(f'{approval_server}/requests')
    assert status == 200
    assert len(body['requests']) >= 1
    _clear()


def test_http_pending_requests(approval_server):
    _clear()
    r = _make_req()
    decide(r['request_id'], Decision.APPROVED)
    _make_req()
    status, body = _get(f'{approval_server}/requests/pending')
    assert status == 200
    assert len(body['requests']) == 1
    _clear()


def test_http_get_request(approval_server):
    _clear()
    r = _make_req()
    status, body = _get(f'{approval_server}/requests/{r["request_id"]}')
    assert status == 200
    assert body['request']['connector'] == 'test-connector'
    _clear()


def test_http_get_request_not_found(approval_server):
    status, body = _get(f'{approval_server}/requests/no-such-id')
    assert status == 404


def test_http_approve(approval_server):
    _clear()
    r = _make_req()
    status, body = _post(
        f'{approval_server}/requests/{r["request_id"]}/approve',
        {'decided_by': 'andy', 'reason': 'ok'},
    )
    assert status == 200
    assert body['ok'] is True
    _clear()


def test_http_deny(approval_server):
    _clear()
    r = _make_req()
    status, body = _post(
        f'{approval_server}/requests/{r["request_id"]}/deny',
        {'decided_by': 'andy', 'reason': 'nope'},
    )
    assert status == 200
    assert body['ok'] is True
    _clear()


def test_http_approve_nonexistent(approval_server):
    status, body = _post(f'{approval_server}/requests/no-such/approve', {})
    assert status == 400
    assert body['ok'] is False


# ── NATS handler ──────────────────────────────────────────────────────────────

def test_nats_request():
    _clear()
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append((subject, json.loads(payload)))

    nc.publish = mock_publish

    payload = json.dumps({
        'connector': 'rest-connector',
        'description': 'POST to CRM — approval required',
        'action': {'method': 'POST'},
        'reply_subject': 'cascadia.test.approved',
    }).encode()
    asyncio.run(handle_event(nc, 'cascadia.approvals.request', payload))

    assert any('queued' in s for s, _ in published)
    _clear()


def test_nats_decide():
    _clear()
    r = _make_req()
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append((subject, json.loads(payload)))

    nc.publish = mock_publish

    payload = json.dumps({
        'request_id': r['request_id'],
        'decision': 'approved',
        'decided_by': 'system',
    }).encode()
    asyncio.run(handle_event(nc, 'cascadia.approvals.decide', payload))

    assert any('outcome' in s for s, _ in published)
    _clear()


def test_nats_invalid_json():
    nc = MagicMock()
    nc.publish = AsyncMock()
    asyncio.run(handle_event(nc, 'cascadia.approvals.request', b'not json'))
    nc.publish.assert_not_called()


# ── Metadata ──────────────────────────────────────────────────────────────────

def test_connector_metadata():
    assert NAME == 'approval-gate'
    assert VERSION == '1.0.0'
    assert PORT == 9988
    assert DEFAULT_TIMEOUT_SECONDS == 3600
