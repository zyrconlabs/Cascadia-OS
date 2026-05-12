# Sprint 2B — Mission Package Implementation Plan

**Status:** Draft — pending 2A RFC approval
**Version:** 1.0-draft
**Date:** 2026-05-12
**Author:** Zyrcon Labs

This plan is ready for execution after Andy approves `docs/mission-package-spec.md`
and `docs/package-canonicalization.md`. Each phase is one commit, passes the full
test suite, and is reviewable independently.

---

## Prerequisite — Resolve Open Questions Before Starting

Before Sprint 2B begins, the following decisions from the RFC must be
resolved. If Andy does not respond, the recommended defaults apply:

| Question | Default if no response |
|----------|----------------------|
| J1 — JSON vs YAML | JSON (`mission.json`) |
| J2 — Tier naming | `"lite"` everywhere |
| J3 — `risk_level: critical` | Add to `manifest_validator.py` |
| J4 — Approval gate timeout | Per-step |
| J5 — Sub-missions | Defer to v2 |
| J6 — Public key bundle location | Static `cascadia/depot/zyrcon_signing_keys.json` |
| J7 — Mission install root | `missions.packages_root` from config.json |

---

## Existing Code to Preserve (Do Not Break)

Sprint 2B touches three areas that have existing implementations. Before
any phase begins, add regression tests for existing behavior so changes
cannot silently break it:

| Module | Existing behavior to preserve |
|--------|------------------------------|
| `cascadia/depot/manifest_validator.py` | All 24 validation rules for operators and connectors |
| `cascadia/missions/manifest.py` | All 21 existing `MissionManifest.validate()` rules |
| `cascadia/missions/registry.py` | `discover()`, `list_catalog()`, `list_installed()`, all lookups |
| `cascadia/registry/crew.py` | Operator install, uninstall, tier check, port conflict check |
| `cascadia/automation/stitch.py` | Workflow registration, run tracking, sales funnel seed |

These are the "do not break" contracts for every phase.

---

## Phase 1 — Spec Fixtures Only

**Commit message:** `test(missions): add mission package fixture files for Sprint 2B`

**What changes:**

Add example mission packages under `cascadia/tests/fixtures/missions/`.
These are data files only. No Python code is added or modified in this phase.

**Fixtures to create:**

```
cascadia/tests/fixtures/missions/
├── valid_signed/
│   ├── mission.json        (complete valid manifest, all required fields)
│   └── workflows/
│       └── main.json
├── tampered_file/
│   ├── mission.json        (valid manifest, but a workflow file's SHA-256 won't match)
│   └── workflows/
│       └── main.json       (content differs from declared sha256)
├── tampered_manifest/
│   └── mission.json        (signature field is invalid/corrupted)
├── missing_capability/
│   └── mission.json        (capabilities list references a non-existent capability)
├── capability_not_in_registry/
│   └── mission.json        (capability string passes format check but isn't in entitlements.py)
├── tier_insufficient/
│   └── mission.json        (tier_required: enterprise)
└── risk_level_too_low/
    └── mission.json        (declares risk_level: low but has email.send capability which is medium)
```

Each `mission.json` is a complete, internally consistent JSON file representing
the fixture's scenario.

**Tests added:** None in this phase (fixtures are data). Phase 2 tests use them.

**Operator/connector behavior:** Unchanged. No Python files modified.

**Backward compatibility:** No runtime behavior changes.

---

## Phase 2 — Mission Package Parser / Validator Extensions

**Commit message:** `feat(missions): extend MissionManifest with capabilities, signing, and file fields`

**What changes:**

Extend `cascadia/missions/manifest.py` (the existing `MissionManifest` class).
This is an extension, not a replacement. All 21 existing validation rules are
preserved and run first; new rules are appended.

**New validation rules added to `MissionManifest.validate()`:**

- Rule 22: `capabilities` — list of strings, each a key in `CAPABILITY_REGISTRY`
- Rule 23: `requires_approval` — list of strings, must be subset of `capabilities`
- Rule 24: `risk_level` — enum (low/medium/high/critical); must be ≥ highest
  capability risk level
