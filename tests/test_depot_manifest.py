"""Tests for cascadia.depot.manifest_validator (Task A1)."""
import json
import pytest
from pathlib import Path

from cascadia.depot.manifest_validator import (
    validate_depot_manifest,
    validate_depot_manifest_file,
    ValidationResult,
    VALID_TYPES,
    VALID_TIERS,
    VALID_CATEGORIES,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────

VALID_OPERATOR = {
    "id": "lead-intake",
    "name": "Lead Intake Operator",
    "type": "operator",
    "version": "1.0.0",
    "description": "Normalizes and deduplicates inbound leads.",
    "author": "Zyrcon Labs",
    "price": 0,
    "tier_required": "enterprise",
    "port": 8101,
    "entry_point": "operator.py",
    "dependencies": ["nats-py"],
    "install_hook": "install.sh",
    "uninstall_hook": "uninstall.sh",
    "category": "sales",
    "industries": ["general"],
    "installed_by_default": False,
    "safe_to_uninstall": True,
    "risk_level": "low",
    "permissions": ["read_crm_contacts", "write_crm_lead"],
    "requires_approval_for": ["write_crm_lead"],
    "data_access": ["lead_records"],
    "writes_external_systems": False,
    "network_access": False,
}

VALID_CONNECTOR = {
    "id": "salesforce",
    "name": "Salesforce Connector",
    "type": "connector",
    "version": "2.1.0",
    "description": "Connects Cascadia OS to Salesforce CRM via OAuth2.",
    "author": "Zyrcon Labs",
    "price": 0,
    "tier_required": "pro",
    "port": 9400,
    "entry_point": "connector.py",
    "dependencies": [],
    "install_hook": "install.sh",
    "uninstall_hook": "uninstall.sh",
    "category": "sales",
    "industries": ["general", "finance"],
    "installed_by_default": False,
    "safe_to_uninstall": True,
    "auth_type": "oauth2",
    "approval_required_for_writes": True,
    "risk_level": "medium",
    "permissions": ["read_crm", "write_crm"],
    "requires_approval_for": ["write_crm"],
    "data_access": ["crm_contacts", "deals"],
    "writes_external_systems": True,
    "network_access": True,
}


def _without(d: dict, *keys) -> dict:
    """Return dict with specified keys removed."""
    return {k: v for k, v in d.items() if k not in keys}


def _with(d: dict, **overrides) -> dict:
    """Return dict with overrides applied."""
    return {**d, **overrides}


# ── Valid manifests pass ──────────────────────────────────────────────────────

def test_valid_operator_passes():
    result = validate_depot_manifest(VALID_OPERATOR)
    assert result.valid is True
    assert result.errors == []


def test_valid_connector_passes():
    result = validate_depot_manifest(VALID_CONNECTOR)
    assert result.valid is True
    assert result.errors == []


def test_free_operator_price_zero():
    result = validate_depot_manifest(_with(VALID_OPERATOR, price=0))
    assert result.valid is True


def test_paid_operator_nonzero_price():
    result = validate_depot_manifest(_with(VALID_OPERATOR, price=29.99))
    assert result.valid is True


def test_optional_fields_accepted():
    data = _with(
        VALID_OPERATOR,
        icon="icon.png",
        approval_required=True,
        nats_subjects=["cascadia.operators.lead-intake.>"],
        screenshots=["screen1.png"],
        readme="README.md",
        homepage_url="https://zyrcon.ai",
        support_email="support@zyrcon.ai",
    )
    result = validate_depot_manifest(data)
    assert result.valid is True


def test_empty_dependencies_list_ok():
    result = validate_depot_manifest(_with(VALID_OPERATOR, dependencies=[]))
    assert result.valid is True


def test_multiple_industries_ok():
    result = validate_depot_manifest(_with(VALID_OPERATOR, industries=["construction", "agriculture", "general"]))
    assert result.valid is True


# ── Missing required fields ───────────────────────────────────────────────────

def test_missing_single_field_fails():
    result = validate_depot_manifest(_without(VALID_OPERATOR, "id"))
    assert result.valid is False
    assert any("id" in err for err in result.errors)


def test_missing_multiple_fields_fails():
    result = validate_depot_manifest(_without(VALID_OPERATOR, "version", "port", "category"))
    assert result.valid is False
    # All three should be reported
    combined = " ".join(result.errors)
    assert "version" in combined
    assert "port" in combined
    assert "category" in combined


def test_all_required_fields_present():
    from cascadia.depot.manifest_validator import REQUIRED_FIELDS
    for field in REQUIRED_FIELDS:
        result = validate_depot_manifest(_without(VALID_OPERATOR, field))
        assert result.valid is False, f"Expected failure when '{field}' is missing"


# ── id validation ─────────────────────────────────────────────────────────────

def test_id_must_be_lowercase():
    result = validate_depot_manifest(_with(VALID_OPERATOR, id="Lead-Intake"))
    assert result.valid is False
    assert any("lowercase" in err for err in result.errors)


def test_id_no_spaces():
    result = validate_depot_manifest(_with(VALID_OPERATOR, id="lead intake"))
    assert result.valid is False


def test_id_hyphens_allowed():
    result = validate_depot_manifest(_with(VALID_OPERATOR, id="lead-intake-v2"))
    assert result.valid is True


def test_id_underscores_allowed():
    result = validate_depot_manifest(_with(VALID_OPERATOR, id="lead_intake"))
    assert result.valid is True


def test_id_empty_fails():
    result = validate_depot_manifest(_with(VALID_OPERATOR, id=""))
    assert result.valid is False


# ── type validation ───────────────────────────────────────────────────────────

def test_valid_types_all_pass():
    for t in VALID_TYPES:
        result = validate_depot_manifest(_with(VALID_OPERATOR, type=t))
        assert result.valid is True, f"Expected {t!r} to be valid"


def test_invalid_type_fails():
    result = validate_depot_manifest(_with(VALID_OPERATOR, type="plugin"))
    assert result.valid is False
    assert any("type" in err for err in result.errors)


# ── version validation ────────────────────────────────────────────────────────

def test_valid_semver():
    for v in ("1.0.0", "2.3.11", "0.0.1", "10.20.30"):
        result = validate_depot_manifest(_with(VALID_OPERATOR, version=v))
        assert result.valid is True, f"Expected {v!r} to be valid semver"


def test_invalid_version_fails():
    for v in ("1.0", "v1.0.0", "1.0.0.0", "latest", ""):
        result = validate_depot_manifest(_with(VALID_OPERATOR, version=v))
        assert result.valid is False, f"Expected {v!r} to fail semver check"


# ── tier_required validation ──────────────────────────────────────────────────

def test_valid_tiers_all_pass():
    for tier in VALID_TIERS:
        result = validate_depot_manifest(_with(VALID_OPERATOR, tier_required=tier))
        assert result.valid is True


def test_invalid_tier_fails():
    result = validate_depot_manifest(_with(VALID_OPERATOR, tier_required="free"))
    assert result.valid is False
    assert any("tier_required" in err for err in result.errors)


# ── price validation ──────────────────────────────────────────────────────────

def test_negative_price_fails():
    result = validate_depot_manifest(_with(VALID_OPERATOR, price=-5))
    assert result.valid is False


def test_zero_price_ok():
    result = validate_depot_manifest(_with(VALID_OPERATOR, price=0))
    assert result.valid is True


def test_string_price_fails():
    result = validate_depot_manifest(_with(VALID_OPERATOR, price="free"))
    assert result.valid is False


# ── port validation ───────────────────────────────────────────────────────────

def test_port_must_be_positive_int():
    result = validate_depot_manifest(_with(VALID_OPERATOR, port=0))
    assert result.valid is False


def test_port_string_fails():
    result = validate_depot_manifest(_with(VALID_OPERATOR, port="8101"))
    assert result.valid is False


def test_port_outside_range_warns():
    result = validate_depot_manifest(_with(VALID_OPERATOR, port=3000))
    assert result.valid is True  # warning, not error
    assert any("range" in w for w in result.warnings)


# ── category validation ───────────────────────────────────────────────────────

def test_valid_categories_all_pass():
    for cat in VALID_CATEGORIES:
        result = validate_depot_manifest(_with(VALID_OPERATOR, category=cat))
        assert result.valid is True


def test_invalid_category_fails():
    result = validate_depot_manifest(_with(VALID_OPERATOR, category="blockchain"))
    assert result.valid is False


# ── installed_by_default must be False ───────────────────────────────────────

def test_installed_by_default_false_required():
    result = validate_depot_manifest(_with(VALID_OPERATOR, installed_by_default=True))
    assert result.valid is False
    assert any("installed_by_default" in err for err in result.errors)


def test_installed_by_default_non_bool_fails():
    result = validate_depot_manifest(_with(VALID_OPERATOR, installed_by_default="false"))
    assert result.valid is False


# ── dependencies must be list of strings ─────────────────────────────────────

def test_dependencies_not_list_fails():
    result = validate_depot_manifest(_with(VALID_OPERATOR, dependencies="nats-py"))
    assert result.valid is False


def test_dependencies_list_of_non_strings_fails():
    result = validate_depot_manifest(_with(VALID_OPERATOR, dependencies=[1, 2]))
    assert result.valid is False


# ── industries must be non-empty list ────────────────────────────────────────

def test_empty_industries_fails():
    result = validate_depot_manifest(_with(VALID_OPERATOR, industries=[]))
    assert result.valid is False


def test_industries_not_list_fails():
    result = validate_depot_manifest(_with(VALID_OPERATOR, industries="general"))
    assert result.valid is False


# ── connector-specific ────────────────────────────────────────────────────────

def test_connector_warns_without_auth_type():
    data = _without(VALID_CONNECTOR, "auth_type")
    result = validate_depot_manifest(data)
    assert result.valid is True  # warning, not error
    assert any("auth_type" in w for w in result.warnings)


def test_connector_invalid_auth_type_fails():
    result = validate_depot_manifest(_with(VALID_CONNECTOR, auth_type="magic_token"))
    assert result.valid is False


def test_connector_valid_auth_types():
    for auth in ("oauth2", "api_key", "bearer", "hmac", "none"):
        result = validate_depot_manifest(_with(VALID_CONNECTOR, auth_type=auth))
        assert result.valid is True


# ── unknown fields warn only ──────────────────────────────────────────────────

def test_unknown_field_warns_not_errors():
    result = validate_depot_manifest(_with(VALID_OPERATOR, future_field="some_value"))
    assert result.valid is True
    assert any("Unknown" in w for w in result.warnings)


# ── description length ────────────────────────────────────────────────────────

def test_long_description_warns():
    long_desc = "A" * 300
    result = validate_depot_manifest(_with(VALID_OPERATOR, description=long_desc))
    assert result.valid is True
    assert any("280" in w for w in result.warnings)


# ── file-based validator ──────────────────────────────────────────────────────

def test_validate_file_not_found():
    result = validate_depot_manifest_file("/nonexistent/manifest.json")
    assert result.valid is False
    assert any("not found" in err.lower() for err in result.errors)


def test_validate_file_invalid_json(tmp_path):
    f = tmp_path / "manifest.json"
    f.write_text("not { valid json")
    result = validate_depot_manifest_file(f)
    assert result.valid is False
    assert any("JSON" in err for err in result.errors)


def test_validate_file_valid_manifest(tmp_path):
    f = tmp_path / "manifest.json"
    f.write_text(json.dumps(VALID_OPERATOR))
    result = validate_depot_manifest_file(f)
    assert result.valid is True


def test_validate_file_invalid_manifest(tmp_path):
    bad = _with(VALID_OPERATOR, installed_by_default=True, type="plugin")
    f = tmp_path / "manifest.json"
    f.write_text(json.dumps(bad))
    result = validate_depot_manifest_file(f)
    assert result.valid is False
    assert len(result.errors) >= 2


# ── Permission manifest fields ────────────────────────────────────────────────

def test_valid_risk_levels_pass():
    from cascadia.depot.manifest_validator import VALID_RISK_LEVELS
    for level in VALID_RISK_LEVELS:
        result = validate_depot_manifest(_with(VALID_OPERATOR, risk_level=level))
        assert result.valid is True, f"Expected risk_level={level!r} to pass"


def test_invalid_risk_level_fails():
    result = validate_depot_manifest(_with(VALID_OPERATOR, risk_level="extreme"))
    assert result.valid is False
    assert any("risk_level" in err for err in result.errors)


def test_critical_risk_level_now_valid():
    # "critical" was missing from VALID_RISK_LEVELS before Sprint 2B Phase 2.
    # Verify it is now accepted.
    result = validate_depot_manifest(_with(VALID_OPERATOR, risk_level="critical"))
    assert result.valid is True


def test_missing_risk_level_fails():
    result = validate_depot_manifest(_without(VALID_OPERATOR, "risk_level"))
    assert result.valid is False


def test_permissions_empty_list_ok():
    result = validate_depot_manifest(_with(VALID_OPERATOR, permissions=[]))
    assert result.valid is True


def test_permissions_not_list_fails():
    result = validate_depot_manifest(_with(VALID_OPERATOR, permissions="read_crm"))
    assert result.valid is False
    assert any("permissions" in err for err in result.errors)


def test_requires_approval_for_list_of_strings():
    result = validate_depot_manifest(_with(VALID_OPERATOR, requires_approval_for=["send_email", "write_crm"]))
    assert result.valid is True


def test_requires_approval_for_not_list_fails():
    result = validate_depot_manifest(_with(VALID_OPERATOR, requires_approval_for="send_email"))
    assert result.valid is False


def test_data_access_list_ok():
    result = validate_depot_manifest(_with(VALID_OPERATOR, data_access=["contacts", "orders"]))
    assert result.valid is True


def test_writes_external_systems_bool_required():
    result = validate_depot_manifest(_with(VALID_OPERATOR, writes_external_systems="yes"))
    assert result.valid is False
    assert any("writes_external_systems" in err for err in result.errors)


def test_network_access_bool_required():
    result = validate_depot_manifest(_with(VALID_OPERATOR, network_access=1))
    assert result.valid is False
    assert any("network_access" in err for err in result.errors)


def test_missing_permission_fields_fails():
    for f in ("risk_level", "permissions", "requires_approval_for", "data_access",
              "writes_external_systems", "network_access"):
        result = validate_depot_manifest(_without(VALID_OPERATOR, f))
        assert result.valid is False, f"Expected failure when '{f}' is missing"


def test_high_risk_with_external_writes_passes():
    result = validate_depot_manifest(_with(
        VALID_OPERATOR,
        risk_level="high",
        writes_external_systems=True,
        network_access=True,
        requires_approval_for=["send_email", "write_crm", "delete_record"],
    ))
    assert result.valid is True
