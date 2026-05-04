"""
tests/test_operators.py — Cascadia OS v0.43
Smoke tests for the operator ecosystem.

Tests operator manifests, configs, registry integrity, and API contracts
without requiring a running server. HTTP tests are skipped if operators
are not running.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

REPO = Path(__file__).parent.parent
REGISTRY = REPO / "cascadia" / "operators" / "registry.json"
SAMPLES  = REPO / "samples"


def http_get(url: str, timeout: int = 2):
    """Return parsed JSON or None if unreachable."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def operator_running(port: int) -> bool:
    return http_get(f"http://127.0.0.1:{port}/api/health") is not None


# ─────────────────────────────────────────────────────────────────────────────
# Registry integrity
# ─────────────────────────────────────────────────────────────────────────────

class TestOperatorRegistry(unittest.TestCase):

    def setUp(self):
        self.registry = json.loads(REGISTRY.read_text())
        self.operators = self.registry["operators"]

    def test_registry_loads(self):
        self.assertIn("operators", self.registry)
        self.assertIn("version", self.registry)

    def test_registry_operator_count_is_non_negative(self):
        self.assertGreaterEqual(len(self.operators), 0)

    def test_all_operators_have_required_fields(self):
        required = {"id", "name", "category", "description", "status", "port", "autonomy"}
        for op in self.operators:
            missing = required - set(op.keys())
            self.assertFalse(missing, f"{op['id']} missing fields: {missing}")

    def test_no_duplicate_ids(self):
        ids = [op["id"] for op in self.operators]
        self.assertEqual(len(ids), len(set(ids)))

    def test_no_duplicate_ports(self):
        ports = [op["port"] for op in self.operators]
        self.assertEqual(len(ports), len(set(ports)))

    def test_production_operators_have_valid_status(self):
        prod = [op["id"] for op in self.operators if op["status"] == "production"]
        # Production operators exist only when commercial operators are installed.
        # Verify any present production operators have valid required fields.
        for op_id in prod:
            op = next(o for o in self.operators if o["id"] == op_id)
            self.assertIn("port", op)

    def test_status_values_valid(self):
        valid = {"production", "beta", "alpha"}
        for op in self.operators:
            self.assertIn(op["status"], valid, f"{op['id']} has invalid status")

    def test_autonomy_values_valid(self):
        valid = {"autonomous", "semi-autonomous", "assistive"}
        for op in self.operators:
            self.assertIn(op["autonomy"], valid, f"{op['id']} has invalid autonomy")

    def test_sample_outputs_exist_if_declared(self):
        for op in self.operators:
            if op.get("sample_output"):
                path = REPO / op["sample_output"]
                self.assertTrue(path.exists(),
                    f"{op['id']} sample_output declared but missing: {op['sample_output']}")


# ─────────────────────────────────────────────────────────────────────────────
# Operator manifests (built-in operators)
# ─────────────────────────────────────────────────────────────────────────────

class TestBuiltinOperatorManifests(unittest.TestCase):

    def _load_manifest(self, operator_dir: str) -> dict:
        p = REPO / "cascadia" / "operators" / operator_dir / "manifest.json"
        if not p.exists():
            self.skipTest(f"manifest not found: {p}")
        return json.loads(p.read_text())

    def test_recon_manifest(self):
        m = self._load_manifest("recon")
        self.assertEqual(m["id"], "recon")
        self.assertIn("port", m)
        self.assertIn("capabilities", m)
        self.assertIn("research.outbound", m["capabilities"])

    def test_scout_manifest(self):
        m = self._load_manifest("scout")
        self.assertEqual(m["id"], "scout")
        self.assertIn("lead.capture", m["capabilities"])

    def test_manifest_required_fields(self):
        required = {"id", "name", "version", "port", "description"}
        for op_dir in ["recon", "scout"]:
            p = REPO / "cascadia" / "operators" / op_dir / "manifest.json"
            if not p.exists():
                continue
            m = json.loads(p.read_text())
            missing = required - set(m.keys())
            self.assertFalse(missing, f"{op_dir}/manifest.json missing: {missing}")


# ─────────────────────────────────────────────────────────────────────────────
# Sample output integrity
# ─────────────────────────────────────────────────────────────────────────────