- Rule 25: `runtime` — enum (server/mobile/both); required field
- Rule 26: `author` — non-empty string; required field (already in operator schema)
- Rule 27: `signed_by` — non-empty string; required field
- Rule 28: `signature_algorithm` — must be `"Ed25519"` (only supported algorithm)
- Rule 29: `key_id` — non-empty string matching `^[a-z][a-z0-9-]+$`
- Rule 30: `package_digest` — string matching `^sha256:[a-f0-9]{64}$`
- Rule 31: `files` — list of dicts; each with `path` (normalized, valid) and
  `sha256` (64-char lowercase hex); `size_bytes` optional positive int
- Rule 32: `tier_required` values — aligned to `lite/pro/business/enterprise`
  (removes `free` as valid value — see RFC J2)

**Tier alignment fix:**

```python
# Before
_VALID_TIERS = {"free", "pro", "business", "enterprise"}
# After
_VALID_TIERS = {"lite", "pro", "business", "enterprise"}
```

**`manifest_validator.py` changes:**

- Add `"critical"` to `VALID_RISK_LEVELS` (see RFC J3)

**New module:** `cascadia/depot/canonicalization.py`

Implement all functions defined in `docs/package-canonicalization.md`:

```python
def normalize_path(path: str) -> str: ...
def is_text_file(path: str) -> bool: ...
def normalize_line_endings(content: bytes) -> bytes: ...
def canonical_file_bytes(path: str, content: bytes) -> bytes: ...
def file_sha256(path: str, content: bytes) -> str: ...
def compute_package_digest(file_map: dict[str, bytes]) -> str: ...
def canonical_manifest_bytes(manifest: dict) -> bytes: ...
```

No I/O. Pure computation over bytes. No side effects.

**Tests:** `cascadia/tests/test_mission_manifest_extended.py`

- All 21 existing rules still pass (regression)
- Rule 22: unknown capability name → validation error
- Rule 23: `requires_approval` not subset of `capabilities` → error
- Rule 24: `risk_level: low` with `email.send` (medium) capability → error
- Rule 24: `risk_level: medium` with `email.send` (medium) capability → OK
- Rule 25: missing `runtime` → error
- Rule 26: missing `author` → error
- Rule 32: `tier_required: free` → error (now invalid)
- Rule 32: `tier_required: lite` → OK

`cascadia/tests/test_canonicalization.py`

- `normalize_path` handles `..`, leading slash, `\`, collapsed slashes, `.`
- `normalize_path` raises on depth > 8
- `is_text_file` returns True for `.json`, `.md`, False for `.png`
- `normalize_line_endings` converts CRLF → LF, bare CR → LF
- `canonical_manifest_bytes` excludes `signature` field, sorts keys, no spaces
- `compute_package_digest` matches worked example in `package-canonicalization.md`

**Operator/connector behavior:** Unchanged. `manifest_validator.py` changes
are backward compatible (adding `"critical"` to valid risk levels does not
break existing manifests using low/medium/high).

---

## Phase 3 — Asymmetric Signing Module

**Commit message:** `feat(depot): add Ed25519 signing and verification module`

**What changes:**

New module: `cascadia/depot/signing.py`

```python
from typing import Protocol

class Signer(Protocol):
    """Abstract signing interface. Sprint 5 Signing Worker implements this."""
    def sign(self, message: bytes) -> bytes: ...
    def key_id(self) -> str: ...

class LocalSigner:
    """
    Ed25519 signer using a local private key file.
    Key path: ZYRCON_SIGNING_KEY_PATH env var, or ~/.config/zyrcon/signing.key
    Sprint 5 replaces this with a Signing Worker client without changing Verifier.
    """
    def __init__(self, key_path: str | None = None): ...
    def sign(self, message: bytes) -> bytes: ...
    def key_id(self) -> str: ...

class Verifier:
    """
    Ed25519 verifier. Holds a map of key_id -> public_key.
    Used by CREW at install time. Never holds private keys.
    """
    def __init__(self, key_bundle_path: str | None = None): ...
    def verify(self, message: bytes, signature_b64: str, key_id: str) -> bool: ...
    def known_key_ids(self) -> list[str]: ...

def load_key_bundle(path: str) -> dict[str, str]:
    """Load key_id -> base64-encoded-public-key map from JSON file."""
    ...

def sign_manifest(manifest: dict, signer: Signer) -> dict:
    """Return manifest with signature, signed_by, signature_algorithm, key_id fields added."""
    ...

def verify_manifest(manifest: dict, verifier: Verifier) -> bool:
    """Verify manifest signature. Returns True if valid. Raises ValueError on key_id unknown."""
    ...
