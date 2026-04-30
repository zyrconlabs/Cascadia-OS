"""Tests for CON-020 — Google Accounts Connector (port 9020)."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cascadia.depot.manifest_validator import validate_depot_manifest

BASE = Path(__file__).parent.parent / "cascadia" / "connectors" / "google"

# ---------------------------------------------------------------------------
# Manifest and file presence
# ---------------------------------------------------------------------------

def test_manifest_valid():
    path = BASE / "manifest.json"
    assert path.exists(), "manifest.json missing"
    data = json.loads(path.read_text())
    result = validate_depot_manifest(data)
    assert result.valid, f"manifest invalid: {result.errors}"
    assert data["id"] == "google-connector"
    assert data["port"] == 9020
    assert data["type"] == "connector"
    assert data["auth_type"] == "oauth2"
    assert data["tier_required"] == "pro"
    assert data["safe_to_uninstall"] is False


def test_required_files_present():
    for fname in ("manifest.json", "connector.py", "health.py",
                  "install.sh", "uninstall.sh", "README.md"):
        assert (BASE / fname).exists(), f"{fname} missing from google connector"


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

from cascadia.connectors.google.connector import (
    NAME, VERSION, PORT,
    get_auth_url, exchange_code, refresh_access_token,
    get_user_info, revoke_token,
    execute_call, handle_event,
    _HealthHandler, _load_tokens, _save_tokens,
)


def test_metadata():
    assert NAME == "google-connector"
    assert VERSION == "1.0.0"
    assert PORT == 9020


# ---------------------------------------------------------------------------
# get_auth_url
# ---------------------------------------------------------------------------

def test_get_auth_url_builds_correct_url():
    with patch.dict(os.environ, {
        "GOOGLE_CLIENT_ID": "test-client-id.apps.googleusercontent.com",
        "GOOGLE_CLIENT_SECRET": "test-secret",
    }):
        result = get_auth_url(scopes=["openid", "email"], state="csrf123")
    assert result["ok"] is True
    url = result["url"]
    assert "accounts.google.com" in url
    assert "test-client-id.apps.googleusercontent.com" in url
    assert "openid" in url
    assert "email" in url
    assert "csrf123" in url
    assert "access_type=offline" in url


def test_get_auth_url_default_scopes():
    with patch.dict(os.environ, {
        "GOOGLE_CLIENT_ID": "cid",
        "GOOGLE_CLIENT_SECRET": "csec",
    }):
        result = get_auth_url()
    assert result["ok"] is True
    assert "openid" in result["url"]
    assert "profile" in result["url"]


def test_get_auth_url_missing_client_id():
    env = {k: v for k, v in os.environ.items()
           if k not in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")}
    with patch.dict(os.environ, env, clear=True):
        result = get_auth_url()
    assert result["ok"] is False
    assert "GOOGLE_CLIENT_ID" in result["error"]


# ---------------------------------------------------------------------------
# exchange_code
# ---------------------------------------------------------------------------

def test_exchange_code_success():
    token_response = {
        "access_token": "ya29.test_access",
        "refresh_token": "1//test_refresh",
        "expires_in": 3599,
        "scope": "openid email profile",
        "token_type": "Bearer",
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        token_file = os.path.join(tmpdir, "google_tokens.json")
        with patch.dict(os.environ, {
            "GOOGLE_CLIENT_ID": "cid",
            "GOOGLE_CLIENT_SECRET": "csec",
            "GOOGLE_TOKEN_FILE": token_file,
        }):
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.return_value.__enter__.return_value.read.return_value = (
                    json.dumps(token_response).encode()
                )
                result = exchange_code("auth_code_xyz")

    assert result["ok"] is True
    assert result["access_token"] == "ya29.test_access"
    assert result["refresh_token"] == "1//test_refresh"
    assert result["expires_in"] == 3599


def test_exchange_code_http_error():
    import urllib.error
    with patch.dict(os.environ, {
        "GOOGLE_CLIENT_ID": "cid",
        "GOOGLE_CLIENT_SECRET": "csec",
    }):
        with patch("urllib.request.urlopen") as mock_urlopen:
            err = urllib.error.HTTPError(
                url="https://oauth2.googleapis.com/token",
                code=400,
                msg="Bad Request",
                hdrs=MagicMock(),
                fp=None,
            )
            err.read = lambda: b'{"error":"invalid_grant"}'
            mock_urlopen.side_effect = err
            result = exchange_code("bad_code")

    assert result["ok"] is False
    assert "400" in result["error"]


# ---------------------------------------------------------------------------
# refresh_access_token
# ---------------------------------------------------------------------------

def test_refresh_access_token_uses_stored_token():
    stored = {
        "access_token": "old_access",
        "refresh_token": "stored_refresh",
        "expires_in": 3599,
    }
    new_token_response = {"access_token": "new_access_token", "expires_in": 3599}

    with tempfile.TemporaryDirectory() as tmpdir:
        token_file = os.path.join(tmpdir, "google_tokens.json")
        Path(token_file).write_text(json.dumps(stored))
        with patch.dict(os.environ, {
            "GOOGLE_CLIENT_ID": "cid",
            "GOOGLE_CLIENT_SECRET": "csec",
            "GOOGLE_TOKEN_FILE": token_file,
        }):
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.return_value.__enter__.return_value.read.return_value = (
                    json.dumps(new_token_response).encode()
                )
                result = refresh_access_token()

    assert result["ok"] is True
    assert result["access_token"] == "new_access_token"


def test_refresh_access_token_explicit_token():
    new_token_response = {"access_token": "fresh_token", "expires_in": 3599}
    with patch.dict(os.environ, {
        "GOOGLE_CLIENT_ID": "cid",
        "GOOGLE_CLIENT_SECRET": "csec",
    }):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value.read.return_value = (
                json.dumps(new_token_response).encode()
            )
            with patch(
                "cascadia.connectors.google.connector._load_tokens",
                return_value={"access_token": "fresh_token"},
            ):
                with patch("cascadia.connectors.google.connector._save_tokens"):
                    result = refresh_access_token(refresh_tok="explicit_refresh_tok")

    assert result["ok"] is True
    assert result["access_token"] == "fresh_token"


def test_refresh_access_token_no_token_available():
    with tempfile.TemporaryDirectory() as tmpdir:
        token_file = os.path.join(tmpdir, "google_tokens.json")
        with patch.dict(os.environ, {
            "GOOGLE_CLIENT_ID": "cid",
            "GOOGLE_CLIENT_SECRET": "csec",
            "GOOGLE_TOKEN_FILE": token_file,
        }):
            result = refresh_access_token()

    assert result["ok"] is False
    assert "refresh_token" in result["error"]


# ---------------------------------------------------------------------------
# get_user_info
# ---------------------------------------------------------------------------

def test_get_user_info_success():
    profile = {
        "sub": "1234567890",
        "email": "andy@example.com",
        "name": "Andy Test",
        "picture": "https://example.com/photo.jpg",
        "email_verified": True,
    }
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__.return_value.read.return_value = (
            json.dumps(profile).encode()
        )
        result = get_user_info(access_token="ya29.valid_token")

    assert result["ok"] is True
    assert result["email"] == "andy@example.com"
    assert result["name"] == "Andy Test"
    assert result["email_verified"] is True


def test_get_user_info_uses_stored_token():
    stored = {"access_token": "stored_tok", "refresh_token": "ref"}
    profile = {"sub": "999", "email": "user@example.com", "name": "User",
               "picture": "", "email_verified": True}

    with tempfile.TemporaryDirectory() as tmpdir:
        token_file = os.path.join(tmpdir, "google_tokens.json")
        Path(token_file).write_text(json.dumps(stored))
        with patch.dict(os.environ, {"GOOGLE_TOKEN_FILE": token_file}):
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.return_value.__enter__.return_value.read.return_value = (
                    json.dumps(profile).encode()
                )
                result = get_user_info()

    assert result["ok"] is True
    assert result["email"] == "user@example.com"


def test_get_user_info_no_token():
    with tempfile.TemporaryDirectory() as tmpdir:
        token_file = os.path.join(tmpdir, "google_tokens.json")
        with patch.dict(os.environ, {"GOOGLE_TOKEN_FILE": token_file}):
            result = get_user_info()

    assert result["ok"] is False
    assert "access_token" in result["error"]


# ---------------------------------------------------------------------------
# revoke_token
# ---------------------------------------------------------------------------

def test_revoke_token_success():
    with tempfile.TemporaryDirectory() as tmpdir:
        token_file = os.path.join(tmpdir, "google_tokens.json")
        Path(token_file).write_text(json.dumps({"access_token": "tok123"}))
        with patch.dict(os.environ, {"GOOGLE_TOKEN_FILE": token_file}):
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.return_value.__enter__.return_value.__exit__ = MagicMock(
                    return_value=False
                )
                mock_urlopen.return_value.__enter__.return_value.read.return_value = b""
                result = revoke_token()

    assert result["ok"] is True


def test_revoke_token_explicit():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__.return_value.read.return_value = b""
        with patch("cascadia.connectors.google.connector._token_file") as mock_tf:
            mock_tf.return_value = MagicMock(exists=lambda: False)
            result = revoke_token(token="explicit_access_token")

    assert result["ok"] is True


# ---------------------------------------------------------------------------
# execute_call dispatcher
# ---------------------------------------------------------------------------

def test_execute_call_unknown_action():
    result = execute_call({})
    assert result["ok"] is False
    assert "unknown action" in result["error"]


def test_execute_call_get_auth_url():
    with patch.dict(os.environ, {
        "GOOGLE_CLIENT_ID": "cid",
        "GOOGLE_CLIENT_SECRET": "csec",
    }):
        result = execute_call({"action": "get_auth_url", "scopes": ["openid"]})
    assert result["ok"] is True
    assert "accounts.google.com" in result["url"]


def test_execute_call_get_user_info():
    profile = {"sub": "1", "email": "a@b.com", "name": "A", "picture": "", "email_verified": True}
    with patch("urllib.request.urlopen") as mock:
        mock.return_value.__enter__.return_value.read.return_value = json.dumps(profile).encode()
        result = execute_call({"action": "get_user_info", "access_token": "tok"})
    assert result["ok"] is True
    assert result["email"] == "a@b.com"


def test_execute_call_refresh_access_token():
    with patch("cascadia.connectors.google.connector.refresh_access_token") as mock_refresh:
        mock_refresh.return_value = {"ok": True, "access_token": "new_tok", "expires_in": 3599}
        result = execute_call({"action": "refresh_access_token"})
    assert result["ok"] is True
    assert result["access_token"] == "new_tok"


# ---------------------------------------------------------------------------
# NATS handle_event — approval gate
# ---------------------------------------------------------------------------

def test_revoke_token_requires_approval():
    """revoke_token must go through the approval gate — never execute directly."""
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({"action": "revoke_token"}).encode()
    asyncio.run(handle_event(nc, "cascadia.connectors.google-connector.call", raw))
    assert any("approvals" in s for s in published), (
        "revoke_token must be routed to cascadia.approvals.request"
    )


def test_get_user_info_no_approval_required():
    """Read-only actions must not trigger the approval gate."""
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    profile = {"sub": "1", "email": "a@b.com", "name": "A", "picture": "", "email_verified": True}
    with patch("cascadia.connectors.google.connector.get_user_info") as mock_gui:
        mock_gui.return_value = {"ok": True, **profile}
        raw = json.dumps({"action": "get_user_info", "access_token": "tok"}).encode()
        asyncio.run(handle_event(nc, "cascadia.connectors.google-connector.call", raw))

    assert not any("approvals" in s for s in published), (
        "get_user_info must not trigger approval"
    )
    assert any("response" in s for s in published), (
        "get_user_info must publish a response"
    )


def test_get_auth_url_no_approval_required():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    with patch.dict(os.environ, {
        "GOOGLE_CLIENT_ID": "cid",
        "GOOGLE_CLIENT_SECRET": "csec",
    }):
        raw = json.dumps({"action": "get_auth_url", "scopes": ["openid"]}).encode()
        asyncio.run(handle_event(nc, "cascadia.connectors.google-connector.call", raw))

    assert not any("approvals" in s for s in published)
    assert any("response" in s for s in published)


def test_refresh_access_token_no_approval_required():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    with patch("cascadia.connectors.google.connector.refresh_access_token") as mock_r:
        mock_r.return_value = {"ok": True, "access_token": "tok", "expires_in": 3599}
        raw = json.dumps({"action": "refresh_access_token"}).encode()
        asyncio.run(handle_event(nc, "cascadia.connectors.google-connector.call", raw))

    assert not any("approvals" in s for s in published)
    assert any("response" in s for s in published)


def test_handle_event_invalid_json():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    asyncio.run(handle_event(nc, "cascadia.connectors.google-connector.call", b"not json"))
    assert len(published) == 0


# ---------------------------------------------------------------------------
# Token persistence
# ---------------------------------------------------------------------------

def test_save_and_load_tokens_roundtrip():
    tokens = {"access_token": "abc", "refresh_token": "xyz", "expires_in": 3599}
    with tempfile.TemporaryDirectory() as tmpdir:
        token_file = os.path.join(tmpdir, "tokens.json")
        with patch.dict(os.environ, {"GOOGLE_TOKEN_FILE": token_file}):
            _save_tokens(tokens)
            loaded = _load_tokens()
    assert loaded == tokens


def test_load_tokens_missing_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        token_file = os.path.join(tmpdir, "nonexistent.json")
        with patch.dict(os.environ, {"GOOGLE_TOKEN_FILE": token_file}):
            result = _load_tokens()
    assert result == {}


# ---------------------------------------------------------------------------
# HTTP handler importable
# ---------------------------------------------------------------------------

def test_health_handler_importable():
    assert hasattr(_HealthHandler, "do_GET")
