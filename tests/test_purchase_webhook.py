"""Tests for Task A5 — Purchase → Auto-Install Webhook."""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import HTTPServer
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from cascadia.depot.purchase_webhook import (
    NAME,
    VERSION,
    PORT,
    _STRIPE_TIMESTAMP_TOLERANCE,
    _processed_events,
    _processed_lock,
    verify_stripe_signature,
    parse_purchase_event,
    process_purchase,
    create_server,
    _PurchaseWebhookHandler,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

SECRET = 'whsec_testsecret'

VALID_MANIFEST = {
    'id': 'purchased-op',
    'name': 'Purchased Op',
    'type': 'operator',
    'version': '1.0.0',
    'description': 'For purchase tests',
    'author': 'Zyrcon Labs',
    'price': 29,
    'tier_required': 'pro',
    'port': 8499,
    'entry_point': 'purchased.op',
    'dependencies': [],
    'install_hook': 'install.sh',
    'uninstall_hook': 'uninstall.sh',
    'category': 'sales',
    'industries': ['all'],
    'installed_by_default': False,
    'safe_to_uninstall': True,
}

CHECKOUT_EVENT = {
    'id': 'evt_001',
    'type': 'checkout.session.completed',
    'data': {
        'object': {
            'customer': 'cus_abc',
            'customer_details': {'email': 'andy@example.com'},
            'amount_total': 2900,
            'currency': 'usd',
            'metadata': {
                'operator_id': 'purchased-op',
                'customer_id': 'cus_abc',
                'package_url': '',
            },
        }
    },
}


def _make_sig(payload: bytes, secret: str = SECRET,
              timestamp: Optional[int] = None) -> str:
    ts = timestamp or int(time.time())
    signed = f'{ts}.'.encode() + payload
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f't={ts},v1={sig}'


def _clear_processed():
    with _processed_lock:
        _processed_events.clear()


# ── verify_stripe_signature ───────────────────────────────────────────────────

def test_verify_valid_signature():
    payload = b'{"type":"checkout.session.completed"}'
    sig = _make_sig(payload)
    assert verify_stripe_signature(payload, sig, SECRET) is True


def test_verify_wrong_secret():
    payload = b'body'
    sig = _make_sig(payload, 'right_secret')
    assert verify_stripe_signature(payload, sig, 'wrong_secret') is False


def test_verify_tampered_payload():
    payload = b'original'
    sig = _make_sig(payload)
    assert verify_stripe_signature(b'tampered', sig, SECRET) is False


def test_verify_expired_timestamp():
    payload = b'body'
    old_ts = int(time.time()) - _STRIPE_TIMESTAMP_TOLERANCE - 10
    sig = _make_sig(payload, timestamp=old_ts)
    assert verify_stripe_signature(payload, sig, SECRET) is False


def test_verify_no_secret_accepts_all():
    # Empty secret = dev/test mode — always passes
    assert verify_stripe_signature(b'anything', 'bad-sig', '') is True


def test_verify_malformed_header():
    assert verify_stripe_signature(b'body', 'not-a-stripe-header', SECRET) is False


# ── parse_purchase_event ──────────────────────────────────────────────────────

def test_parse_checkout_completed():
    purchase = parse_purchase_event(CHECKOUT_EVENT)
    assert purchase is not None
    assert purchase['operator_id'] == 'purchased-op'
    assert purchase['customer_id'] == 'cus_abc'
    assert purchase['event_id'] == 'evt_001'


def test_parse_missing_operator_id():
    event = {**CHECKOUT_EVENT, 'data': {'object': {'metadata': {}}}}
    assert parse_purchase_event(event) is None


def test_parse_wrong_event_type():
    event = {**CHECKOUT_EVENT, 'type': 'invoice.paid'}
    assert parse_purchase_event(event) is None


def test_parse_payment_intent_succeeded():
    event = {
        'id': 'evt_002',
        'type': 'payment_intent.succeeded',
        'data': {
            'object': {
                'customer': 'cus_xyz',
                'metadata': {'operator_id': 'some-op'},
            }
        },
    }
    purchase = parse_purchase_event(event)
    assert purchase is not None
    assert purchase['operator_id'] == 'some-op'


def test_parse_extracts_package_url():
    event = {
        'id': 'evt_003',
        'type': 'checkout.session.completed',
        'data': {
            'object': {
                'customer': 'cus_1',
                'metadata': {
                    'operator_id': 'my-op',
                    'package_url': 'https://cdn.example.com/my-op.zip',
                },
            }
        },
    }
    purchase = parse_purchase_event(event)
    assert purchase['package_url'] == 'https://cdn.example.com/my-op.zip'


# ── process_purchase ──────────────────────────────────────────────────────────

def test_process_purchase_success():
    _clear_processed()
    purchase = {
        'event_id': 'evt_proc_1',
        'operator_id': 'purchased-op',
        'customer_id': 'cus_abc',
        'customer_email': 'test@example.com',
        'package_url': '',
    }
    with patch('cascadia.depot.purchase_webhook.install') as mock_install:
        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.health_ok = True
        mock_result.error = None
        mock_result.manifest = VALID_MANIFEST
        mock_install.return_value = mock_result

        outcome = process_purchase(purchase, catalog_lookup=lambda _: VALID_MANIFEST)

    assert outcome['ok'] is True
    assert outcome['install_ok'] is True
    assert outcome['event_id'] == 'evt_proc_1'
    _clear_processed()


def test_process_purchase_install_failure():
    _clear_processed()
    purchase = {
        'event_id': 'evt_proc_fail',
        'operator_id': 'bad-op',
        'customer_id': 'cus_x',
        'customer_email': '',
        'package_url': '',
    }
    with patch('cascadia.depot.purchase_webhook.install') as mock_install:
        mock_result = MagicMock()
        mock_result.ok = False
        mock_result.health_ok = False
        mock_result.error = 'CREW unreachable'
        mock_result.manifest = None
        mock_install.return_value = mock_result

        outcome = process_purchase(purchase)

    assert outcome['ok'] is False
    assert outcome['error'] == 'CREW unreachable'
    _clear_processed()


def test_process_purchase_duplicate_event():
    _clear_processed()
    purchase = {
        'event_id': 'evt_dup',
        'operator_id': 'x',
        'customer_id': 'y',
        'customer_email': '',
        'package_url': '',
    }
    with _processed_lock:
        _processed_events.add('evt_dup')

    with patch('cascadia.depot.purchase_webhook.install') as mock_install:
        outcome = process_purchase(purchase)
        mock_install.assert_not_called()

    assert outcome['ok'] is False
    assert 'duplicate' in outcome['error']
    _clear_processed()


def test_process_purchase_uses_catalog():
    _clear_processed()
    resolved = [None]

    def catalog_lookup(op_id: str):
        resolved[0] = op_id
        return VALID_MANIFEST

    purchase = {
        'event_id': 'evt_catalog',
        'operator_id': 'purchased-op',
        'customer_id': 'cus_1',
        'customer_email': '',
        'package_url': '',
    }
    with patch('cascadia.depot.purchase_webhook.install') as mock_install:
        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.health_ok = False
        mock_result.error = None
        mock_result.manifest = VALID_MANIFEST
        mock_install.return_value = mock_result

        process_purchase(purchase, catalog_lookup=catalog_lookup)

    assert resolved[0] == 'purchased-op'
    _clear_processed()


# ── HTTP server ───────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def webhook_server():
    _clear_processed()
    server = create_server(
        port=0,
        catalog_lookup=lambda op_id: VALID_MANIFEST if op_id == 'purchased-op' else None,
        stripe_secret='',  # empty = accept all (dev mode)
    )
    # Bind to free port
    server = HTTPServer(('127.0.0.1', 0), type('H', (_PurchaseWebhookHandler,), {
        'catalog_lookup': staticmethod(lambda op_id: VALID_MANIFEST if op_id == 'purchased-op' else None),
        'stripe_secret': '',
    }))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{port}'
    server.shutdown()


def _post(url, body, headers=None):
    raw = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=raw, method='POST',
        headers={'Content-Type': 'application/json',
                 'Content-Length': str(len(raw)), **(headers or {})},
    )
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


