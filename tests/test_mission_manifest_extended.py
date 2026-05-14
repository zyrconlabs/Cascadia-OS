"""Tests for MissionManifest Rules 22–33 (Sprint 2B Phase 3)."""
from __future__ import annotations

import copy
import json
import unittest
import warnings
from pathlib import Path

from cascadia.missions.manifest import MissionManifest, MissionManifestError

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "missions"
VALID_SIGNED_DIR = FIXTURES_ROOT / "valid_signed"


def _load_valid_signed() -> dict:
    return json.loads((VALID_SIGNED_DIR / "mission.json").read_text())


class TestExistingRulesRegression(unittest.TestCase):
    """Rules 1–21 still pass for existing bare mission manifests."""

    def setUp(self):
        self.mm = MissionManifest()
        self.valid = _load_valid_signed()

    def test_valid_signed_fixture_passes_all_rules(self):
        errors = self.mm.validate(self.valid)
        self.assertEqual(errors, [], f"Expected no errors: {errors}")

    def test_valid_signed_fixture_with_base_path_passes(self):
        errors = self.mm.validate(self.valid, base_path=str(VALID_SIGNED_DIR))
        # Prism/mobile schema and workflow files may not exist in fixture dir —
        # only assert no package-mode errors
        package_errors = [e for e in errors if "capabilities" in e or "runtime" in e]
        self.assertEqual(package_errors, [])


class TestRule22Capabilities(unittest.TestCase):

    def setUp(self):
        self.mm = MissionManifest()
        self.valid = _load_valid_signed()

    def test_valid_capabilities_passes(self):
        errors = self.mm.validate(self.valid)
        self.assertEqual(errors, [])

    def test_empty_capabilities_passes(self):
        m = copy.deepcopy(self.valid)
        m["capabilities"] = []
        m["requires_approval"] = []
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])

    def test_unknown_capability_name_fails(self):
        m = copy.deepcopy(self.valid)
        m["capabilities"] = ["crm.read", "send_email"]  # send_email is not in registry
        errors = self.mm.validate(m)
        self.assertTrue(any("unknown capability" in e for e in errors), errors)

    def test_capability_not_in_registry_fails(self):
        m = copy.deepcopy(self.valid)
        m["capabilities"] = ["weather.fetch"]  # not in CAPABILITY_REGISTRY
        errors = self.mm.validate(m)
        self.assertTrue(any("unknown capability" in e for e in errors), errors)

    def test_capabilities_not_list_fails(self):
        m = copy.deepcopy(self.valid)
        m["capabilities"] = "crm.read"
        errors = self.mm.validate(m)
        self.assertTrue(any("capabilities" in e for e in errors), errors)

    def test_missing_capability_fixture_fails(self):
        data = json.loads((Path(__file__).parent / "fixtures" / "missions" / "missing_capability" / "mission.json").read_text())
        errors = self.mm.validate(data)
        self.assertTrue(any("unknown capability" in e for e in errors), errors)


class TestRule23RequiresApproval(unittest.TestCase):

    def setUp(self):
        self.mm = MissionManifest()
        self.valid = _load_valid_signed()

    def test_valid_requires_approval_passes(self):
        errors = self.mm.validate(self.valid)
        self.assertEqual(errors, [])

    def test_requires_approval_subset_of_capabilities_passes(self):
        m = copy.deepcopy(self.valid)
        m["capabilities"] = ["crm.read", "email.send"]
        m["requires_approval"] = ["email.send"]
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])

    def test_requires_approval_not_in_capabilities_fails(self):
        m = copy.deepcopy(self.valid)
        m["capabilities"] = ["crm.read"]
        m["requires_approval"] = ["email.send"]  # not in capabilities
        errors = self.mm.validate(m)
        self.assertTrue(any("requires_approval" in e for e in errors), errors)

    def test_requires_approval_empty_list_ok(self):
        m = copy.deepcopy(self.valid)
        m["requires_approval"] = []
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])


class TestRule24RiskLevel(unittest.TestCase):

    def setUp(self):
        self.mm = MissionManifest()
        self.valid = _load_valid_signed()

    def test_risk_level_matches_capabilities(self):
        m = copy.deepcopy(self.valid)
        m["capabilities"] = ["email.send"]  # medium
        m["risk_level"] = "medium"
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])

    def test_risk_level_too_low_fails(self):
        m = copy.deepcopy(self.valid)
        m["capabilities"] = ["email.send"]  # medium
        m["risk_level"] = "low"  # below medium
        errors = self.mm.validate(m)
        self.assertTrue(any("risk_level" in e for e in errors), errors)

    def test_risk_level_higher_than_capabilities_passes(self):
        m = copy.deepcopy(self.valid)
        m["capabilities"] = ["crm.read"]  # low
        m["requires_approval"] = []       # reset — email.send no longer listed
        m["risk_level"] = "high"  # declaring higher than required is allowed
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])

    def test_risk_level_invalid_value_fails(self):
        m = copy.deepcopy(self.valid)
        m["risk_level"] = "extreme"
        errors = self.mm.validate(m)
        self.assertTrue(any("risk_level" in e for e in errors), errors)

    def test_risk_level_critical_with_vault_write_passes(self):
        m = copy.deepcopy(self.valid)
        m["capabilities"] = ["vault.write"]  # critical
        m["requires_approval"] = []
        m["risk_level"] = "critical"
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])

    def test_risk_level_too_low_fixture(self):
        data = json.loads((Path(__file__).parent / "fixtures" / "missions" / "risk_level_too_low" / "mission.json").read_text())
        errors = self.mm.validate(data)
        self.assertTrue(any("risk_level" in e for e in errors), errors)

    def test_empty_capabilities_any_risk_level_ok(self):
        m = copy.deepcopy(self.valid)
        m["capabilities"] = []
        m["requires_approval"] = []
        m["risk_level"] = "low"
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])


