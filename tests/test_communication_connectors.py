"""Tests for CON-013 Gmail, CON-014 Outlook, CON-015 Google Calendar, CON-016 Teams."""
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
    ('gmail',           'gmail-connector',           9500),
    ('outlook',         'outlook-connector',          9501),
    ('google_calendar', 'google-calendar-connector',  9502),
    ('teams',           'teams-connector',            9503),
])
def test_manifest_valid(dirname, expected_id, expected_port):
    path = BASE / dirname / 'manifest.json'
    assert path.exists()
    data = json.loads(path.read_text())
    result = validate_depot_manifest(data)
    assert result.valid, f"{dirname}: {result.errors}"
    assert data['id'] == expected_id
    assert data['port'] == expected_port
    assert data['type'] == 'connector'


@pytest.mark.parametrize('dirname', ['gmail', 'outlook', 'google_calendar', 'teams'])
def test_required_files_present(dirname):
    d = BASE / dirname
    for fname in ('manifest.json', 'connector.py', 'health.py', 'install.sh', 'uninstall.sh', 'README.md'):
        assert (d / fname).exists(), f"{dirname}/{fname} missing"


# ── Gmail connector ───────────────────────────────────────────────────────────

from cascadia.connectors.gmail.connector import (
    NAME as GMAIL_NAME, VERSION as GMAIL_VERSION, PORT as GMAIL_PORT,
    send_email as gmail_send, list_messages as gmail_list,
    get_message as gmail_get, execute_call as gmail_exec,
    handle_event as gmail_handle,
)

def test_gmail_metadata():
    assert GMAIL_NAME == 'gmail-connector'
    assert GMAIL_VERSION == '1.0.0'
    assert GMAIL_PORT == 9500


def test_gmail_send_email():
    with patch('urllib.request.urlopen') as mock:
        mock.return_value.__enter__.return_value.read.return_value = json.dumps(
            {'id': 'MSG123', 'threadId': 'T456', 'labelIds': ['SENT']}
        ).encode()
        result = gmail_send('bob@example.com', 'Test Subject', 'Hello Bob', 'access_tok')
    assert result['ok'] is True


def test_gmail_send_approval_gate():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({
        'action': 'send_email', 'to': 'bob@example.com',
        'subject': 'Hi', 'body': 'Hello', 'access_token': 'tok'
    }).encode()
    asyncio.run(gmail_handle(nc, 'cascadia.connectors.gmail-connector.call', raw))
    assert any('approvals' in s for s in published)


def test_gmail_list_messages_no_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    with patch('cascadia.connectors.gmail.connector.list_messages') as mock_list:
        mock_list.return_value = {'ok': True, 'messages': []}
        raw = json.dumps({'action': 'list_messages', 'access_token': 'tok'}).encode()
        asyncio.run(gmail_handle(nc, 'cascadia.connectors.gmail-connector.call', raw))
    assert not any('approvals' in s for s in published)
    assert any('response' in s for s in published)


def test_gmail_execute_missing_action():
    result = gmail_exec({})
    assert result['ok'] is False


# ── Outlook connector ─────────────────────────────────────────────────────────

from cascadia.connectors.outlook.connector import (
    NAME as OL_NAME, VERSION as OL_VERSION, PORT as OL_PORT,
    send_email as ol_send, list_messages as ol_list,
    execute_call as ol_exec, handle_event as ol_handle,
)

def test_outlook_metadata():
    assert OL_NAME == 'outlook-connector'
    assert OL_VERSION == '1.0.0'
    assert OL_PORT == 9501


def test_outlook_send_email():
    with patch('urllib.request.urlopen') as mock:
        mock.return_value.__enter__.return_value.status = 202
        mock.return_value.__enter__.return_value.read.return_value = b''
        result = ol_send('alice@example.com', 'Subject', 'Body', 'access_tok')
    assert result['ok'] is True


