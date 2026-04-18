#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# Cascadia OS — polish pass
# 1. CHANGELOG v0.34 full entry
# 2. Operator smoke tests (test_operators.py)
# 3. GitHub release tag v0.34.0
#
# Run from repo root: bash apply-polish.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -e
REPO="$(cd "$(dirname "$0")" && pwd)"

if [[ ! -f "$REPO/cascadia/kernel/flint.py" ]]; then
  echo "ERROR: Run from inside your cascadia-os repo."
  exit 1
fi

echo "Applying polish pass to: $REPO"
echo ""

# ── 1. CHANGELOG ──────────────────────────────────────────────────────────────
echo "[1/3] Writing CHANGELOG v0.34 full entry"
python3 - <<'PYEOF'
import pathlib

p = pathlib.Path("CHANGELOG.md")
src = p.read_text()

new_entry = '''# Changelog

---

## v0.34.0 — 2026-04-18

### Summary
Major operator ecosystem release. Five operators now generate real output locally
using Qwen 3B via llama.cpp — no cloud API required. Full stack starts in one
command. SwiftBar menu bar plugin provides live system status and one-click
controls. PRISM dashboard shows live operator cards. Single-source version
management from pyproject.toml.

### Platform

**`cascadia/kernel/flint.py`**
- LLM proxy added — `POST /v1/chat/completions` on port 4011
  Translates OpenAI-compatible format to any local or cloud LLM backend.
  Zero new dependencies — pure stdlib urllib. All operators route through FLINT.
- `/api/flint/status` now returns `components_healthy` and `components_total`
  counts so demo.sh and SwiftBar can display `11/11` format
- `/health` now includes `version` field
- Version strings removed from source — all read from `cascadia/__init__.py`

**`cascadia/__init__.py`**
- New central version reader — parses `pyproject.toml` at import time
- Exposes `__version__`, `VERSION`, `VERSION_SHORT`
- To bump version: edit `pyproject.toml` only — everything updates on restart

**`cascadia/operators/recon/recon_worker.py`**
- Wired to Qwen 3B via FLINT proxy (`zyrcon-ai-v0.1`)
- Hallucination filter added to `validate_rows()` — rejects placeholder
  contacts (john.doe@, 555-1234, generic LinkedIn URLs) before writing to CSV
- Updated search queries for better Houston warehouse contact yield

**`cascadia/dashboard/prism.py` + `prism.html`**
- `/api/prism/operators` endpoint — reads `registry.json`, pings each
  operator's health endpoint, returns live status for all 8 operators
- Operator cards section added to PRISM sidebar — live status, category
  color coding, production/beta badges, clickable links to dashboards
- Polls every 15 seconds independently of component refresh

**`config.json`**
- `llm` block added: `provider`, `url`, `model`, `api_key`
- Default: llama.cpp on `http://127.0.0.1:8080`, model `zyrcon-ai-v0.1`

**`pyproject.toml`**
- Version bumped to `0.34.0`

### Operators

**RECON** (port 7001) — production
- Autonomous outbound lead research, Houston warehouse contacts
- 283 cycles run, 67+ leads collected across multiple sessions
- CSV output with hallucination filtering active
- Sample output: `samples/recon-houston-warehouse-leads-2026-04-18.csv`

**SCOUT** (port 7002) — production
- Inbound lead capture and qualification chat widget
- Port changed from 7000 (macOS Control Center conflict) to 7002
- Wired to FLINT LLM proxy

**QUOTE** (port 8007) — production
- RFQ → professional proposal in under 30 seconds
- Tested live: Gulf Coast Logistics 85,000 sqft warehouse redesign
- Pricing engine: $8–$22/sqft warehouse design range
- Sample output: `samples/proposal-Gulf-Coast-Logistics-2026-04-18.md`

**CHIEF** (port 8006) — production
- Daily executive brief synthesizing all operator data
- Reads RECON CSV, QUOTE proposals, Vault memory
- Sample output: `samples/chief-brief-2026-04-18.md`

**Aurelia** (port 8009) — beta
- Personal executive assistant — commitments, priorities, weekly CEO report
- Morning brief endpoint: `GET /api/morning-brief`

**Debrief** (port 8008) — beta
- Post-call intelligence logger
- Tested live: Gulf Coast Logistics call — extracted action items, commitments,
  follow-up email draft from raw notes in under 60 seconds
- Sample output: `samples/debrief-gulf-coast-logistics-2026-04-18.md`

### Infrastructure

**`start.sh`** — new
- Single command brings up full stack in correct order:
  llama.cpp → Cascadia OS (11 components) → RECON → SCOUT → QUOTE → CHIEF
- Health checks at each step, graceful fallback if already running

**`stop.sh`** — new
- Cleanly terminates all processes

**`tools/swiftbar/cascadia.1m.sh`** — new
- SwiftBar menu bar plugin for macOS
- Shows live component count (`⬡ 11/11`), LLM status, all operator statuses
- One-click: open PRISM, open RECON, Start/Stop/Run Demo
- Pending approval alerts surfaced in menu bar
- Install: copy to `~/swiftbar-plugins/`, requires SwiftBar (swiftbar.app)

**`cascadia/operators/registry.json`** — new
- Central manifest for all 8 operators
- Fields: id, name, category, description, status, port, autonomy, sample_output

**`samples/`** — new directory
- `recon-houston-warehouse-leads-2026-04-18.csv`
- `proposal-Gulf-Coast-Logistics-2026-04-18.md`
- `chief-brief-2026-04-18.md`
- `debrief-gulf-coast-logistics-2026-04-18.md`
- `README.md`

### Model

- All operators unified on `zyrcon-ai-v0.1` (Qwen2.5-3B-Instruct-Q4_K_M)
- Running via llama.cpp with Metal GPU offload on Apple Silicon
- FLINT proxy handles OpenAI-compatible format — operators need no changes
  when switching between local and cloud backends

### Demo

`bash demo.sh` — end-to-end workflow unchanged, now shows `11/11` components
`bash start.sh` — full stack up in ~60 seconds from cold start

'''

