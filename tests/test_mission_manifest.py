"""Tests for cascadia.missions.manifest — MissionManifest loader and validator."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from cascadia.missions.manifest import MissionManifest, MissionManifestError

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "missions"
VALID_FIXTURE_DIR = FIXTURES_ROOT / "test_growth_desk"


def _load_fixture() -> dict:
    """Return a fresh copy of the valid test_growth_desk manifest dict."""
    return json.loads((VALID_FIXTURE_DIR / "mission.json").read_text())


class TestMissionManifest(unittest.TestCase):

    def setUp(self):
        self.mm = MissionManifest()
        self.valid = _load_fixture()

    # ── Positive cases ────────────────────────────────────────────────────────

    def test_valid_fixture_manifest_passes(self):
        errors = self.mm.validate(self.valid, base_path=str(VALID_FIXTURE_DIR))
        self.assertEqual(errors, [], f"Expected no errors, got: {errors}")

    def test_referenced_mobile_schema_file_exists_passes(self):
        errors = self.mm.validate(self.valid, base_path=str(VALID_FIXTURE_DIR))
        self.assertEqual(errors, [])

    # ── Missing / wrong type ──────────────────────────────────────────────────

    def test_missing_type_fails(self):
        m = copy.deepcopy(self.valid)
        del m["type"]
        errors = self.mm.validate(m)
        self.assertTrue(any("type" in e for e in errors))

    def test_wrong_type_fails(self):
        m = copy.deepcopy(self.valid)
        m["type"] = "operator"
        errors = self.mm.validate(m)
        self.assertTrue(any("type" in e for e in errors))

    # ── Required scalar fields ────────────────────────────────────────────────

    def test_missing_id_fails(self):
        m = copy.deepcopy(self.valid)
        del m["id"]
        errors = self.mm.validate(m)
        self.assertTrue(any("id" in e for e in errors))

    # ── Operators block ───────────────────────────────────────────────────────

    def test_missing_operators_required_fails(self):
        m = copy.deepcopy(self.valid)
        del m["operators"]["required"]
        errors = self.mm.validate(m)
        self.assertTrue(any("operators.required" in e for e in errors))

    # ── Schema dict checks ────────────────────────────────────────────────────

    def test_missing_mobile_schema_field_fails(self):
        m = copy.deepcopy(self.valid)
        m["mobile"] = {}   # dict present but no "schema" key
        errors = self.mm.validate(m)
        self.assertTrue(any("mobile.schema" in e for e in errors))

    def test_missing_prism_schema_field_fails(self):
        m = copy.deepcopy(self.valid)
        m["prism"] = {"nav_label": "Growth"}  # no "schema" key
        errors = self.mm.validate(m)
        self.assertTrue(any("prism.schema" in e for e in errors))

    def test_missing_billing_fails(self):
        m = copy.deepcopy(self.valid)
        del m["billing"]
        errors = self.mm.validate(m)
        self.assertTrue(any("billing" in e for e in errors))

    def test_missing_limits_fails(self):
        m = copy.deepcopy(self.valid)
        del m["limits"]
        errors = self.mm.validate(m)
        self.assertTrue(any("limits" in e for e in errors))

    # ── File existence checks (with base_path) ────────────────────────────────

    def test_referenced_mobile_schema_file_missing_fails(self):
        m = copy.deepcopy(self.valid)
        m["mobile"]["schema"] = "ui/does_not_exist.json"
        errors = self.mm.validate(m, base_path=str(VALID_FIXTURE_DIR))
        self.assertTrue(any("mobile.schema file not found" in e for e in errors))

    def test_referenced_workflow_file_missing_fails(self):
        m = copy.deepcopy(self.valid)
        m["workflows"]["ghost_workflow"] = "workflows/ghost.json"
        errors = self.mm.validate(m, base_path=str(VALID_FIXTURE_DIR))
        self.assertTrue(any("workflow file not found" in e for e in errors))

    # ── DEPOT validator backward compat ───────────────────────────────────────

    def test_existing_operator_manifest_still_validates(self):
        from cascadia.depot.manifest_validator import validate_depot_manifest
        op = {
            "id": "test-op",
            "name": "Test Operator",
            "type": "operator",
            "version": "1.0.0",
            "description": "Minimal operator for compat check.",
            "author": "Zyrcon Labs",
            "price": 0,
            "tier_required": "lite",
            "port": 8200,
            "entry_point": "server.py",
            "dependencies": [],
            "install_hook": "install.sh",
            "uninstall_hook": "uninstall.sh",
            "category": "operations",
            "industries": ["general"],
            "installed_by_default": False,
            "safe_to_uninstall": True,
            "risk_level": "low",
            "permissions": [],
            "requires_approval_for": [],
            "data_access": [],
            "writes_external_systems": False,
            "network_access": False,
        }
        result = validate_depot_manifest(op)
        self.assertTrue(result.valid, f"Operator validation broke: {result.errors}")


if __name__ == "__main__":
    unittest.main()
