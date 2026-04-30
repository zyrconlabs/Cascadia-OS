"""Tests for B1 — DEPOT packaging of CON-017 to CON-021."""
from __future__ import annotations

import asyncio
import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BASE = Path(__file__).parent.parent / 'cascadia' / 'connectors'

# ── Manifest validation ───────────────────────────────────────────────────────

from cascadia.depot.manifest_validator import validate_depot_manifest

CONNECTOR_DIRS = {
    'slack':    ('slack-connector',    9003),
    'telegram': ('telegram-connector', 9000),
    'whatsapp': ('whatsapp-connector', 9001),
    'discord':  ('discord-connector',  9004),
    'sms':      ('sms-connector',      9002),
}

@pytest.mark.parametrize('dirname,connector_info', list(CONNECTOR_DIRS.items()))
def test_manifest_valid(dirname, connector_info):
    expected_id, expected_port = connector_info
    path = BASE / dirname / 'manifest.json'
    assert path.exists(), f"manifest.json missing in {dirname}"
    data = json.loads(path.read_text())
    result = validate_depot_manifest(data)
    assert result.valid, f"{dirname} manifest invalid: {result.errors}"
    assert data['id'] == expected_id
    assert data['port'] == expected_port
    assert data['type'] == 'connector'
    assert data['installed_by_default'] is False


@pytest.mark.parametrize('dirname', list(CONNECTOR_DIRS.keys()))
def test_required_files_present(dirname):
    d = BASE / dirname
    for fname in ('manifest.json', 'connector.py', 'health.py', 'install.sh', 'uninstall.sh', 'README.md'):
        assert (d / fname).exists(), f"{dirname}/{fname} missing"


# ── Slack connector ───────────────────────────────────────────────────────────

from cascadia.connectors.slack.connector import (
    NAME as SLACK_NAME, VERSION as SLACK_VERSION, PORT as SLACK_PORT,
    send_message as slack_send, execute_call as slack_exec, handle_event as slack_handle,
    _HealthHandler as SlackHealth,
)

def test_slack_metadata():
    assert SLACK_NAME == 'slack-connector'
    assert SLACK_VERSION == '1.0.0'
    assert SLACK_PORT == 9003


def test_slack_send_message_calls_api():
    with patch('urllib.request.urlopen') as mock:
        mock.return_value.__enter__.return_value.read.return_value = json.dumps(
            {'ok': True, 'ts': '12345.6789', 'channel': 'C123'}
        ).encode()
        result = slack_send('C123', 'Hello!', 'xoxb-test-token')
    assert result['ok'] is True
    assert result['ts'] == '12345.6789'


def test_slack_send_message_api_error():
    with patch('urllib.request.urlopen') as mock:
        mock.return_value.__enter__.return_value.read.return_value = json.dumps(
            {'ok': False, 'error': 'channel_not_found'}
        ).encode()
        result = slack_send('CBAD', 'Hello', 'xoxb-test')
    assert result['ok'] is False


def test_slack_execute_call_missing_action():
    result = slack_exec({})
    assert result['ok'] is False


def test_slack_execute_call_send():
    with patch('cascadia.connectors.slack.connector.send_message') as mock_send:
        mock_send.return_value = {'ok': True, 'ts': '1.0'}
        result = slack_exec({'action': 'send_message', 'channel': 'C1', 'text': 'Hi', 'token': 'tok'})
    assert result['ok'] is True


def test_slack_approval_gate():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish

    raw = json.dumps({'action': 'send_message', 'channel': 'C1', 'text': 'Hi', 'token': 't'}).encode()
    asyncio.run(slack_handle(nc, 'cascadia.connectors.slack-connector.call', raw))
    assert any('approvals' in s for s in published)


def test_slack_health_handler_importable():
    assert hasattr(SlackHealth, 'do_GET')


# ── Telegram connector ────────────────────────────────────────────────────────

from cascadia.connectors.telegram.connector import (
    NAME as TG_NAME, VERSION as TG_VERSION, PORT as TG_PORT,
    send_message as tg_send, execute_call as tg_exec, handle_event as tg_handle,
)

def test_telegram_metadata():
    assert TG_NAME == 'telegram-connector'
    assert TG_VERSION == '1.0.0'
    assert TG_PORT == 9000


