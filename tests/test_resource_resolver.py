"""Tests for cascadia.settings.resource_resolver (Phase 5)."""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from cascadia.settings.resource_resolver import (
    get_installed_connectors,
    resolve_resource,
    suggest_fallback,
    _FALLBACK_MAP,
    _BUILTIN_IDS,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_connectors_dir(tmp_path):
    """Create a fake connectors directory with two manifests."""
    for cid, name in [("gmail", "Gmail Connector"), ("slack-connector", "Slack")]:
        d = tmp_path / cid
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps({"id": cid, "name": name, "type": "connector"}),
            encoding="utf-8",
        )
    return tmp_path


FAKE_INSTALLED = [
    {"id": "gmail", "name": "Gmail Connector", "type": "connector"},
    {"id": "slack-connector", "name": "Slack", "type": "connector"},
]


# ── get_installed_connectors ──────────────────────────────────────────────────

def test_get_installed_connectors_returns_list(fake_connectors_dir):
    result = get_installed_connectors(fake_connectors_dir)
    assert len(result) == 2


def test_get_installed_connectors_returns_dicts(fake_connectors_dir):
    result = get_installed_connectors(fake_connectors_dir)
    assert all(isinstance(r, dict) for r in result)


def test_get_installed_connectors_missing_dir():
    result = get_installed_connectors(Path("/nonexistent/path"))
    assert result == []


def test_get_installed_connectors_skips_dirs_without_manifest(tmp_path):
    (tmp_path / "empty_dir").mkdir()
    result = get_installed_connectors(tmp_path)
    assert result == []


# ── resolve_resource — installed ──────────────────────────────────────────────

def test_installed_connector_returns_installed_status():
    result = resolve_resource("gmail", installed=FAKE_INSTALLED)
    assert result["status"] == "installed"


def test_builtin_connector_returns_installed():
    result = resolve_resource("webhook", installed=[])
    assert result["status"] == "installed"


def test_builtin_slack_returns_installed():
    result = resolve_resource("slack", installed=[])
    assert result["status"] == "installed"


# ── resolve_resource — fallback ───────────────────────────────────────────────

def test_missing_crm_returns_fallback():
    result = resolve_resource("hubspot", installed=[])
    assert result["status"] == "fallback"


def test_missing_crm_fallback_is_google_sheets():
    result = resolve_resource("hubspot", installed=[])
    assert result["id"] == "google_sheets"
    assert result["fallback_for"] == "hubspot"


def test_missing_sms_fallback_is_email():
    result = resolve_resource("twilio", installed=[])
    assert result["status"] == "fallback"
    assert result["id"] == "gmail"


def test_missing_field_service_suggests_google_sheets():
    result = resolve_resource("jobber", installed=[])
    assert result["status"] == "fallback"
    assert result["id"] == "google_sheets"


def test_truly_unknown_resource_returns_unavailable():
    result = resolve_resource("nonexistent_crm_xyz", installed=[])
    assert result["status"] == "unavailable"


# ── suggest_fallback ──────────────────────────────────────────────────────────

def test_suggest_fallback_for_missing_crm():
    result = suggest_fallback("salesforce", installed=[])
    assert result is not None
    assert result["id"] == "google_sheets"


def test_suggest_fallback_returns_none_for_unknown():
    result = suggest_fallback("nonexistent_app", installed=[])
    assert result is None


def test_suggest_fallback_for_missing_accounting():
    result = suggest_fallback("quickbooks", installed=[])
    assert result is not None


def test_suggest_fallback_for_missing_form_builder():
    result = suggest_fallback("typeform", installed=[])
    assert result is not None
    assert result["id"] == "webhook"


# ── Fallback map sanity ───────────────────────────────────────────────────────

def test_fallback_map_has_expected_keys():
    assert "hubspot" in _FALLBACK_MAP
    assert "twilio" in _FALLBACK_MAP
    assert "quickbooks" in _FALLBACK_MAP
    assert "typeform" in _FALLBACK_MAP


def test_builtin_ids_include_core_connectors():
    assert "gmail" in _BUILTIN_IDS
    assert "webhook" in _BUILTIN_IDS
    assert "slack" in _BUILTIN_IDS