def test_http_health(webhook_server):
    status, body = _get(f'{webhook_server}/health')
    assert status == 200
    assert body['service'] == NAME


def test_http_wrong_path(webhook_server):
    status, body = _post(f'{webhook_server}/webhook/other', {})
    assert status == 404


def test_http_non_depot_event_ignored(webhook_server):
    _clear_processed()
    event = {'id': 'evt_sub', 'type': 'invoice.paid', 'data': {'object': {}}}
    status, body = _post(f'{webhook_server}/webhook/stripe/purchase', event)
    assert status == 200
    assert body['action'] == 'ignored'
    _clear_processed()


def test_http_invalid_json(webhook_server):
    req = urllib.request.Request(
        f'{webhook_server}/webhook/stripe/purchase',
        data=b'not json',
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req) as r:
            body = json.loads(r.read())
            assert 'JSON' in body.get('error', '') or body.get('ok') is False
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_http_depot_purchase_triggers_install(webhook_server):
    _clear_processed()
    event = {**CHECKOUT_EVENT, 'id': 'evt_http_ok'}
    with patch('cascadia.depot.purchase_webhook.install') as mock_install:
        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.health_ok = True
        mock_result.error = None
        mock_result.manifest = VALID_MANIFEST
        mock_install.return_value = mock_result

        status, body = _post(f'{webhook_server}/webhook/stripe/purchase', event)

    assert status == 200
    assert body['install_ok'] is True
    _clear_processed()


def test_http_get_health(webhook_server):
    status, body = _get(f'{webhook_server}/health')
    assert status == 200
    assert 'uptime_seconds' in body


# ── Metadata ──────────────────────────────────────────────────────────────────

def test_metadata():
    assert NAME == 'purchase-webhook'
    assert VERSION == '1.0.0'