```

**Key bundle file:** `cascadia/depot/zyrcon_signing_keys.json`

```json
{
  "zyrcon-2026-q2": "<base64url-encoded Ed25519 public key>",
  "zyrcon-2026-q1": "<old key, kept during rotation window>"
}
```

**Local dev keypair generation:** A one-time script in `scripts/generate_signing_key.py`
that writes a local Ed25519 keypair to `~/.config/zyrcon/signing.key` (private)
and outputs the public key for adding to the key bundle. Never committed to git.
Add `~/.config/zyrcon/signing.key` to `.gitignore`.

**Tests:** `cascadia/tests/test_signing.py`

- `LocalSigner.sign()` produces bytes verifiable by `Verifier.verify()`
- `verify_manifest()` returns True for a correctly signed manifest
- `verify_manifest()` returns False for a tampered manifest (one field changed)
- `verify_manifest()` raises ValueError for unknown key_id
- Key rotation: verifier with two keys accepts signatures from either key
- `sign_manifest()` returns a dict with all four signature fields set
- `canonical_manifest_bytes()` excludes `signature` field

**Operator/connector behavior:** Unchanged. New module only.

---

## Phase 4 — File Hash and Package Digest Verification in CREW

**Commit message:** `feat(crew): add mission package hash and digest verification`

**What changes:**

Modify `cascadia/registry/crew.py`.

Add a new private function `_verify_mission_package(zip_path, manifest)` that:

1. Extracts zip to a temp directory
2. Strips excluded paths (macOS, bytecode, VCS, editor backups)
3. Computes `compute_package_digest()` over the extracted files
4. Compares to `manifest["package_digest"]`
5. For each entry in `manifest["files"]`: computes `file_sha256()` and compares
6. Checks for extra files (in zip but not in `files[]`)
7. Returns a list of errors (empty = pass)

Add a new private function `_verify_mission_signature(manifest, verifier)` that:

1. Calls `verify_manifest(manifest, verifier)` from `signing.py`
2. Returns `(True, "")` or `(False, error_code)`

The existing `install_operator()` function already dispatches on `type == "mission"`
implicitly (via `manifest_validator.py`). This phase adds an explicit mission
branch in `install_operator()` that calls the two new verification functions before
extraction and registry write.

**Operator/connector install path:** The existing code path is unchanged. The
mission branch is added as an early `if manifest.get("type") == "mission":` block
that runs signature verification, then calls `_verify_mission_package()`, then
continues to the existing install steps.

**Fail behavior:** Any verification failure returns immediately with a structured
error dict (see spec Section H). No partial install state is left behind.

**Verifier initialization:** `Verifier` is instantiated once at `CrewService.__init__`
using the key bundle at `cascadia/depot/zyrcon_signing_keys.json`. Re-read on
each install call if the file mtime has changed (supports key rotation without
restart).

**Tests:** `cascadia/tests/test_crew_mission_verify.py`

- Tampered file (SHA-256 mismatch) → install returns error `"file_hash_mismatch"`
- Extra file in zip not in `files[]` → error `"extra_files_in_package"`
- Missing file declared in `files[]` but absent from zip → error `"missing_files"`
- Package digest mismatch → error `"package_digest_mismatch"`
- Invalid signature → error `"invalid_signature"`
- Unknown key_id → error `"unknown_key_id"`
- Valid package → proceeds to registry write

**Regression tests:** `cascadia/tests/test_crew_operator_install_regression.py`

- Existing operator install path (zip_b64, manifest-only, package_url) still works
- Existing tier check still works
- Existing port conflict check still works
- Existing uninstall path unchanged

---

## Phase 5 — CREW Mission Install + STITCH Registration

**Commit message:** `feat(crew): wire mission install path to MissionRegistry and STITCH`

**What changes:**

Complete the mission install path in `cascadia/registry/crew.py` to:

1. Run all verification from Phase 4
2. Check kill switch (query local kill switch table)
3. Check tier via LICENSE_GATE (existing `_check_tier` pattern)
4. Check `min_zyrcon_version` (compare to `cascadia.VERSION`)
5. Check `operators.required` dependencies (query local CREW registry)
6. Extract zip to `missions.packages_root / mission_id`
7. Register in `MissionRegistry` (write to `missions_registry.json`)
8. POST to STITCH `register_mission` endpoint (best-effort; if STITCH is
   unreachable, write to a pending-registration queue in a JSON sidecar file;
   STITCH reads and clears this queue on startup)
9. Write audit log entry
10. Return `201` with mission ID, version, and health status

Add `POST /api/crew/install_mission` as a new route (separate from
`install_operator`) to keep the two paths cleanly separated and avoid
adding yet more branching to the already complex `install_operator()`.

Add `POST /api/crew/uninstall_mission` with `dry_run` support per spec
Section I.

**STITCH changes:** Add `POST /api/workflows/register_mission` route to
`cascadia/automation/stitch.py` that:
- Accepts `{ "mission_id": "...", "install_path": "..." }`
- Reads workflow files from `install_path`
- Upserts to `workflow_definitions` with namespaced IDs
  (`"mission:<mission_id>:<workflow_id>"`)
- Returns `200 { "registered": ["mission:lead_qual:main", ...] }`

**Operator/connector behavior:** Unchanged. New routes are additive.

**Tests:** `cascadia/tests/test_crew_mission_install.py`

- Full install flow: valid signed package → 201 success
- Mission appears in `MissionRegistry.list_installed()` after install
- Mission workflows appear in STITCH `workflow_definitions` after install
- Tier insufficient → 403 with `tier_insufficient` error
- Missing required operator → 422 with `missing_operator` error
- Version incompatible → 422 with `version_incompatible` error
- Kill switch hit → 403 with `package_revoked` error
- Dry-run uninstall → lists affected workflows and data tables
- Confirmed uninstall → removes from registry and STITCH

---

## Phase 6 — Integration Tests

**Commit message:** `test(missions): add full mission install integration tests`

**What changes:**

Add integration tests that exercise the full pipeline end-to-end using
real local components (no mocks for the happy path).

**Tests:** `cascadia/tests/test_mission_integration.py`

- Parse → schema validate → signature verify → hash verify → install → register
- All 10 install verification steps in sequence (spec Section H)
- Tampered file at each step fails at the correct step
- Approval gate: mock approval resolves workflow run
- Key rotation: two active keys, signature from either key passes
- Key rotation: signature from expired key_id (not in bundle) fails
- Unicode file paths: handles non-ASCII filenames in normalized form
- Empty `files[]` (zero-asset mission) — valid if no extra files in zip
- Oversized package: reject if total uncompressed size > 100 MB (configurable)
- Operator install path regression: existing operators still install correctly
- Connector install path regression: existing connectors still install correctly

**No new Python modules.** Tests only.

---

## Phase 7 — Documentation and Cleanup

**Commit message:** `docs(missions): add installation guide and CHANGELOG entry`

**What changes:**

- Update `CHANGELOG.md` with Sprint 2B changes
- Add `docs/missions.md` update: link to `mission-package-spec.md` and
  `package-canonicalization.md` as the canonical references (existing
  `docs/missions.md` documents the runtime mission system; update it to
  reference the package format)
- Verify `git diff --name-only` matches expected list only

---

## Modules to Create

| Module | Purpose | Public interface |
|--------|---------|-----------------|
| `cascadia/depot/canonicalization.py` | Canonical byte computation | 7 functions (see Phase 2) |
| `cascadia/depot/signing.py` | Ed25519 signing and verification | `Signer`, `LocalSigner`, `Verifier`, `sign_manifest`, `verify_manifest` |
| `scripts/generate_signing_key.py` | One-time dev key generation | CLI script, not imported |

**Test files to create:**

| Test file | Covers |
|-----------|--------|
| `cascadia/tests/test_mission_manifest_extended.py` | New MissionManifest rules (22–32) |
| `cascadia/tests/test_canonicalization.py` | All canonicalization functions + worked example |
| `cascadia/tests/test_signing.py` | Ed25519 sign/verify, key rotation |
| `cascadia/tests/test_crew_mission_verify.py` | Hash and digest verification in CREW |
| `cascadia/tests/test_crew_operator_install_regression.py` | Existing operator install unchanged |
| `cascadia/tests/test_crew_mission_install.py` | Full mission install path |
| `cascadia/tests/test_mission_integration.py` | End-to-end pipeline |

---

## Modules to Modify

| Module | Change | Backward compatibility |
|--------|--------|----------------------|
| `cascadia/missions/manifest.py` | Add rules 22–32; align tier values | Existing rules 1–21 unchanged. `"free"` tier is now invalid — see migration note. |
| `cascadia/depot/manifest_validator.py` | Add `"critical"` to `VALID_RISK_LEVELS` | Backward compatible extension. |
| `cascadia/registry/crew.py` | Add `install_mission`, `uninstall_mission` routes; add `_verify_mission_package`, `_verify_mission_signature` | New routes only. Existing `install_operator` path unchanged. |
| `cascadia/automation/stitch.py` | Add `POST /api/workflows/register_mission` route | New route only. Existing routes unchanged. |

### Migration Note — `"free"` Tier

Any existing `mission.json` files using `tier_required: "free"` will fail
validation after Phase 2. Before Phase 2 commits, run a one-time migration:

```bash
find . -name "mission.json" -exec grep -l '"tier_required": "free"' {} \; | \
  xargs sed -i 's/"tier_required": "free"/"tier_required": "lite"/g'