class TestRule25Runtime(unittest.TestCase):

    def setUp(self):
        self.mm = MissionManifest()
        self.valid = _load_valid_signed()

    def test_runtime_server_passes(self):
        m = copy.deepcopy(self.valid)
        m["runtime"] = "server"
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])

    def test_runtime_mobile_passes(self):
        m = copy.deepcopy(self.valid)
        m["runtime"] = "mobile"
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])

    def test_runtime_both_passes(self):
        m = copy.deepcopy(self.valid)
        m["runtime"] = "both"
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])

    def test_missing_runtime_fails(self):
        m = copy.deepcopy(self.valid)
        del m["runtime"]
        errors = self.mm.validate(m)
        self.assertTrue(any("runtime" in e for e in errors), errors)

    def test_invalid_runtime_fails(self):
        m = copy.deepcopy(self.valid)
        m["runtime"] = "cloud"
        errors = self.mm.validate(m)
        self.assertTrue(any("runtime" in e for e in errors), errors)


class TestRule26Author(unittest.TestCase):

    def setUp(self):
        self.mm = MissionManifest()
        self.valid = _load_valid_signed()

    def test_author_present_passes(self):
        errors = self.mm.validate(self.valid)
        self.assertEqual(errors, [])

    def test_missing_author_fails(self):
        m = copy.deepcopy(self.valid)
        del m["author"]
        errors = self.mm.validate(m)
        self.assertTrue(any("author" in e for e in errors), errors)

    def test_empty_author_fails(self):
        m = copy.deepcopy(self.valid)
        m["author"] = ""
        errors = self.mm.validate(m)
        self.assertTrue(any("author" in e for e in errors), errors)


class TestRules27to31SigningFields(unittest.TestCase):

    def setUp(self):
        self.mm = MissionManifest()
        self.valid = _load_valid_signed()

    def test_all_signing_fields_present_passes(self):
        errors = self.mm.validate(self.valid)
        self.assertEqual(errors, [])

    def test_missing_signed_by_fails(self):
        m = copy.deepcopy(self.valid)
        del m["signed_by"]
        errors = self.mm.validate(m)
        self.assertTrue(any("signed_by" in e for e in errors), errors)

    def test_wrong_signature_algorithm_fails(self):
        m = copy.deepcopy(self.valid)
        m["signature_algorithm"] = "RSA"
        errors = self.mm.validate(m)
        self.assertTrue(any("signature_algorithm" in e for e in errors), errors)

    def test_invalid_key_id_fails(self):
        m = copy.deepcopy(self.valid)
        m["key_id"] = "INVALID KEY"
        errors = self.mm.validate(m)
        self.assertTrue(any("key_id" in e for e in errors), errors)

    def test_valid_key_id_passes(self):
        m = copy.deepcopy(self.valid)
        m["key_id"] = "zyrcon-2026-q2"
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])

    def test_invalid_package_digest_fails(self):
        m = copy.deepcopy(self.valid)
        m["package_digest"] = "notahash"
        errors = self.mm.validate(m)
        self.assertTrue(any("package_digest" in e for e in errors), errors)

    def test_package_digest_wrong_prefix_fails(self):
        m = copy.deepcopy(self.valid)
        m["package_digest"] = "md5:" + "a" * 32
        errors = self.mm.validate(m)
        self.assertTrue(any("package_digest" in e for e in errors), errors)

    def test_valid_package_digest_passes(self):
        m = copy.deepcopy(self.valid)
        m["package_digest"] = "sha256:" + "a" * 64
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])

    def test_files_not_list_fails(self):
        m = copy.deepcopy(self.valid)
        m["files"] = "nope"
        errors = self.mm.validate(m)
        self.assertTrue(any("files" in e for e in errors), errors)

    def test_files_entry_missing_sha256_fails(self):
        m = copy.deepcopy(self.valid)
        m["files"] = [{"path": "workflows/main.json"}]
        errors = self.mm.validate(m)
        self.assertTrue(any("sha256" in e for e in errors), errors)

    def test_files_entry_invalid_sha256_fails(self):
        m = copy.deepcopy(self.valid)
        m["files"] = [{"path": "workflows/main.json", "sha256": "UPPERCASE" + "a" * 55}]
        errors = self.mm.validate(m)
        self.assertTrue(any("sha256" in e for e in errors), errors)

    def test_files_entry_path_with_dotdot_fails(self):
        m = copy.deepcopy(self.valid)
        m["files"] = [{"path": "../escape.json", "sha256": "a" * 64}]
        errors = self.mm.validate(m)
        self.assertTrue(any("path" in e for e in errors), errors)

    def test_files_entry_negative_size_bytes_fails(self):
        m = copy.deepcopy(self.valid)
        m["files"] = [{"path": "a.json", "sha256": "a" * 64, "size_bytes": -1}]
        errors = self.mm.validate(m)
        self.assertTrue(any("size_bytes" in e for e in errors), errors)

    def test_files_entry_valid_with_size_bytes_passes(self):
        m = copy.deepcopy(self.valid)
        m["files"] = [{"path": "a.json", "sha256": "a" * 64, "size_bytes": 100}]
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])

    def test_empty_files_list_passes(self):
        m = copy.deepcopy(self.valid)
        m["files"] = []
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])