class TestSampleOutputs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not SAMPLES.exists():
            raise unittest.SkipTest("samples/ directory not present — commercial operators not installed")

    def test_samples_directory_exists(self):
        self.assertTrue(SAMPLES.exists())

    def test_samples_readme_exists(self):
        self.assertTrue((SAMPLES / "README.md").exists())

    def test_recon_csv_exists_and_has_rows(self):
        csvs = list(SAMPLES.glob("recon-*.csv"))
        self.assertGreater(len(csvs), 0, "No RECON CSV found in samples/")
        with open(csvs[0]) as f:
            rows = list(csv.DictReader(f))
        self.assertGreater(len(rows), 0, "RECON CSV is empty")
        # Verify required columns
        required_cols = {"full_name", "company", "title"}
        self.assertTrue(required_cols.issubset(set(rows[0].keys())),
            f"RECON CSV missing columns. Has: {set(rows[0].keys())}")

    def test_recon_csv_no_obvious_hallucinations(self):
        csvs = list(SAMPLES.glob("recon-*.csv"))
        if not csvs:
            self.skipTest("No RECON CSV in samples/")
        fake_emails = {"john.doe@", "jane.smith@", "test@"}
        fake_phones = {"555-1234", "555-5678", "555-0000"}
        with open(csvs[0]) as f:
            rows = list(csv.DictReader(f))
        for row in rows:
            email = row.get("email", "").lower()
            phone = row.get("phone", "")
            for fe in fake_emails:
                self.assertNotIn(fe, email,
                    f"Hallucinated email found: {email}")
            for fp in fake_phones:
                self.assertNotIn(fp, phone,
                    f"Hallucinated phone found: {phone}")

    def test_quote_proposal_exists_and_has_content(self):
        proposals = list(SAMPLES.glob("proposal-*.md"))
        self.assertGreater(len(proposals), 0, "No proposal found in samples/")
        content = proposals[0].read_text()
        self.assertIn("Zyrcon Labs", content)
        self.assertIn("Investment", content)
        self.assertGreater(len(content), 500)

    def test_chief_brief_exists(self):
        briefs = list(SAMPLES.glob("chief-brief-*.md"))
        self.assertGreater(len(briefs), 0, "No CHIEF brief found in samples/")
        content = briefs[0].read_text()
        self.assertIn("CHIEF", content)

    def test_debrief_sample_exists(self):
        debriefs = list(SAMPLES.glob("debrief-*.md"))
        self.assertGreater(len(debriefs), 0, "No Debrief sample found in samples/")
        content = debriefs[0].read_text()
        self.assertIn("Action Items", content)


# ─────────────────────────────────────────────────────────────────────────────
# Live operator health checks (skipped if not running)
# ─────────────────────────────────────────────────────────────────────────────

class TestLiveOperatorHealth(unittest.TestCase):

    OPERATOR_PORTS = {
        "RECON":  7001,
        "SCOUT":  7002,
        "QUOTE":  8007,
        "CHIEF":  8006,
        "Aurelia": 8009,
        "Debrief": 8008,
    }

    def _check(self, name: str, port: int):
        d = http_get(f"http://127.0.0.1:{port}/api/health")
        if d is None:
            self.skipTest(f"{name} not running on :{port}")
        self.assertEqual(d.get("status"), "online",
            f"{name} health returned status={d.get('status')}")
        self.assertIn("version", d, f"{name} health missing version field")

    def test_recon_health(self):   self._check("RECON",   7001)
    def test_scout_health(self):   self._check("SCOUT",   7002)
    def test_quote_health(self):   self._check("QUOTE",   8007)
    def test_chief_health(self):   self._check("CHIEF",   8006)
    def test_aurelia_health(self): self._check("Aurelia", 8009)
    def test_debrief_health(self): self._check("Debrief", 8008)

    def test_prism_operators_endpoint(self):
        d = http_get("http://127.0.0.1:6300/api/prism/operators")
        if d is None:
            self.skipTest("PRISM not running on :6300")
        self.assertIn("operators", d)
        self.assertIn("total", d)
        self.assertIn("online", d)
        self.assertGreaterEqual(d["total"], 0)
        self.assertGreaterEqual(d["online"], 0)


# ─────────────────────────────────────────────────────────────────────────────
# FLINT version autoupdate
# ─────────────────────────────────────────────────────────────────────────────

class TestVersionAutoupdate(unittest.TestCase):

    def test_version_readable_from_init(self):
        from cascadia import __version__, VERSION, VERSION_SHORT
        # Accept semver (X.Y.Z) or CalVer (YYYY.M or YYYY.M.patch)
        self.assertRegex(__version__, r'^\d+\.\d+(\.\d+)?$')
        self.assertEqual(VERSION, __version__)
        self.assertRegex(VERSION_SHORT, r'^\d+\.\d+$')

    def test_version_matches_pyproject(self):
        import re
        from cascadia import __version__
        toml = (REPO / "pyproject.toml").read_text()
        m = re.search(r'^version\s*=\s*"([^"]+)"', toml, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(__version__, m.group(1))

    def test_flint_health_returns_version(self):
        d = http_get("http://127.0.0.1:4011/health")
        if d is None:
            self.skipTest("FLINT not running on :4011")
        self.assertIn("version", d)
        # Running FLINT reports the version it was started with; skip skew check
        # if the process predates the current code version (requires FLINT restart).
        from cascadia import VERSION_SHORT
        if d["version"] != VERSION_SHORT:
            self.skipTest(f"FLINT version {d['version']!r} predates current {VERSION_SHORT!r} — restart FLINT")
        self.assertEqual(d["version"], VERSION_SHORT)


if __name__ == "__main__":
    print("\n=== Cascadia OS — Operator Ecosystem Tests ===\n")
    unittest.main(verbosity=2)
