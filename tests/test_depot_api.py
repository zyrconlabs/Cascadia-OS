"""Tests for Task A2 — DEPOT API Server (port 6208)."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from typing import Dict
from unittest.mock import patch

import pytest

from cascadia.depot.api import (
    NAME,
    VERSION,
    PORT,
    _catalog,
    _catalog_lock,
    load_catalog,
    get_catalog_entries,
    get_entry,
    _safe_listing,
    proxy_install,
    handle_purchase,
    _DepotHandler,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_MANIFEST = {
    'id': 'test-op',
    'name': 'Test Operator',
    'type': 'operator',
    'version': '1.0.0',
    'description': 'A test operator for unit tests',
    'author': 'Zyrcon Labs',
    'price': 0,
    'tier_required': 'lite',
    'port': 8199,
    'entry_point': 'test.op',
    'dependencies': [],
    'install_hook': 'install.sh',
    'uninstall_hook': 'uninstall.sh',
    'category': 'operations',
    'industries': ['all'],
    'installed_by_default': False,
    'safe_to_uninstall': True,
    'risk_level': 'low',
    'permissions': [],
    'requires_approval_for': [],
    'data_access': [],
    'writes_external_systems': False,
    'network_access': False,
}

PAID_MANIFEST = {**SAMPLE_MANIFEST, 'id': 'paid-op', 'name': 'Paid Op',
                 'price': 29, 'tier_required': 'pro', 'category': 'sales'}

CONNECTOR_MANIFEST = {**SAMPLE_MANIFEST, 'id': 'test-conn', 'name': 'Test Connector',
                      'type': 'connector', 'port': 9990, 'category': 'runtime'}


def _load_samples():
    with _catalog_lock:
        _catalog.clear()
        _catalog['test-op'] = SAMPLE_MANIFEST
        _catalog['paid-op'] = PAID_MANIFEST
        _catalog['test-conn'] = CONNECTOR_MANIFEST


def _clear_catalog():
    with _catalog_lock:
        _catalog.clear()


# ── load_catalog ──────────────────────────────────────────────────────────────

def test_load_catalog_scans_connectors(tmp_path):
    """load_catalog finds manifest.json files under the connectors dir."""
    manifest_dir = tmp_path / 'myconn'
    manifest_dir.mkdir()
    (manifest_dir / 'manifest.json').write_text(json.dumps(CONNECTOR_MANIFEST))

    _clear_catalog()
    n = load_catalog(extra_dirs=[tmp_path])
    assert n >= 1
    assert 'test-conn' in _catalog
    _clear_catalog()


def test_load_catalog_skips_invalid_manifests(tmp_path):
    """Invalid manifests are silently skipped."""
    (tmp_path / 'manifest.json').write_text('{"id": "bad"}')  # missing required fields
    _clear_catalog()
    n = load_catalog(extra_dirs=[tmp_path])
    assert 'bad' not in _catalog
    _clear_catalog()


def test_load_catalog_skips_bad_json(tmp_path):
    bad = tmp_path / 'sub'
    bad.mkdir()
    (bad / 'manifest.json').write_text('not json at all')
    _clear_catalog()
    load_catalog(extra_dirs=[tmp_path])
    _clear_catalog()


# ── get_catalog_entries ───────────────────────────────────────────────────────

def test_list_all_entries():
    _load_samples()
    entries = get_catalog_entries()
    ids = {e['id'] for e in entries}
    assert ids == {'test-op', 'paid-op', 'test-conn'}
    _clear_catalog()


def test_filter_by_category():
    _load_samples()
    entries = get_catalog_entries(category='sales')
    assert all(e['id'] == 'paid-op' for e in entries)
    _clear_catalog()


def test_filter_by_tier():
    _load_samples()
    entries = get_catalog_entries(tier='pro')
    assert len(entries) == 1
    assert entries[0]['id'] == 'paid-op'
    _clear_catalog()


def test_filter_by_type():
    _load_samples()
    entries = get_catalog_entries(type_filter='connector')
    assert all(e['id'] == 'test-conn' for e in entries)
    _clear_catalog()


def test_filter_free_only():
    _load_samples()
    entries = get_catalog_entries(free_only=True)
    for e in entries:
        assert float(e.get('price', 0)) == 0
    assert not any(e['id'] == 'paid-op' for e in entries)
    _clear_catalog()


def test_search_by_name():
    _load_samples()
    entries = get_catalog_entries(q='Paid')
    assert len(entries) == 1
    assert entries[0]['id'] == 'paid-op'
    _clear_catalog()


def test_search_case_insensitive():
    _load_samples()
    entries = get_catalog_entries(q='test operator')
    assert any(e['id'] == 'test-op' for e in entries)
    _clear_catalog()


def test_search_no_match():
    _load_samples()
    entries = get_catalog_entries(q='xyznonexistent')
    assert entries == []
    _clear_catalog()


# ── get_entry / _safe_listing ─────────────────────────────────────────────────

def test_get_entry_found():
    _load_samples()
    entry = get_entry('test-op')
    assert entry is not None
    assert entry['id'] == 'test-op'
    _clear_catalog()


def test_get_entry_not_found():
    _clear_catalog()
    assert get_entry('nonexistent') is None


def test_safe_listing_strips_internal_path():
    manifest = {**SAMPLE_MANIFEST, '_manifest_path': '/internal/path'}
    listing = _safe_listing(manifest)
    assert '_manifest_path' not in listing
    assert listing['id'] == 'test-op'


def test_safe_listing_includes_expected_keys():
    listing = _safe_listing(SAMPLE_MANIFEST)
    for key in ('id', 'name', 'type', 'version', 'description', 'author',
                'price', 'tier_required', 'port', 'category'):
        assert key in listing


# ── proxy_install ─────────────────────────────────────────────────────────────

def test_proxy_install_not_in_catalog():
    _clear_catalog()
    result = proxy_install('missing-op')
    assert result['ok'] is False
    assert 'catalog' in result['error']


def test_proxy_install_crew_unreachable():
    _load_samples()
    # No CREW running on port 19999
    with patch('cascadia.depot.api.CREW_URL', 'http://127.0.0.1:19999'):
        result = proxy_install('test-op', requester='test')
    assert result['ok'] is False
    assert 'unreachable' in result['error']
    _clear_catalog()


def test_proxy_install_crew_http_error():
    _load_samples()
    import urllib.error
    with patch('urllib.request.urlopen') as mock_open:
        mock_exc = urllib.error.HTTPError(
            url='http://x', code=403, msg='Forbidden', hdrs={}, fp=None
        )
        mock_exc.read = lambda: b'{"error":"tier"}'
        mock_open.side_effect = mock_exc
        result = proxy_install('test-op')
    assert result['ok'] is False
    assert '403' in result['error']
    _clear_catalog()


def test_proxy_install_crew_success():
    _load_samples()
    with patch('urllib.request.urlopen') as mock_open:
        mock_resp = mock_open.return_value.__enter__.return_value
        mock_resp.read.return_value = json.dumps({'ok': True, 'status': 'installed'}).encode()
        result = proxy_install('test-op', requester='andy')
    assert result['ok'] is True
    _clear_catalog()


# ── handle_purchase ───────────────────────────────────────────────────────────

def test_purchase_missing_operator_id():
    result = handle_purchase({'customer_id': 'cus_123'})
    assert result['ok'] is False
    assert 'operator_id' in result['error']


def test_purchase_operator_not_in_catalog():
    _clear_catalog()
    result = handle_purchase({'operator_id': 'ghost-op', 'customer_id': 'cus_123'})
    assert result['ok'] is False
    assert 'catalog' in result['error']


def test_purchase_triggers_install():
    _load_samples()
    with patch('cascadia.depot.api.proxy_install') as mock_install:
        mock_install.return_value = {'ok': True, 'status': 'installed'}
        result = handle_purchase({'operator_id': 'test-op', 'customer_id': 'cus_abc'})
    assert result['ok'] is True
    assert result['operator_id'] == 'test-op'
    assert result['customer_id'] == 'cus_abc'
    mock_install.assert_called_once_with('test-op', requester='cus_abc',
                                         options={'source': 'purchase'})
    _clear_catalog()


# ── HTTP server ───────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def depot_server():
    _load_samples()
    server = HTTPServer(('127.0.0.1', 0), _DepotHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{port}'
    server.shutdown()


def _get(url) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _post(url, body=None) -> tuple[int, dict]:
    raw = json.dumps(body or {}).encode()
    req = urllib.request.Request(url, data=raw, method='POST',
                                 headers={'Content-Type': 'application/json',
                                          'Content-Length': str(len(raw))})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http_health(depot_server):
    status, body = _get(f'{depot_server}/health')
    assert status == 200
    assert body['service'] == NAME
    assert body['port'] == PORT


def test_http_list_operators(depot_server):
    status, body = _get(f'{depot_server}/v1/operators')
    assert status == 200
    assert body['ok'] is True
    assert body['count'] == 3


def test_http_filter_by_category(depot_server):
    status, body = _get(f'{depot_server}/v1/operators?category=sales')
    assert status == 200
    assert body['count'] == 1
    assert body['operators'][0]['id'] == 'paid-op'


def test_http_filter_by_type(depot_server):
    status, body = _get(f'{depot_server}/v1/operators?type=connector')
    assert status == 200
    assert body['count'] == 1


def test_http_search(depot_server):
    status, body = _get(f'{depot_server}/v1/operators?q=Test+Operator')
    assert status == 200
    assert any(op['id'] == 'test-op' for op in body['operators'])


def test_http_get_operator(depot_server):
    status, body = _get(f'{depot_server}/v1/operators/test-op')
    assert status == 200
    assert body['ok'] is True
    assert body['operator']['id'] == 'test-op'


def test_http_get_operator_not_found(depot_server):
    status, body = _get(f'{depot_server}/v1/operators/no-such-op')
    assert status == 404
    assert body['ok'] is False


def test_http_categories(depot_server):
    status, body = _get(f'{depot_server}/v1/categories')
    assert status == 200
    assert 'sales' in body['categories']
    assert 'runtime' in body['categories']


def test_http_tiers(depot_server):
    status, body = _get(f'{depot_server}/v1/tiers')
    assert status == 200
    assert 'lite' in body['tiers']
    assert 'pro' in body['tiers']


def test_http_catalog_reload(depot_server):
    status, body = _get(f'{depot_server}/v1/catalog/reload')
    assert status == 200
    assert body['ok'] is True
    assert 'loaded' in body


def test_http_install_not_in_catalog(depot_server):
    _clear_catalog()
    status, body = _post(f'{depot_server}/v1/operators/ghost-op/install', {})
    assert status == 502
    assert body['ok'] is False
    _load_samples()


def test_http_purchase_missing_operator_id(depot_server):
    status, body = _post(f'{depot_server}/v1/purchase', {'customer_id': 'cus_x'})
    assert status == 400
    assert body['ok'] is False


def test_http_unknown_path(depot_server):
    status, body = _get(f'{depot_server}/v1/unknown')
    assert status == 404


# ── Metadata ──────────────────────────────────────────────────────────────────

def test_service_metadata():
    assert NAME == 'depot-api'
    assert VERSION == '1.0.0'
    assert PORT == 6208
