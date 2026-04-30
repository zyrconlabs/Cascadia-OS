"""Tests for Task A4 — Desktop → iOS Auto-Sync Publisher."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from cascadia.depot.sync_publisher import (
    NAME,
    VERSION,
    SyncEvent,
    build_event,
    _pending,
    _publish,
    _drain_pending,
    publish_installed,
    publish_uninstalled,
    publish_updated,
    publish_snapshot,
    handle_sync_request,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

MANIFEST = {
    'id': 'sync-test-op',
    'name': 'Sync Test Operator',
    'version': '1.2.0',
    'port': 8399,
    'tier_required': 'lite',
    'category': 'operations',
}


def _clear_pending():
    _pending.clear()


def _run(coro):
    return asyncio.run(coro)


# ── build_event ───────────────────────────────────────────────────────────────

def test_build_event_installed():
    ev = build_event('installed', MANIFEST, source='depot', health_ok=True)
    assert ev.event_type == 'installed'
    assert ev.operator_id == 'sync-test-op'
    assert ev.operator_name == 'Sync Test Operator'
    assert ev.version == '1.2.0'
    assert ev.health_ok is True
    assert ev.source == 'depot'


def test_build_event_uninstalled():
    ev = build_event('uninstalled', MANIFEST)
    assert ev.event_type == 'uninstalled'
    assert ev.health_ok is False


def test_event_subject_installed():
    ev = build_event('installed', MANIFEST)
    assert ev.subject == 'cascadia.sync.operators.installed'


def test_event_subject_uninstalled():
    ev = build_event('uninstalled', MANIFEST)
    assert ev.subject == 'cascadia.sync.operators.uninstalled'


def test_event_subject_updated():
    ev = build_event('updated', MANIFEST)
    assert ev.subject == 'cascadia.sync.operators.updated'


def test_event_subject_snapshot():
    ev = build_event('snapshot', MANIFEST)
    assert ev.subject == 'cascadia.sync.catalog.snapshot'


def test_event_to_dict():
    ev = build_event('installed', MANIFEST)
    d = ev.to_dict()
    assert d['event_type'] == 'installed'
    assert d['operator_id'] == 'sync-test-op'


def test_event_to_nats_payload():
    ev = build_event('installed', MANIFEST)
    raw = ev.to_nats_payload()
    data = json.loads(raw)
    assert data['publisher'] == NAME
    assert data['operator_id'] == 'sync-test-op'


# ── _publish — with NATS ──────────────────────────────────────────────────────

def test_publish_sends_to_nats():
    ev = build_event('installed', MANIFEST)
    published = []

    async def mock_publish(subject, payload):
        published.append((subject, json.loads(payload)))

    nc = MagicMock()
    nc.publish = mock_publish

    ok = _run(_publish(nc, ev))
    assert ok is True
    assert len(published) == 1
    assert published[0][0] == 'cascadia.sync.operators.installed'
    assert published[0][1]['operator_id'] == 'sync-test-op'


def test_publish_without_nats_queues_event():
    _clear_pending()
    ev = build_event('installed', MANIFEST)
    ok = _run(_publish(None, ev))
    assert ok is False
    assert len(_pending) == 1
    _clear_pending()


def test_publish_nats_error_queues_event():
    _clear_pending()
    ev = build_event('installed', MANIFEST)

    async def failing_publish(subject, payload):
        raise Exception('NATS connection lost')

    nc = MagicMock()
    nc.publish = failing_publish

    ok = _run(_publish(nc, ev))
    assert ok is False
    assert len(_pending) == 1
    _clear_pending()


# ── _drain_pending ────────────────────────────────────────────────────────────

def test_drain_pending_sends_queued():
    _clear_pending()
    ev1 = build_event('installed', MANIFEST)
    ev2 = build_event('uninstalled', {**MANIFEST, 'id': 'op2'})
    _pending.extend([ev1, ev2])

    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc = MagicMock()
    nc.publish = mock_publish

    sent = _run(_drain_pending(nc))
    assert sent == 2
    assert len(_pending) == 0
    _clear_pending()


def test_drain_pending_empty():
    _clear_pending()
    nc = MagicMock()
    nc.publish = AsyncMock()
    sent = _run(_drain_pending(nc))
    assert sent == 0


def test_drain_pending_partial_failure():
    _clear_pending()
    ev1 = build_event('installed', MANIFEST)
    ev2 = build_event('uninstalled', {**MANIFEST, 'id': 'op2'})
    _pending.extend([ev1, ev2])

    call_count = [0]

    async def flaky_publish(subject, payload):
        call_count[0] += 1
        if call_count[0] == 2:
            raise Exception('fail')

    nc = MagicMock()
    nc.publish = flaky_publish

    sent = _run(_drain_pending(nc))
    assert sent == 1
    assert len(_pending) == 1  # failed one re-queued
    _clear_pending()


# ── High-level publish functions ──────────────────────────────────────────────

def test_publish_installed():
    published = []

    async def mock_publish(subject, payload):
        published.append(json.loads(payload))

    nc = MagicMock()
    nc.publish = mock_publish

    ok = _run(publish_installed(nc, MANIFEST, source='purchase', health_ok=True))
    assert ok is True
    assert published[0]['event_type'] == 'installed'
    assert published[0]['health_ok'] is True
    assert published[0]['source'] == 'purchase'


def test_publish_uninstalled():
    published = []

    async def mock_publish(subject, payload):
        published.append(json.loads(payload))

    nc = MagicMock()
    nc.publish = mock_publish

    _run(publish_uninstalled(nc, MANIFEST))
    assert published[0]['event_type'] == 'uninstalled'


def test_publish_updated():
    published = []

    async def mock_publish(subject, payload):
        published.append(json.loads(payload))

    nc = MagicMock()
    nc.publish = mock_publish

    _run(publish_updated(nc, MANIFEST))
    assert published[0]['event_type'] == 'updated'


def test_publish_snapshot_multiple():
    m2 = {**MANIFEST, 'id': 'op2', 'port': 8400}
    published = []

    async def mock_publish(subject, payload):
        published.append(json.loads(payload))

    nc = MagicMock()
    nc.publish = mock_publish

    count = _run(publish_snapshot(nc, [MANIFEST, m2]))
    assert count == 2
    assert all(p['event_type'] == 'snapshot' for p in published)
    ids = {p['operator_id'] for p in published}
    assert ids == {'sync-test-op', 'op2'}


def test_publish_snapshot_empty():
    nc = MagicMock()
    nc.publish = AsyncMock()
    count = _run(publish_snapshot(nc, []))
    assert count == 0


# ── handle_sync_request ───────────────────────────────────────────────────────

def test_sync_request_ping():
    published = []

    async def mock_publish(subject, payload):
        published.append((subject, json.loads(payload)))

    nc = MagicMock()
    nc.publish = mock_publish

    _run(handle_sync_request(nc, 'cascadia.sync.request.ping', b''))
    assert any('pong' in s for s, _ in published)
    pong = next(d for s, d in published if 'pong' in s)
    assert pong['publisher'] == NAME
    assert pong['version'] == VERSION


def test_sync_request_snapshot():
    catalog = [MANIFEST, {**MANIFEST, 'id': 'op2'}]

    published = []

    async def mock_publish(subject, payload):
        published.append((subject, json.loads(payload)))

    nc = MagicMock()
    nc.publish = mock_publish

    _run(handle_sync_request(
        nc, 'cascadia.sync.request.snapshot', b'{}',
        catalog_fn=lambda: catalog,
    ))

    snapshot_events = [(s, d) for s, d in published if 'snapshot' in s and 'response' not in s]
    assert len(snapshot_events) == 2

    response = next(d for s, d in published if 'response.snapshot' in s)
    assert response['operators_sent'] == 2


def test_sync_request_no_catalog_fn():
    published = []

    async def mock_publish(subject, payload):
        published.append((subject, json.loads(payload)))

    nc = MagicMock()
    nc.publish = mock_publish

    _run(handle_sync_request(nc, 'cascadia.sync.request.snapshot', b''))
    response = next(d for s, d in published if 'response.snapshot' in s)
    assert response['operators_sent'] == 0


def test_sync_request_invalid_json():
    nc = MagicMock()
    nc.publish = AsyncMock()
    # Should not raise
    _run(handle_sync_request(nc, 'cascadia.sync.request.ping', b'not json'))
    nc.publish.assert_called()


# ── Metadata ──────────────────────────────────────────────────────────────────

def test_metadata():
    assert NAME == 'depot-sync'
    assert VERSION == '1.0.0'
