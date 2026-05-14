"""
Tests for connector vault secret loading and SDK namespace support.

Covers:
  a. vault_get() sends namespace in payload
  b. vault_get() backward compatibility (default namespace)
  c-d. Each connector reads its manifest vault_key from namespace="secrets"
  e. Env/config fallback when vault returns None
"""
import importlib
import json
import sys
from types import ModuleType
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Task a & b — SDK vault_get namespace payload
# ---------------------------------------------------------------------------

def _import_sdk() -> ModuleType:
    """Import cascadia_sdk from sdk/ (not an installed package)."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "cascadia_sdk",
        Path(__file__).parent.parent / "sdk" / "cascadia_sdk.py",
    )
    mod = importlib.util.module_from_spec(spec)
    return mod, spec


def test_vault_get_sends_namespace_secrets():
    """vault_get(key, namespace='secrets') includes namespace in POST payload."""
    mod, spec = _import_sdk()
    captured = {}

    def fake_post(port, path, body):
        captured.update(body)
        return {"value": "tok123"}

    mod._post = fake_post  # type: ignore[attr-defined]
    mod._PORTS = {"vault": 9999}  # type: ignore[attr-defined]
    spec.loader.exec_module(mod)

    with patch.object(mod, "_post", fake_post):
        result = mod.vault_get("telegram:bot_token", namespace="secrets")

    assert captured.get("namespace") == "secrets"
    assert captured.get("key") == "telegram:bot_token"


def test_vault_get_default_namespace_backward_compat():
    """vault_get(key) uses namespace='default' — no regression for existing callers."""
    mod, spec = _import_sdk()
    captured = {}

    def fake_post(port, path, body):
        captured.update(body)
        return {"value": "some_val"}

    mod._post = fake_post  # type: ignore[attr-defined]
    mod._PORTS = {"vault": 9999}  # type: ignore[attr-defined]
    spec.loader.exec_module(mod)

    with patch.object(mod, "_post", fake_post):
        mod.vault_get("some_key")

    assert captured.get("namespace") == "default"
    assert captured.get("key") == "some_key"


# ---------------------------------------------------------------------------
# Helper — load a connector module with vault_get mocked at import time
# ---------------------------------------------------------------------------

def _load_connector(connector_name: str, vault_return: Optional[str], env_var: str, env_val: str):
    """
    Import a connector module with vault_get and os.environ mocked.
    Returns the freshly imported module.
    """
    module_key = f"cascadia.connectors.{connector_name}.connector"
    # Remove from cache to force re-import with new mocks
    for k in list(sys.modules.keys()):
        if connector_name in k or k == "cascadia_sdk":
            del sys.modules[k]

    fake_vault = MagicMock(return_value=vault_return)
    fake_sdk = MagicMock()
    fake_sdk.vault_get = fake_vault

    with patch.dict("sys.modules", {"cascadia_sdk": fake_sdk}):
        with patch.dict("os.environ", {env_var: env_val} if env_val else {}, clear=False):
            mod = importlib.import_module(module_key)

    return mod, fake_vault


# ---------------------------------------------------------------------------
# Task c — Telegram reads telegram:bot_token from secrets namespace
# ---------------------------------------------------------------------------

def test_telegram_vault_key_and_namespace():
    """Telegram connector calls vault_get('telegram:bot_token', namespace='secrets')."""
    for k in list(sys.modules.keys()):
        if "telegram" in k or k == "cascadia_sdk":
            del sys.modules[k]

    fake_vault = MagicMock(return_value="tg_token_from_vault")
    fake_sdk = MagicMock()
    fake_sdk.vault_get = fake_vault

    with patch.dict("sys.modules", {"cascadia_sdk": fake_sdk}):
        with patch.dict("os.environ", {}, clear=False):
            mod = importlib.import_module("cascadia.connectors.telegram.connector")

    fake_vault.assert_called_once_with("telegram:bot_token", namespace="secrets")
    assert mod._BOT_TOKEN == "tg_token_from_vault"


# ---------------------------------------------------------------------------
# Task d — Slack, Discord, WhatsApp read their manifest vault_key
# ---------------------------------------------------------------------------

def test_slack_vault_key_and_namespace():
    """Slack connector calls vault_get('slack:bot_token', namespace='secrets')."""
    for k in list(sys.modules.keys()):
        if "slack" in k or k == "cascadia_sdk":
            del sys.modules[k]

    fake_vault = MagicMock(return_value="slack_token_from_vault")
    fake_sdk = MagicMock()
    fake_sdk.vault_get = fake_vault

    with patch.dict("sys.modules", {"cascadia_sdk": fake_sdk}):
        mod = importlib.import_module("cascadia.connectors.slack.connector")

    fake_vault.assert_called_once_with("slack:bot_token", namespace="secrets")
    assert mod._BOT_TOKEN == "slack_token_from_vault"


def test_discord_vault_key_and_namespace():
    """Discord connector calls vault_get('discord:bot_token', namespace='secrets')."""
    for k in list(sys.modules.keys()):
        if "discord" in k or k == "cascadia_sdk":
            del sys.modules[k]

    fake_vault = MagicMock(return_value="discord_token_from_vault")
    fake_sdk = MagicMock()
    fake_sdk.vault_get = fake_vault

    with patch.dict("sys.modules", {"cascadia_sdk": fake_sdk}):
        mod = importlib.import_module("cascadia.connectors.discord.connector")

    fake_vault.assert_called_once_with("discord:bot_token", namespace="secrets")
    assert mod._BOT_TOKEN == "discord_token_from_vault"


def test_whatsapp_vault_key_and_namespace():
    """WhatsApp connector calls vault_get('whatsapp:api_key', namespace='secrets')."""
    for k in list(sys.modules.keys()):
        if "whatsapp" in k or k == "cascadia_sdk":
            del sys.modules[k]

    fake_vault = MagicMock(return_value="wa_token_from_vault")
    fake_sdk = MagicMock()
    fake_sdk.vault_get = fake_vault

    with patch.dict("sys.modules", {"cascadia_sdk": fake_sdk}):
        mod = importlib.import_module("cascadia.connectors.whatsapp.connector")

    fake_vault.assert_called_once_with("whatsapp:api_key", namespace="secrets")
    assert mod._ACCESS_TOKEN == "wa_token_from_vault"


# ---------------------------------------------------------------------------
# Task e — env/config fallback when vault returns None
# ---------------------------------------------------------------------------

def test_telegram_falls_through_to_env_when_vault_empty():
    """When vault returns None, connector reads TELEGRAM_BOT_TOKEN env var."""
    for k in list(sys.modules.keys()):
        if "telegram" in k or k == "cascadia_sdk":
            del sys.modules[k]

    fake_vault = MagicMock(return_value=None)
    fake_sdk = MagicMock()
    fake_sdk.vault_get = fake_vault

    with patch.dict("sys.modules", {"cascadia_sdk": fake_sdk}):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "env_tg_token"}, clear=False):
            mod = importlib.import_module("cascadia.connectors.telegram.connector")

    assert mod._BOT_TOKEN == "env_tg_token"


def test_slack_falls_through_to_env_when_vault_empty():
    """When vault returns None, connector reads SLACK_BOT_TOKEN env var."""
    for k in list(sys.modules.keys()):
        if "slack" in k or k == "cascadia_sdk":
            del sys.modules[k]

    fake_vault = MagicMock(return_value=None)
    fake_sdk = MagicMock()
    fake_sdk.vault_get = fake_vault

    with patch.dict("sys.modules", {"cascadia_sdk": fake_sdk}):
        with patch.dict("os.environ", {"SLACK_BOT_TOKEN": "env_slack_token"}, clear=False):
            mod = importlib.import_module("cascadia.connectors.slack.connector")

    assert mod._BOT_TOKEN == "env_slack_token"


def test_discord_falls_through_to_env_when_vault_empty():
    """When vault returns None, connector reads DISCORD_BOT_TOKEN env var."""
    for k in list(sys.modules.keys()):
        if "discord" in k or k == "cascadia_sdk":
            del sys.modules[k]

    fake_vault = MagicMock(return_value=None)
    fake_sdk = MagicMock()
    fake_sdk.vault_get = fake_vault

    with patch.dict("sys.modules", {"cascadia_sdk": fake_sdk}):
        with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "env_discord_token"}, clear=False):
            mod = importlib.import_module("cascadia.connectors.discord.connector")

    assert mod._BOT_TOKEN == "env_discord_token"


def test_whatsapp_falls_through_to_env_when_vault_empty():
    """When vault returns None, connector reads WHATSAPP_ACCESS_TOKEN env var."""
    for k in list(sys.modules.keys()):
        if "whatsapp" in k or k == "cascadia_sdk":
            del sys.modules[k]

    fake_vault = MagicMock(return_value=None)
    fake_sdk = MagicMock()
    fake_sdk.vault_get = fake_vault

    with patch.dict("sys.modules", {"cascadia_sdk": fake_sdk}):
        with patch.dict("os.environ", {"WHATSAPP_ACCESS_TOKEN": "env_wa_token"}, clear=False):
            mod = importlib.import_module("cascadia.connectors.whatsapp.connector")

    assert mod._ACCESS_TOKEN == "env_wa_token"


def test_connector_falls_through_to_config_file_when_vault_and_env_empty(tmp_path):
    """When vault and env are both empty, connector reads from config.json file."""
    for k in list(sys.modules.keys()):
        if "telegram" in k or k == "cascadia_sdk":
            del sys.modules[k]

    fake_vault = MagicMock(return_value=None)
    fake_sdk = MagicMock()
    fake_sdk.vault_get = fake_vault

    config_content = json.dumps({"bot_token": "config_file_token"})

    with patch.dict("sys.modules", {"cascadia_sdk": fake_sdk}):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": ""}, clear=False):
            with patch("pathlib.Path.read_text", return_value=config_content):
                mod = importlib.import_module("cascadia.connectors.telegram.connector")

    assert mod._BOT_TOKEN == "config_file_token"