# Replace the old v0.34 entry with the full new one
old_start = "## v0.34 — 2026-04-18"
if old_start in src:
    # Find end of old entry (next ## or end of file)
    import re
    # Replace from old header to next header
    src = re.sub(
        r'## v0\.34 — 2026-04-18.*?(?=\n## |\Z)',
        '',
        src,
        flags=re.DOTALL
    ).strip()

# Write new entry at top
final = new_entry + "\n---\n\n" + src.replace("# Changelog\n\n---\n\n", "")
p.write_text(final)
print("  CHANGELOG.md updated — full v0.34.0 entry written")
PYEOF

# ── 2. Operator smoke tests ────────────────────────────────────────────────────
echo "[2/3] Writing tests/test_operators.py"
cat > tests/test_operators.py << 'TESTEOF'
"""
tests/test_operators.py — Cascadia OS v0.34
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

    def test_registry_has_eight_operators(self):
        self.assertEqual(len(self.operators), 8)

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

    def test_production_operators_exist(self):
        prod = [op["id"] for op in self.operators if op["status"] == "production"]
        self.assertIn("recon", prod)
        self.assertIn("scout", prod)
        self.assertIn("quote", prod)
        self.assertIn("chief", prod)

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
        self.assertEqual(d["total"], 8)
        self.assertGreaterEqual(d["online"], 0)


# ─────────────────────────────────────────────────────────────────────────────
# FLINT version autoupdate
# ─────────────────────────────────────────────────────────────────────────────

class TestVersionAutoupdate(unittest.TestCase):

    def test_version_readable_from_init(self):
        from cascadia import __version__, VERSION, VERSION_SHORT
        self.assertRegex(__version__, r'^\d+\.\d+\.\d+$')
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
        from cascadia import VERSION_SHORT
        self.assertEqual(d["version"], VERSION_SHORT)


if __name__ == "__main__":
    print("\n=== Cascadia OS — Operator Ecosystem Tests ===\n")
    unittest.main(verbosity=2)
TESTEOF

echo "  tests/test_operators.py written"

# ── 3. Tag GitHub release ──────────────────────────────────────────────────────
echo "[3/3] Tagging GitHub release v0.34.0"
git add CHANGELOG.md tests/test_operators.py apply-polish.sh
git commit -m "v0.34.0: full CHANGELOG, operator smoke tests, release tag"
git tag -a v0.34.0 -m "Cascadia OS v0.34.0

Operator ecosystem release. Five operators generating real output locally
using Qwen 3B via llama.cpp. Full stack in one command. SwiftBar menu bar
plugin. PRISM operator cards. Single-source version management.

Operators: RECON, SCOUT, QUOTE, CHIEF (production) + Aurelia, Debrief (beta)
Platform: 11/11 components, crash recovery, approval gates
Model: Qwen2.5-3B-Instruct-Q4_K_M via llama.cpp + Metal GPU
Tests: 182 platform tests + operator ecosystem tests"

git push origin main
git push origin v0.34.0

echo ""
echo "═══════════════════════════════════════════════════"
echo " Polish pass complete."
echo "═══════════════════════════════════════════════════"
echo ""
echo " Run operator tests:"
echo "   python3 -m pytest tests/test_operators.py -v"
echo ""
echo " GitHub release:"
echo "   https://github.com/zyrconlabs/cascadia-os/releases/tag/v0.34.0"
echo ""
