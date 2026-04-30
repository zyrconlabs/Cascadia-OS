"""Tests for B2 (email/SMTP), B3 (calendar), B4 (zapier) connectors."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cascadia.depot.manifest_validator import validate_depot_manifest

BASE = Path(__file__).parent.parent / 'cascadia' / 'connectors'

# ── Manifest validation ───────────────────────────────────────────────────────

@pytest.mark.parametrize('dirname,expected_id,expected_port', [
    ('email',    'email-connector',    9010),
    ('calendar', 'calendar-connector', 9031),
    ('zapier',   'zapier-connector',   9030),
])
def test_manifest_valid(dirname, expected_id, expected_port):
    path = BASE / dirname / 'manifest.json'
    assert path.exists(), f"manifest.json missing in {dirname}"
    data = json.loads(path.read_text())
    result = validate_depot_manifest(data)
    assert result.valid, f"{dirname}: {result.errors}"
    assert data['id'] == expected_id
    assert data['port'] == expected_port
    assert data['type'] == 'connector'
    assert data['installed_by_default'] is False


@pytest.mark.parametrize('dirname', ['email', 'calendar', 'zapier'])
def test_required_files_present(dirname):
    d = BASE / dirname
    for fname in ('manifest.json', 'connector.py', 'health.py', 'install.sh', 'uninstall.sh', 'README.md'):
        assert (d / fname).exists(), f"{dirname}/{fname} missing"


# ── Email connector ───────────────────────────────────────────────────────────

from cascadia.connectors.email.connector import (
    NAME as EMAIL_NAME, VERSION as EMAIL_VERSION, PORT as EMAIL_PORT,
    execute_call as email_exec, handle_event as email_handle,
)

def test_email_metadata():
    assert EMAIL_NAME == 'email-connector'
    assert EMAIL_VERSION == '1.0.0'
    assert EMAIL_PORT == 9010


def test_email_send_requires_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({
        'action': 'send_email',
        'smtp_host': 'smtp.example.com', 'smtp_port': 587,
        'username': 'u', 'password': 'p',
        'to': 'alice@example.com', 'subject': 'Hi', 'body': 'Hello'
    }).encode()
    asyncio.run(email_handle(nc, 'cascadia.connectors.email-connector.call', raw))
    assert any('approvals' in s for s in published)


def test_email_list_inbox_no_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    with patch('cascadia.connectors.email.connector.list_inbox') as mock_list:
        mock_list.return_value = {'ok': True, 'messages': []}
        raw = json.dumps({
            'action': 'list_inbox',
            'imap_host': 'imap.example.com', 'username': 'u', 'password': 'p'
        }).encode()
        asyncio.run(email_handle(nc, 'cascadia.connectors.email-connector.call', raw))
    assert not any('approvals' in s for s in published)
    assert any('response' in s for s in published)


def test_email_execute_missing_action():
    result = email_exec({})
    assert result['ok'] is False


# ── Calendar connector ────────────────────────────────────────────────────────

from cascadia.connectors.calendar.connector import (
    NAME as CAL_NAME, VERSION as CAL_VERSION, PORT as CAL_PORT,
    execute_call as cal_exec, handle_event as cal_handle,
)

def test_calendar_metadata():
    assert CAL_NAME == 'calendar-connector'
    assert CAL_VERSION == '1.0.0'
    assert CAL_PORT == 9031


def test_calendar_create_event_requires_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({
        'action': 'create_event', 'provider': 'google',
        'credentials': {'access_token': 'tok'},
        'calendar_id': 'primary',
        'summary': 'Meeting', 'start': '2026-01-01T09:00:00Z', 'end': '2026-01-01T10:00:00Z'
    }).encode()
    asyncio.run(cal_handle(nc, 'cascadia.connectors.calendar-connector.call', raw))
    assert any('approvals' in s for s in published)


def test_calendar_delete_event_requires_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({
        'action': 'delete_event', 'provider': 'google',
        'credentials': {'access_token': 'tok'},
        'calendar_id': 'primary', 'event_id': 'EVT1'
    }).encode()
    asyncio.run(cal_handle(nc, 'cascadia.connectors.calendar-connector.call', raw))
    assert any('approvals' in s for s in published)


def test_calendar_list_events_no_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    with patch('cascadia.connectors.calendar.connector.list_events') as mock_list:
        mock_list.return_value = {'ok': True, 'events': []}
        raw = json.dumps({
            'action': 'list_events', 'provider': 'google',
            'credentials': {'access_token': 'tok'}, 'calendar_id': 'primary'
        }).encode()
        asyncio.run(cal_handle(nc, 'cascadia.connectors.calendar-connector.call', raw))
    assert not any('approvals' in s for s in published)
    assert any('response' in s for s in published)


def test_calendar_execute_missing_action():
    result = cal_exec({})
    assert result['ok'] is False


# ── Zapier connector ──────────────────────────────────────────────────────────

from cascadia.connectors.zapier.connector import (
    NAME as ZAP_NAME, VERSION as ZAP_VERSION, PORT as ZAP_PORT,
    list_hooks, register_hook, delete_hook,
    execute_call as zap_exec, handle_event as zap_handle,
)

def test_zapier_metadata():
    assert ZAP_NAME == 'zapier-connector'
    assert ZAP_VERSION == '1.0.0'
    assert ZAP_PORT == 9030


def test_zapier_register_requires_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({
        'action': 'register_hook', 'hook_id': 'h1',
        'name': 'My Hook', 'target_operator': 'lead-intake'
    }).encode()
    asyncio.run(zap_handle(nc, 'cascadia.connectors.zapier-connector.call', raw))
    assert any('approvals' in s for s in published)


def test_zapier_send_requires_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({
        'action': 'send_to_zapier',
        'webhook_url': 'https://hooks.zapier.com/hooks/catch/123/abc',
        'payload': {'event': 'new_lead'}
    }).encode()
    asyncio.run(zap_handle(nc, 'cascadia.connectors.zapier-connector.call', raw))
    assert any('approvals' in s for s in published)


def test_zapier_list_hooks_no_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'list_hooks'}).encode()
    asyncio.run(zap_handle(nc, 'cascadia.connectors.zapier-connector.call', raw))
    assert not any('approvals' in s for s in published)
    assert any('response' in s for s in published)


def test_zapier_execute_missing_action():
    result = zap_exec({})
    assert result['ok'] is False
