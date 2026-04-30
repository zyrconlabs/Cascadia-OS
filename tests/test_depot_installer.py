"""Tests for Task A3 — DEPOT One-Click Install Flow."""
from __future__ import annotations

import base64
import io
import json
import time
import zipfile
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from cascadia.depot.installer import (
    NAME,
    VERSION,
    Step,
    InstallEvent,
    InstallResult,
    fetch_package,
    extract_manifest,
    validate_manifest,
    poll_health,
    install,
    install_batch,
    _crew_install,
    _rollback,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

VALID_MANIFEST = {
    'id': 'test-install-op',
    'name': 'Test Install Operator',
    'type': 'operator',
    'version': '1.0.0',
    'description': 'For install tests',
    'author': 'Zyrcon Labs',
    'price': 0,
    'tier_required': 'lite',
    'port': 8299,
    'entry_point': 'test.install.op',
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


def _make_zip(manifest: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('manifest.json', json.dumps(manifest))
        zf.writestr('server.py', 'print("hello")')
    return buf.getvalue()


def _make_zip_b64(manifest: dict) -> str:
    return base64.b64encode(_make_zip(manifest)).decode()


# ── fetch_package ─────────────────────────────────────────────────────────────

def test_fetch_package_base64():
    data = b'hello zip'
    encoded = base64.b64encode(data).decode()
    result, err = fetch_package(encoded)
    assert err is None
    assert result == data


def test_fetch_package_invalid_base64():
    _, err = fetch_package('not!valid!base64!!!')
    assert err is not None
    assert 'base64' in err


def test_fetch_package_url_unreachable():
    _, err = fetch_package('http://127.0.0.1:19999/pkg.zip')
    assert err is not None
    assert 'download failed' in err or 'error' in err


def test_fetch_package_url_success():
    with patch('urllib.request.urlopen') as mock_open:
        mock_resp = mock_open.return_value.__enter__.return_value
        mock_resp.read.return_value = b'zip content'
        data, err = fetch_package('http://example.com/pkg.zip')
    assert err is None
    assert data == b'zip content'


# ── extract_manifest ──────────────────────────────────────────────────────────

def test_extract_manifest_root_level():
    zip_bytes = _make_zip(VALID_MANIFEST)
    manifest, err = extract_manifest(zip_bytes)
    assert err is None
    assert manifest['id'] == 'test-install-op'


def test_extract_manifest_nested():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('test-op/manifest.json', json.dumps(VALID_MANIFEST))
    manifest, err = extract_manifest(buf.getvalue())
    assert err is None
    assert manifest['id'] == 'test-install-op'


def test_extract_manifest_missing():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('server.py', 'x')
    _, err = extract_manifest(buf.getvalue())
    assert err is not None
    assert 'manifest.json' in err


def test_extract_manifest_bad_zip():
    _, err = extract_manifest(b'not a zip')
    assert err is not None
    assert 'zip' in err


def test_extract_manifest_bad_json():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('manifest.json', '{bad json}')
    _, err = extract_manifest(buf.getvalue())
    assert err is not None
    assert 'JSON' in err


# ── validate_manifest ─────────────────────────────────────────────────────────

def test_validate_manifest_valid():
    err = validate_manifest(VALID_MANIFEST)
    assert err is None


def test_validate_manifest_missing_fields():
    bad = {'id': 'x', 'name': 'y'}
    err = validate_manifest(bad)
    assert err is not None
    assert len(err) > 0


def test_validate_manifest_invalid_type():
    bad = {**VALID_MANIFEST, 'type': 'robot'}
    err = validate_manifest(bad)
    assert err is not None


# ── poll_health ───────────────────────────────────────────────────────────────

def test_poll_health_responds_200():
    with patch('urllib.request.urlopen') as mock_open:
        mock_resp = mock_open.return_value.__enter__.return_value
        mock_resp.status = 200
        assert poll_health(8299, timeout=2) is True


def test_poll_health_timeout():
    # Port 19999 won't respond
    assert poll_health(19999, path='/health', timeout=0.1, interval=0.05) is False


# ── _crew_install ─────────────────────────────────────────────────────────────

def test_crew_install_success():
    with patch('urllib.request.urlopen') as mock_open:
        mock_resp = mock_open.return_value.__enter__.return_value
        mock_resp.read.return_value = json.dumps({'installed': 'test-op', 'health_ok': True}).encode()
        result = _crew_install(VALID_MANIFEST, None)
    assert result.get('ok') is True


def test_crew_install_unreachable():
    with patch('cascadia.depot.installer.CREW_URL', 'http://127.0.0.1:19999'):
        result = _crew_install(VALID_MANIFEST, None)
    assert result['ok'] is False
    assert 'unreachable' in result['error']


def test_crew_install_http_403():
    import urllib.error
    with patch('urllib.request.urlopen') as mock_open:
        exc = urllib.error.HTTPError(
            url='http://x', code=403, msg='Forbidden', hdrs={}, fp=None
        )
        exc.read = lambda: b'{"error":"tier_required","tier_required":"pro"}'
        mock_open.side_effect = exc
        result = _crew_install(VALID_MANIFEST, None)
    assert result['ok'] is False
    assert result.get('crew_status') == 403


# ── install — happy path ──────────────────────────────────────────────────────

def test_install_with_manifest_override():
    """install() succeeds when manifest_override is provided and CREW responds ok."""
    with patch('cascadia.depot.installer._crew_install') as mock_crew, \
         patch('cascadia.depot.installer.poll_health', return_value=True):
        mock_crew.return_value = {'ok': True, 'health_ok': False, 'flint': {}}
        result = install('test-install-op', manifest_override=VALID_MANIFEST)

    assert result.ok is True
    assert result.operator_id == 'test-install-op'
    assert result.health_ok is True
    steps = {e.step for e in result.events}
    assert Step.VALIDATE in steps
    assert Step.REGISTER in steps
    assert Step.DONE in steps


def test_install_dry_run():
    with patch('cascadia.depot.installer._crew_install') as mock_crew:
        mock_crew.return_value = {'ok': True, 'flint': {}}
        result = install('test-install-op', manifest_override=VALID_MANIFEST, dry_run=True)

    assert result.ok is True
    done_ev = next(e for e in result.events if e.step == Step.DONE)
    assert 'dry_run' in done_ev.message


def test_install_progress_callback():
    steps_seen: List[str] = []

    def on_progress(ev: InstallEvent) -> None:
        steps_seen.append(ev.step)

    with patch('cascadia.depot.installer._crew_install') as mock_crew, \
         patch('cascadia.depot.installer.poll_health', return_value=False):
        mock_crew.return_value = {'ok': True, 'health_ok': False, 'flint': {}}
        install('test-install-op', manifest_override=VALID_MANIFEST, on_progress=on_progress)

    assert Step.VALIDATE in steps_seen
    assert Step.DONE in steps_seen


def test_install_from_zip():
    zip_b64 = _make_zip_b64(VALID_MANIFEST)
    with patch('cascadia.depot.installer._crew_install') as mock_crew, \
         patch('cascadia.depot.installer.poll_health', return_value=True):
        mock_crew.return_value = {'ok': True, 'health_ok': True, 'flint': {}}
        result = install('test-install-op', package_source=zip_b64)

    assert result.ok is True
    fetch_ev = next(e for e in result.events if e.step == Step.FETCH)
    assert fetch_ev.status == 'ok'


def test_install_result_to_dict():
    with patch('cascadia.depot.installer._crew_install') as mock_crew, \
         patch('cascadia.depot.installer.poll_health', return_value=True):
        mock_crew.return_value = {'ok': True, 'health_ok': False, 'flint': {}}
        result = install('test-install-op', manifest_override=VALID_MANIFEST)

    d = result.to_dict()
    assert 'ok' in d
    assert 'events' in d
    assert 'duration_ms' in d


# ── install — failure paths ───────────────────────────────────────────────────

def test_install_no_source_or_manifest():
    result = install('orphan-op')
    assert result.ok is False
    assert result.error is not None


def test_install_bad_base64():
    result = install('x', package_source='!!!notbase64!!!')
    assert result.ok is False
    fetch_ev = next(e for e in result.events if e.step == Step.FETCH)
    assert fetch_ev.status == 'error'


def test_install_invalid_manifest_in_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('manifest.json', json.dumps({'id': 'x'}))
    zip_b64 = base64.b64encode(buf.getvalue()).decode()
    result = install('x', package_source=zip_b64)
    assert result.ok is False
    validate_ev = next(e for e in result.events if e.step == Step.VALIDATE)
    assert validate_ev.status == 'error'


def test_install_tier_rejection():
    with patch('cascadia.depot.installer._crew_install') as mock_crew:
        mock_crew.return_value = {
            'ok': False, 'error': 'tier_required', 'crew_status': 403,
            'tier_required': 'pro', 'upgrade_url': 'https://zyrcon.store',
        }
        result = install('paid-op', manifest_override=VALID_MANIFEST)

    assert result.ok is False
    tier_ev = next(e for e in result.events if e.step == Step.TIER_CHECK)
    assert tier_ev.status == 'error'


def test_install_port_conflict():
    with patch('cascadia.depot.installer._crew_install') as mock_crew:
        mock_crew.return_value = {
            'ok': False, 'error': 'port_conflict', 'crew_status': 409,
            'port': 8299, 'conflict_with': 'other-op',
        }
        result = install('test-install-op', manifest_override=VALID_MANIFEST)

    assert result.ok is False
    reg_ev = next(e for e in result.events if e.step == Step.REGISTER)
    assert reg_ev.status == 'error'


def test_install_crew_unreachable():
    with patch('cascadia.depot.installer._crew_install') as mock_crew:
        mock_crew.return_value = {'ok': False, 'error': 'CREW unreachable: Connection refused'}
        result = install('test-install-op', manifest_override=VALID_MANIFEST)

    assert result.ok is False


# ── install_batch ─────────────────────────────────────────────────────────────

def test_install_batch_all_succeed():
    manifest_b = {**VALID_MANIFEST, 'id': 'op-b', 'port': 8300}
    with patch('cascadia.depot.installer._crew_install') as mock_crew, \
         patch('cascadia.depot.installer.poll_health', return_value=True):
        mock_crew.return_value = {'ok': True, 'health_ok': True, 'flint': {}}
        results = install_batch([
            {'operator_id': 'test-install-op', 'manifest_override': VALID_MANIFEST},
            {'operator_id': 'op-b', 'manifest_override': manifest_b},
        ])

    assert len(results) == 2
    assert all(r.ok for r in results)


def test_install_batch_partial_failure():
    with patch('cascadia.depot.installer._crew_install') as mock_crew, \
         patch('cascadia.depot.installer.poll_health', return_value=False):
        def side_effect(manifest, *a, **kw):
            if manifest.get('id') == 'test-install-op':
                return {'ok': True, 'health_ok': False, 'flint': {}}
            return {'ok': False, 'error': 'CREW error', 'crew_status': 500}

        mock_crew.side_effect = side_effect
        results = install_batch([
            {'operator_id': 'test-install-op', 'manifest_override': VALID_MANIFEST},
            {'operator_id': 'bad-op', 'manifest_override': {**VALID_MANIFEST, 'id': 'bad-op'}},
        ])

    assert results[0].ok is True
    assert results[1].ok is False


def test_install_batch_progress_callback():
    events: List[tuple] = []

    def on_progress(op_id: str, ev: InstallEvent) -> None:
        events.append((op_id, ev.step))

    with patch('cascadia.depot.installer._crew_install') as mock_crew, \
         patch('cascadia.depot.installer.poll_health', return_value=False):
        mock_crew.return_value = {'ok': True, 'health_ok': False, 'flint': {}}
        install_batch(
            [{'operator_id': 'test-install-op', 'manifest_override': VALID_MANIFEST}],
            on_progress=on_progress,
        )

    op_ids = {op for op, _ in events}
    assert 'test-install-op' in op_ids


# ── Metadata ──────────────────────────────────────────────────────────────────

def test_installer_metadata():
    assert NAME == 'depot-installer'
    assert VERSION == '1.0.0'