```

The only known locations (as of 2026-05-12 inspection) are internal test
fixtures and `missions_registry.json`. The operators repo and mobile repo
are out of scope and must not be touched.

---

## Files NOT to Touch

| File / Repo | Reason |
|------------|--------|
| Operators repo | Entirely out of scope |
| Mobile repo | Entirely out of scope |
| Grid repo | Entirely out of scope |
| Enterprise repo | Entirely out of scope |
| Existing operator manifests | Schema unchanged |
| Existing connector manifests | Schema unchanged |
| `cascadia/encryption/curtain.py` | CURTAIN is not used for package signing |
| `cascadia/licensing/license_gate.py` | No changes needed |
| `cascadia/dashboard/prism.py` | No changes needed in Sprint 2B |
| `config.json` | `missions.packages_root` key already exists |
| `.env` or any secret file | Signing key never committed |

---

## Required Tests Summary

All tests must pass without any failing. Skip count must not increase
above current baseline (≤17 skipped as of Sprint 1).

| Scenario | Expected result |
|----------|----------------|
| Valid signed mission package → install | 201 success |
| Tampered file (SHA-256 mismatch) | Error: `file_hash_mismatch` |
| Tampered manifest (bad signature) | Error: `invalid_signature` |
| Missing required capability | Error: `unknown_capability` |
| Capability not in entitlements registry | Error: `unknown_capability` |
| `risk_level` too low for declared capabilities | Validation error |
| Tier insufficient | Error: `tier_insufficient` |
| Missing required operator | Error: `missing_operator` |
| Package on kill switch | Error: `package_revoked` |
| `min_zyrcon_version` not met | Error: `version_incompatible` |
| Approval gate — approved | Workflow proceeds |
| Approval gate — timeout `fail_step` | Step marked failed, run continues |
| Mission registers with STITCH after install | Workflow IDs appear in STITCH |
| Mission uninstall dry-run | Returns impact assessment |
| Mission uninstall confirmed | Removed from registry and STITCH |
| Key rotation: old key still active | Old-key signature verified |
| Key rotation: old key expired | Old-key signature rejected |
| Operator install path: unchanged | Operator still installs correctly |
| Connector install path: unchanged | Connector still installs correctly |
| `tier_required: "free"` in manifest | Validation error (now invalid) |
| `tier_required: "lite"` in manifest | Validation passes |
| `risk_level: "critical"` in manifest | Validation passes (RFC J3 fix) |

---

## Estimated Effort

| Phase | Estimated hours | New files | Modified files |
|-------|----------------|-----------|---------------|
| 1 — Fixtures | 1h | 8 fixture files | 0 |
| 2 — Validator + canonicalization | 4h | 2 modules + 2 test files | 2 |
| 3 — Signing module | 3h | 2 modules + 1 test file | 1 (gitignore) |
| 4 — Hash/digest verification in CREW | 3h | 2 test files | 1 |
| 5 — Mission install + STITCH | 4h | 2 test files | 2 |
| 6 — Integration tests | 2h | 1 test file | 0 |
| 7 — Docs + changelog | 1h | 0 | 2 |
| **Total** | **~18h** | **~18 files** | **~8 files** |

Commits: 7 (one per phase). All on branch `mission-package-spec-rfc` or
`mission-package-impl` depending on Andy's branch preference for Sprint 2B.

---

## Risks and Mitigations

### Risk 1 — Local signing key management for development

**Problem:** Developers need an Ed25519 private key to sign test fixtures.
Signing key must never be committed to the repo.

**Mitigation:** `scripts/generate_signing_key.py` generates a local dev
keypair. Add `signing.key` to `.gitignore`. For CI, generate an ephemeral
keypair at test setup time and use the matching public key in the test key
bundle. The test verifier uses a fixture key bundle, not the production one.

---

### Risk 2 — `"free"` → `"lite"` migration breaks existing mission.json files

**Problem:** `MissionManifest._VALID_TIERS` currently accepts `"free"`.
Changing it to `"lite"` will break any `mission.json` file using `"free"`.

**Mitigation:** Run the one-time migration script (documented in
"Modules to Modify" above) before Phase 2 commits. Write a pre-commit
check that scans for `"tier_required": "free"` in `mission.json` files.
Known affected file: `cascadia/missions/missions_registry.json`.

---

### Risk 3 — STITCH unavailable during mission install

**Problem:** If STITCH is not running when a mission installs, workflow
registration fails and the mission appears installed but its workflows
aren't executable.

**Mitigation:** Mission install writes to a pending-registration sidecar
file (`data/runtime/stitch_pending.json`) if STITCH is unreachable.
STITCH reads and processes this file on startup. Install still returns 201
but with `"stitch_pending": true` in the response. PRISM surfaces a
warning to the user that the mission will be fully available after STITCH
starts.

---

### Risk 4 — Deterministic YAML serialization (if YAML is chosen for J1)

**Problem:** YAML serializers are less deterministic than JSON. PyYAML can
produce inconsistent output for the same data structure across versions.
ruamel.yaml is more deterministic but is a larger dependency.

**Mitigation:** If Andy selects YAML (RFC J1 Option B), use
`json.dumps(...).encode('utf-8')` as the canonical signing input
regardless of whether the file format is JSON or YAML. Parse either format
to a dict, then sign the canonical JSON representation. This decouples the
file format from the signing format.

This is why the recommended default is JSON — it avoids this complexity
entirely.

---

### Risk 5 — Performance of file hash verification for large packages

**Problem:** A mission package with many large files could make install
slow if files are hashed synchronously.

**Mitigation:** Hash files in parallel using `concurrent.futures.ThreadPoolExecutor`
(I/O-bound work, GIL not a bottleneck). For packages under 50 MB total,
sequential is acceptable. Add a progress log line every 10 files for
operator visibility. For MVP, accept up to 100 MB uncompressed; reject
larger packages with a clear error.

---

### Risk 6 — Atomicity of multi-step install

**Problem:** If CREW crashes after extracting files but before writing to
`MissionRegistry`, the mission is partially installed — files on disk but
not registered. A restart could leave orphaned files.

**Mitigation:** Extract to a temp directory first (`data/runtime/install_tmp/<mission_id>/`).
Only move to final location after all verification passes AND `MissionRegistry`
write succeeds. If the process crashes mid-move, the temp directory survives
and is cleaned up on next CREW startup (add orphan cleanup to CREW init,
consistent with FLINT's `_cleanup_orphan_components()` pattern).

---

### Risk 7 — `cryptography` package version compatibility

**Problem:** `cryptography` package is already used (AES-GCM in CURTAIN).
Ed25519 support was added in `cryptography` 2.6. However, the exact API
used (`Ed25519PrivateKey.generate()`) requires checking installed version.

**Mitigation:** Add a startup assertion in `signing.py`:
```python
from cryptography import __version__ as _cv
assert tuple(int(x) for x in _cv.split('.')[:2]) >= (2, 6), \
    f"cryptography >= 2.6 required for Ed25519; installed: {_cv}"
```
Check `requirements.txt` and `pyproject.toml` pin a sufficient version.
In practice, any modern install will have a version far above 2.6.

---

## Sprint 2B Acceptance Criteria

1. All 7 phases committed, each passing full test suite
2. New test count: ≥ 40 new tests
3. Existing test count: unchanged (no regressions)
4. Skip count: ≤ 17 (no new skips)
5. `git diff --name-only` vs `main` shows only allowed files
6. No code references private key path (only env var or config key)
7. No hardcoded `/Users/andy/...` paths
8. `signing.key` appears in `.gitignore`
9. `docs/mission-package-spec.md` Section H install verification flow
   is fully implemented and tested
10. Operator and connector install paths produce identical results as before