def test_telegram_send_message():
    with patch('urllib.request.urlopen') as mock:
        mock.return_value.__enter__.return_value.read.return_value = json.dumps(
            {'ok': True, 'result': {'message_id': 42}}
        ).encode()
        result = tg_send('123456', 'Hello from Cascadia', 'BOT_TOKEN')
    assert result['ok'] is True


def test_telegram_approval_gate():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'send_message', 'chat_id': '123', 'text': 'Hi', 'token': 't'}).encode()
    asyncio.run(tg_handle(nc, 'cascadia.connectors.telegram-connector.call', raw))
    assert any('approvals' in s for s in published)


# ── WhatsApp connector ────────────────────────────────────────────────────────

from cascadia.connectors.whatsapp.connector import (
    NAME as WA_NAME, VERSION as WA_VERSION, PORT as WA_PORT,
    send_message as wa_send, execute_call as wa_exec, handle_event as wa_handle,
)

def test_whatsapp_metadata():
    assert WA_NAME == 'whatsapp-connector'
    assert WA_VERSION == '1.0.0'
    assert WA_PORT == 9001


def test_whatsapp_send_message():
    with patch('urllib.request.urlopen') as mock:
        mock.return_value.__enter__.return_value.read.return_value = json.dumps(
            {'messages': [{'id': 'wamid.123'}]}
        ).encode()
        result = wa_send('12345678', '+15551234567', 'Hello!', 'EAA_TOKEN')
    assert 'messages' in result or result.get('ok') is not False


def test_whatsapp_approval_gate():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'send_message', 'phone_number_id': '1', 'to': '+1555', 'text': 'Hi', 'token': 't'}).encode()
    asyncio.run(wa_handle(nc, 'cascadia.connectors.whatsapp-connector.call', raw))
    assert any('approvals' in s for s in published)


# ── Discord connector ─────────────────────────────────────────────────────────

from cascadia.connectors.discord.connector import (
    NAME as DC_NAME, VERSION as DC_VERSION, PORT as DC_PORT,
    send_message as dc_send, execute_call as dc_exec, handle_event as dc_handle,
)

def test_discord_metadata():
    assert DC_NAME == 'discord-connector'
    assert DC_VERSION == '1.0.0'
    assert DC_PORT == 9004


def test_discord_send_message():
    with patch('urllib.request.urlopen') as mock:
        mock.return_value.__enter__.return_value.read.return_value = json.dumps(
            {'id': '987654321', 'channel_id': 'C1', 'content': 'Hello!'}
        ).encode()
        result = dc_send('C1', 'Hello!', 'Bot DISCORD_TOKEN')
    assert 'id' in result or result.get('ok') is not False


def test_discord_approval_gate():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'send_message', 'channel_id': 'C1', 'content': 'Hi', 'token': 't'}).encode()
    asyncio.run(dc_handle(nc, 'cascadia.connectors.discord-connector.call', raw))
    assert any('approvals' in s for s in published)


# ── SMS/Twilio connector ──────────────────────────────────────────────────────

from cascadia.connectors.sms.connector import (
    NAME as SMS_NAME, VERSION as SMS_VERSION, PORT as SMS_PORT,
    send_sms, execute_call as sms_exec, handle_event as sms_handle,
)

def test_sms_metadata():
    assert SMS_NAME == 'sms-connector'
    assert SMS_VERSION == '1.0.0'
    assert SMS_PORT == 9002


def test_sms_send():
    with patch('urllib.request.urlopen') as mock:
        mock.return_value.__enter__.return_value.read.return_value = json.dumps(
            {'sid': 'SM123', 'status': 'queued', 'to': '+15551234567'}
        ).encode()
        result = send_sms('+15559999999', '+15551234567', 'Test SMS', 'ACtest', 'authtoken')
    assert 'sid' in result or result.get('ok') is not False


def test_sms_always_requires_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'send_sms', 'from': '+1555', 'to': '+1666', 'body': 'Hi',
                      'account_sid': 'AC1', 'auth_token': 'tok'}).encode()
    asyncio.run(sms_handle(nc, 'cascadia.connectors.sms-connector.call', raw))
    assert any('approvals' in s for s in published)