def test_outlook_approval_gate():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({
        'action': 'send_email', 'to': 'alice@example.com',
        'subject': 'Hi', 'body': 'Hello', 'access_token': 'tok'
    }).encode()
    asyncio.run(ol_handle(nc, 'cascadia.connectors.outlook-connector.call', raw))
    assert any('approvals' in s for s in published)


# ── Google Calendar connector ─────────────────────────────────────────────────

from cascadia.connectors.google_calendar.connector import (
    NAME as GC_NAME, VERSION as GC_VERSION, PORT as GC_PORT,
    list_events, create_event, get_event,
    execute_call as gc_exec, handle_event as gc_handle,
)

def test_gcal_metadata():
    assert GC_NAME == 'google-calendar-connector'
    assert GC_VERSION == '1.0.0'
    assert GC_PORT == 9502


def test_gcal_list_events():
    with patch('urllib.request.urlopen') as mock:
        mock.return_value.__enter__.return_value.read.return_value = json.dumps({
            'items': [{'id': 'EVT1', 'summary': 'Meeting', 'start': {'dateTime': '2026-01-01T09:00:00Z'}, 'end': {'dateTime': '2026-01-01T10:00:00Z'}}]
        }).encode()
        result = list_events('primary', 'access_tok')
    assert result['ok'] is True
    assert len(result['events']) == 1


def test_gcal_create_event_approval_gate():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({
        'action': 'create_event', 'calendar_id': 'primary',
        'summary': 'New Meeting', 'start': '2026-01-01T09:00:00Z',
        'end': '2026-01-01T10:00:00Z', 'access_token': 'tok'
    }).encode()
    asyncio.run(gc_handle(nc, 'cascadia.connectors.google-calendar-connector.call', raw))
    assert any('approvals' in s for s in published)


def test_gcal_list_events_no_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    with patch('cascadia.connectors.google_calendar.connector.list_events') as mock_list:
        mock_list.return_value = {'ok': True, 'events': []}
        raw = json.dumps({'action': 'list_events', 'calendar_id': 'primary', 'access_token': 'tok'}).encode()
        asyncio.run(gc_handle(nc, 'cascadia.connectors.google-calendar-connector.call', raw))
    assert not any('approvals' in s for s in published)


# ── Microsoft Teams connector ─────────────────────────────────────────────────

from cascadia.connectors.teams.connector import (
    NAME as TEAMS_NAME, VERSION as TEAMS_VERSION, PORT as TEAMS_PORT,
    send_channel_message, send_chat_message, list_channels,
    execute_call as teams_exec, handle_event as teams_handle,
)

def test_teams_metadata():
    assert TEAMS_NAME == 'teams-connector'
    assert TEAMS_VERSION == '1.0.0'
    assert TEAMS_PORT == 9503


def test_teams_send_channel_message():
    with patch('urllib.request.urlopen') as mock:
        mock.return_value.__enter__.return_value.read.return_value = json.dumps(
            {'id': 'MSG789', 'body': {'content': 'Hello team'}}
        ).encode()
        result = send_channel_message('TEAM1', 'CHAN1', 'Hello team', 'access_tok')
    assert result['ok'] is True


def test_teams_approval_gate_channel():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({
        'action': 'send_channel_message', 'team_id': 'T1',
        'channel_id': 'C1', 'content': 'Hi team', 'access_token': 'tok'
    }).encode()
    asyncio.run(teams_handle(nc, 'cascadia.connectors.teams-connector.call', raw))
    assert any('approvals' in s for s in published)


def test_teams_list_channels_no_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    with patch('cascadia.connectors.teams.connector.list_channels') as mock_list:
        mock_list.return_value = {'ok': True, 'channels': []}
        raw = json.dumps({'action': 'list_channels', 'team_id': 'T1', 'access_token': 'tok'}).encode()
        asyncio.run(teams_handle(nc, 'cascadia.connectors.teams-connector.call', raw))
    assert not any('approvals' in s for s in published)
    assert any('response' in s for s in published)