class TestRule32TierAlignment(unittest.TestCase):

    def setUp(self):
        self.mm = MissionManifest()
        self.valid = _load_valid_signed()

    def test_tier_lite_accepted(self):
        m = copy.deepcopy(self.valid)
        m["tier_required"] = "lite"
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])

    def test_tier_pro_accepted(self):
        m = copy.deepcopy(self.valid)
        m["tier_required"] = "pro"
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])

    def test_tier_business_accepted(self):
        m = copy.deepcopy(self.valid)
        m["tier_required"] = "business"
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])

    def test_tier_enterprise_accepted(self):
        m = copy.deepcopy(self.valid)
        m["tier_required"] = "enterprise"
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])

    def test_tier_free_deprecated_alias(self):
        m = copy.deepcopy(self.valid)
        m["tier_required"] = "free"
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            errors = self.mm.validate(m)
        self.assertEqual(errors, [], "free is a deprecated alias, not an error")
        deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        self.assertGreaterEqual(len(deprecation_warnings), 1)

    def test_tier_invalid_fails(self):
        m = copy.deepcopy(self.valid)
        m["tier_required"] = "starter"
        errors = self.mm.validate(m)
        self.assertTrue(any("tier_required" in e for e in errors), errors)


class TestRule33DependencyFormat(unittest.TestCase):

    def setUp(self):
        self.mm = MissionManifest()
        self.valid = _load_valid_signed()

    def test_bare_operator_id_passes(self):
        m = copy.deepcopy(self.valid)
        m["operators"]["required"] = ["scout", "recon"]
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])

    def test_pinned_exact_version_passes(self):
        m = copy.deepcopy(self.valid)
        m["operators"]["required"] = ["scout@1.2.0"]
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])

    def test_pinned_minimum_version_passes(self):
        m = copy.deepcopy(self.valid)
        m["operators"]["required"] = ["scout@>=1.2.0"]
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])

    def test_invalid_pin_syntax_fails(self):
        m = copy.deepcopy(self.valid)
        m["operators"]["required"] = ["scout@invalid"]
        errors = self.mm.validate(m)
        self.assertTrue(any("operators.required" in e for e in errors), errors)

    def test_uppercase_operator_id_fails(self):
        m = copy.deepcopy(self.valid)
        m["operators"]["required"] = ["Scout"]
        errors = self.mm.validate(m)
        self.assertTrue(any("operators.required" in e for e in errors), errors)

    def test_optional_operators_format_validated(self):
        m = copy.deepcopy(self.valid)
        m["operators"]["optional"] = ["recon@bad"]
        errors = self.mm.validate(m)
        self.assertTrue(any("operators.optional" in e for e in errors), errors)

    def test_connector_id_format_validated(self):
        m = copy.deepcopy(self.valid)
        m["connectors"]["required"] = ["email@bad-pin"]
        errors = self.mm.validate(m)
        self.assertTrue(any("connectors.required" in e for e in errors), errors)

    def test_valid_connector_with_version_passes(self):
        m = copy.deepcopy(self.valid)
        m["connectors"]["optional"] = ["salesforce@>=2.0.0"]
        errors = self.mm.validate(m)
        self.assertEqual(errors, [])

    def test_rule33_applies_to_non_package_manifests(self):
        """Rule 33 always applies — even manifests without signature."""
        import json
        bare = json.loads((Path(__file__).parent / "fixtures" / "missions" / "test_growth_desk" / "mission.json").read_text())
        # test_growth_desk uses ["brief", "social", "chief"] — all valid format
        errors = self.mm.validate(bare)
        dep_errors = [e for e in errors if "operators" in e or "connectors" in e]
        self.assertEqual(dep_errors, [], dep_errors)


if __name__ == "__main__":
    unittest.main()
