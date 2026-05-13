# Mission Package Specification

**Status:** RFC — awaiting Andy review before Sprint 2B implementation
**Version:** 1.0-draft
**Date:** 2026-05-12
**Author:** Zyrcon Labs

---

## Preflight Findings (Inform This RFC)

Before writing this spec, the following code was inspected:

- `cascadia/depot/manifest_validator.py` — existing operator/connector schema
- `cascadia/missions/manifest.py` — existing `MissionManifest` class (already wired)
- `cascadia/missions/registry.py` — existing `MissionRegistry`
- `cascadia/encryption/curtain.py` — CURTAIN signing primitives
- `cascadia/registry/crew.py` — CREW install path
- `cascadia/automation/stitch.py` — STITCH workflow engine
- `cascadia/shared/entitlements.py` — capability registry (61 capabilities)

**Critical finding:** `cascadia/missions/manifest.py` and `cascadia/missions/registry.py`
already exist and are wired into `manifest_validator.py` (line 80–82). A
`MissionManifest` class already validates a `mission.json` format with 21 rules.
This spec extends and formalizes that existing foundation rather than starting
from scratch.

**Format question (OPEN — DECISION NEEDED):** The build plan refers to
`mission.yaml`. The existing codebase uses `mission.json`. This spec uses JSON
examples for consistency with the existing implementation. See Section J, item J1
for the full decision record.

---

## Section A — Package Identity and Metadata

### Required Fields

| Field | Type | Validation |
|-------|------|-----------|
| `type` | string | Must be `"mission"` |
| `id` | string | Lowercase, alphanumeric + hyphens + underscores. Must be unique per author within the catalog. Pattern: `^[a-z][a-z0-9_-]+$` |
| `version` | string | Semver: `x.y.z` where each part is a non-negative integer |
| `name` | string | Non-empty, 1–80 characters, human-readable display name |
| `description` | string | 1–500 characters |
| `tier_required` | string | Enum: `lite` \| `pro` \| `business` \| `enterprise` |
| `runtime` | string | Enum: `server` \| `mobile` \| `both` |
| `author` | string | Developer or org slug. Must match developer record in catalog. |
| `signed_by` | string | Entity that produced the signature. Typically `"zyrcon-labs"` for Zyrcon-published packages. |

**Note on `tier_required` values:** The existing `MissionManifest` class uses `"free"` as a
tier value, while `manifest_validator.py` uses `"lite"`. Sprint 2B must align both to `"lite"`.
This is a **BREAKING CHANGE to the existing `mission.json` format** — any installed missions
using `"free"` must be migrated. See Section J, item J2.

### Optional Fields

| Field | Type | Default | Validation |
|-------|------|---------|-----------|
| `tags` | list[string] | `[]` | Each tag: lowercase, max 30 chars |
| `category` | string | `null` | Enum from `manifest_validator.VALID_CATEGORIES` (18 values: sales, marketing, support, finance, operations, devops, ecommerce, data, hr, industry, communication, productivity, iot, legal, integration, analytics, identity, runtime) |
| `industries` | list[string] | `[]` | Free-form strings, e.g. `["healthcare", "fintech"]` |
| `min_zyrcon_version` | string | `null` | Semver string. Install fails if AI Server version is below this. |
| `homepage_url` | string | `null` | HTTPS URL |
| `support_email` | string | `null` | Valid email format |
| `changelog` | string | `null` | Free-form markdown string |
| `icon` | string | `null` | Relative path to icon file within package |

### Example Identity Block

```json
{
  "type": "mission",
  "id": "lead_qualification",
  "version": "1.2.0",
  "name": "Lead Qualification Pipeline",
  "description": "Qualifies inbound leads, researches companies, generates proposals, and sends with approval.",
  "tier_required": "pro",
  "runtime": "server",
  "author": "zyrcon-labs",
  "signed_by": "zyrcon-labs",
  "tags": ["sales", "crm", "automation"],
  "category": "sales",
  "industries": ["saas", "professional_services"],
  "min_zyrcon_version": "0.49.0"
}
```

---

## Section B — Capability Declarations

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `capabilities` | list[string] | Yes | All capabilities this mission may invoke. Each name must exist in `cascadia/shared/entitlements.py:CAPABILITY_REGISTRY`. |
| `requires_approval` | list[string] | Yes | Subset of `capabilities` that trigger a human approval gate before execution. Must be a subset of `capabilities`. |
| `risk_level` | string | Yes | `low` \| `medium` \| `high` \| `critical`. |

### Risk Level: Declared, Not Derived

`risk_level` is **declared** in the manifest, not auto-derived. However,
validation enforces a floor: declared `risk_level` must be greater than or
equal to the highest risk level of any capability in the `capabilities` list.
A mission that declares `high` capabilities but only claims `risk_level: medium`
fails validation.

Risk level ranking (lowest to highest): `low < medium < high < critical`.

This is intentional: authors can declare a higher risk level than strictly
required, but cannot under-declare. This means future capability additions
won't silently lower apparent risk.

### Validation Rules

1. Every string in `capabilities` must be a key in `CAPABILITY_REGISTRY`
   (exact match required; wildcard capability strings are not permitted in
   mission manifests).
2. Every string in `requires_approval` must also appear in `capabilities`.
3. `risk_level` must be ≥ the highest risk level of any listed capability.
4. `capabilities` may be empty (a mission with no external capability calls
   is valid — it may only invoke built-in STITCH steps).
5. If `capabilities` is empty, `requires_approval` must also be empty.
6. If `risk_level` is `critical`, validation adds a warning recommending
   explicit justification in `description`.

### Relationship to `entitlements.py`

The `CAPABILITY_REGISTRY` in `cascadia/shared/entitlements.py` is the
authoritative source. As of Sprint 2A inspection, it contains 61 capabilities
across 4 risk levels (low=26, medium=16, high=13, critical=6).

**Note:** The existing `manifest_validator.py` only accepts `low`, `medium`,
`high` for `risk_level`. Sprint 2B must align it to also accept `critical`
(consistent with `entitlements.py`). This is a backward-compatible
extension — existing manifests using low/medium/high remain valid.

### Example Capability Block

```json
{
  "capabilities": [
    "crm.read",
    "crm.write",
    "email.send",
    "email.read",
    "file.write"
  ],
  "requires_approval": [
    "email.send"
  ],
  "risk_level": "medium"
}
```

---

## Section C — Requirements (Dependencies)

### Fields

```json
{
  "operators": {
    "required": ["scout", "recon", "chief", "quote"],
    "optional": ["sentiment"]
  },
  "connectors": {
    "required": ["email"],
    "optional": ["calendar", "slack"]
  }
}
```

These fields already exist in the `MissionManifest` schema and are validated
by the existing `MissionManifest.validate()`. This spec formalizes the
pinning syntax and install-time behavior.

### Pinning Syntax

Operators and connectors may be listed as bare IDs (floating) or pinned:

- `"scout"` — any installed version accepted
- `"scout@1.2.0"` — exact version required
- `"scout@>=1.2.0"` — minimum version (semver comparison)

Pinning format: `"<id>[@<version-constraint>]"` where `<version-constraint>`
is either an exact semver string or a `>=`-prefixed semver string. No other
operators (`^`, `~`, `<`, `<=`) are supported in v1 to keep parsing simple.

### Install-Time Behavior

1. CREW checks `operators.required` — any missing operator triggers a
   co-install prompt before the mission install continues. Install does
   not proceed if the user declines and the operator remains absent.
2. `operators.optional` items are listed in the install summary but do
   not block install.
3. `connectors.required` same behavior as operators.required.
4. `connectors.optional` same as operators.optional.
5. Version constraint failures block install with a clear message:
   `"required scout@>=1.2.0 but installed scout@1.0.3"`.

### `min_zyrcon_version`

May appear at top-level (Section A) or inside a `requires` block — both
are valid. If both are present, the stricter (higher) version wins.
Validation uses `packaging.version.Version` for semver comparison, falling
back to a simple three-tuple integer comparison if `packaging` is unavailable.

---

## Section D — Workflow Definition

### Existing STITCH Integration

STITCH (`cascadia/automation/stitch.py`) already supports a step-based
workflow model. Mission packages define workflows as separate JSON files
(referenced from `mission.json` via the `workflows` dict) and register
them with STITCH's `WorkflowStore` at install time.

The `workflows` field in `mission.json` is a dict mapping workflow IDs to
relative file paths:

```json
{
  "workflows": {
    "main": "workflows/main.json",
    "onboarding": "workflows/onboarding.json"
  }
}
```

Each workflow file is a standalone JSON document in STITCH's existing format.

### Step Types

| Type | Description |
|------|-------------|
| `operator` | Call an operator's HTTP endpoint |
| `connector` | Call a connector's HTTP endpoint |
| `approval` | Pause workflow, await human approval |
| `condition` | Evaluate a boolean expression; branch on result |
| `delay` | Wait a fixed duration before next step |

**Note:** `subworkflow` (inline invocation of another mission's workflow) is deferred to v2 per
J5 decision. It is not implemented in Sprint 2B. Do not implement this step type.

### Step Schema

```json
{
  "id": "step_email",
  "name": "Send Proposal",
  "type": "operator",
  "operator": "email",
  "port": 8010,
  "endpoint": "POST /send",
  "depends_on": ["step_approve"],
  "input_map": {
    "to": "{trigger.contact_email}",
    "subject": "Proposal for {trigger.company_name}"
  },
  "output_key": "email_result",
  "requires_approval": false,
  "timeout_seconds": 30,
  "on_error": "fail_mission",
  "retry": {
    "max_attempts": 2,
    "backoff_seconds": [5, 30]
  }
}
```

### Dependency Declaration

`depends_on` is a list of step IDs that must complete successfully before
this step begins. Circular dependencies fail validation. Validation uses
a topological sort; any cycle is reported with the full cycle path.

### Approval Gate Semantics

```json
{
  "id": "step_approve",
  "type": "approval",
  "label": "Approve proposal for {trigger.company_name}?",
  "risk_level": "medium",
  "timeout": "24h",
  "on_timeout": "fail_step",
  "depends_on": ["step_quote"]
}
```

- `timeout`: ISO 8601 duration string (`"24h"`, `"30m"`, `"7d"`)
- `on_timeout`: `fail_step` \| `fail_mission` \| `skip_step` (default: `fail_step`)
- Approval is routed to PRISM's approval queue and mobile push notifications

See J4 for the open question on per-step vs. global approval timeout.

### Input/Output Passing

Input values use `{context.key}` template syntax:
- `{trigger.<field>}` — from workflow trigger input
- `{<step_output_key>.<field>}` — from a previous step's output
- `{env.<VAR>}` — from environment variable (only whitelisted vars)

### Error Handling Per Step

| `on_error` value | Behavior |
|-----------------|----------|
| `fail_mission` | Abort workflow, mark run as failed, notify |
| `fail_step` | Mark step failed, skip dependent steps, continue non-dependent steps |
| `skip_step` | Treat step as succeeded with empty output, continue |
| `retry` | Retry per `retry` config, then apply fallback `on_error` |

Default `on_error` is `fail_mission`.

---

## Section E — Files and Assets

### `files[]` Field

Every file included in the mission package zip must be declared:

```json
{
  "files": [
    {
      "path": "workflows/main.json",
      "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "size_bytes": 4096
    },
    {
      "path": "templates/quote.md",
      "sha256": "abc123def456...",
      "size_bytes": 1280
    }
  ]
}
```

### Field Definitions

| Field | Required | Description |
|-------|----------|-------------|
| `path` | Yes | POSIX-normalized path relative to package root. No `..`, no leading `/`, max depth 8. |
| `sha256` | Yes | Lowercase hex SHA-256 of the file's canonical bytes (after line ending normalization — see canonicalization doc). |
| `size_bytes` | No | Uncompressed file size in bytes. Advisory — must match if present. |

### Validation Rules

1. Every file present in the zip must appear in `files[]`. Any extra file
   (not in `files[]`) causes install rejection with a list of the offending paths.
2. Every entry in `files[]` must correspond to a file in the zip. Missing
   files cause install rejection.
3. Each file's actual SHA-256 (computed over canonical bytes) must match
   `sha256`. Any mismatch causes install rejection and identifies the specific
   file.
4. `path` must not contain `..`, must not start with `/`, must not be an
   absolute path, must use `/` separators only, max 8 directory levels.
5. Symlinks are not permitted inside packages.
6. `mission.json` itself is NOT listed in `files[]` — it is verified
   separately via the manifest signature.

### Reserved Paths

These paths are excluded from `files[]` regardless of presence in the zip:

- `.DS_Store`, `__MACOSX/` (macOS artifacts)
- `*.pyc`, `__pycache__/` (Python bytecode)
- `.git/`, `.svn/` (VCS metadata)
- `*~`, `*.swp` (editor backups)

If any reserved path appears inside a submitted zip, it is silently
stripped before hash verification and is not listed in `files[]`.

---

## Section F — Package Digest

```json
{
  "package_digest": "sha256:e3b0c44298fc1c149afbf4c8996fb924..."
}
```

`package_digest` is a SHA-256 digest computed over the canonical package
contents — not the zip file itself. The canonical representation is defined
in `docs/package-canonicalization.md`.

**Verification flow:** The installer computes the package digest independently
from the zip contents and compares it to the declared value. Any mismatch
causes immediate install rejection before file hashes are checked.

The `sha256:` prefix is literal and required. It allows future algorithm
agility without breaking the field schema.

---

## Section G — Signature

```json
{
  "signature": "<base64url-encoded 64-byte Ed25519 signature>",
  "signed_by": "zyrcon-labs",
  "signature_algorithm": "Ed25519",
  "key_id": "zyrcon-2026-q2"
}
```

### Field Definitions

| Field | Required | Description |
|-------|----------|-------------|
| `signature` | Yes | Base64url-encoded Ed25519 signature (no padding) |
| `signed_by` | Yes | Signer entity slug. Must match a known public key holder. |
| `signature_algorithm` | Yes | `"Ed25519"` (only supported algorithm in v1) |
| `key_id` | Yes | Key identifier for rotation support. Format: `"<entity>-<year>-<quarter>"` |

### Signed Content

The signature covers the **canonical manifest** — the full `mission.json`
content with the `signature` field removed, serialized using the rules in
`docs/package-canonicalization.md` Section "Manifest Canonicalization".

The exact bytes fed to the Ed25519 signer are the UTF-8 encoding of the
canonical JSON. No additional framing, no length prefix.

### Key Rotation

Multiple valid `key_id` values may be active simultaneously during rotation
windows. The verifier holds a map of `key_id → public_key` and attempts
verification with the key matching the manifest's `key_id`. If the key_id
is unknown or expired, verification fails.

Rotation overlap window: 90 days by default. After 90 days, the old key_id
is removed from the verifier's map and old packages signed with it are
rejected unless re-signed.

### CURTAIN Relationship

CURTAIN (`cascadia/encryption/curtain.py`) currently provides HMAC-SHA256
envelope signing. Its `capabilities` endpoint already declares
`"asymmetric": "planned"`. CURTAIN is **not used** for package signing.

Package signing uses Ed25519 directly via Python's `cryptography` package
(`cryptography.hazmat.primitives.asymmetric.ed25519`), which is already
a project dependency (used by CURTAIN for AES-GCM). No new library is
required.

CURTAIN remains the service-to-service envelope mechanism. Package signing
is a separate, caller-side operation.

### Signing Abstraction (Sprint 2B Design Requirement)

Sprint 2B must implement signing behind a `Signer` protocol so that the
isolated Signing Worker (Sprint 5) can replace `LocalSigner` without
changing verifier code:

```python
class Signer(Protocol):
    def sign(self, message: bytes) -> bytes: ...
    def key_id(self) -> str: ...

class LocalSigner:
    """Sprint 2B implementation. Reads local Ed25519 private key."""
    ...

class Verifier:
    """Verifies signatures using public key map. Used by CREW at install."""
    def verify(self, message: bytes, signature: bytes, key_id: str) -> bool: ...
```

Local private key location (Sprint 2B): `~/.config/zyrcon/signing.key`
(or `ZYRCON_SIGNING_KEY_PATH` env var). Never hardcoded path, never
committed to the repo.

---

## Section H — Install Verification Flow

The following sequence is the authoritative order CREW must execute for
mission package installs. Every step must pass before the next begins.
Any failure causes immediate install rejection with a structured error
response.

```
1. Parse mission.json
   → Parse as JSON. Fail if not valid JSON or not a dict.
   → Fail if type != "mission".

2. Schema validation
   → Run MissionManifest.validate(manifest) — checks all existing rules.
   → Run new rules: capabilities, requires_approval, risk_level, runtime,
     signed_by, signature, files[], package_digest.
   → Collect all errors; fail with full list (not just first error).

3. Signature verification
   → Strip signature field from manifest.
   → Serialize remaining manifest canonically (per canonicalization doc).
   → Retrieve public key for key_id from verifier's key map.
   → Verify Ed25519 signature over canonical bytes.
   → Fail with "invalid_signature" if verification fails.
   → Fail with "unknown_key_id" if key_id is not in key map.

4. Kill switch check
   → Call KillSwitchProvider.is_revoked(package_id, version).
   → Fail with "package_revoked" if True.

   Sprint 2B implementation:
     KillSwitchProvider — abstract interface: is_revoked(id, version) -> bool
     NoopKillSwitchProvider — default; always returns False (not revoked)
     InMemoryKillSwitchProvider — for tests; allows simulating revoked packages
   No SQLite table is created in Sprint 2B. No cloud or Supabase calls.
   Cloud/Supabase-backed revocation deferred to Sprint 5/6.

5. Package digest verification
   → Extract zip to a temporary directory.
   → Strip reserved paths (macOS artifacts, bytecode, VCS, editor backups).
   → Compute package digest per canonicalization rules.
   → Compare to declared package_digest.
   → Fail with "package_digest_mismatch" if they differ.

6. Per-file hash verification
   → For each entry in files[]:
       - Locate file in extracted zip.
       - Compute SHA-256 of canonical bytes.
       - Compare to declared sha256.
       - Collect mismatches.
   → Verify no extra files exist beyond files[] + reserved paths.
   → Fail with full list of mismatched/extra files if any.

7. Tier verification
   → Call LICENSE_GATE POST /api/license/check_tier with tier_required.
   → Fail with "tier_insufficient" if ok=false.
   → Fail with "license_gate_unavailable" if gate is unreachable
     (fail closed — consistent with Sprint 1 fix).

8. Version compatibility check
   → If min_zyrcon_version is set, compare to running Zyrcon AI Server
     version.
   → Fail with "version_incompatible" if server version is below minimum.

9. Dependency check
   → For each operator in operators.required: verify installed and meets
     version constraint. If missing, prompt user to co-install.
     Install does not proceed unless all required operators are present.
   → For each connector in connectors.required: same behavior.
   → For optional dependencies: list missing ones in install summary,
     continue without blocking.

10. Register and install
    → Move extracted files from temp directory to mission install root.
    → Write entry to MissionRegistry (missions_registry.json).
    → Register workflows with STITCH via register_workflow() (see Section I).
    → Write audit log entry.
    → Return success response with installed mission ID and version.
```

**Note on removed Step 7 (capability approval catalog):** A separate
"approved capabilities record from catalog" check was considered during
design but removed for Sprint 2B. The Zyrcon Ed25519 signature (Step 3)
IS the approval record — Zyrcon would not sign a manifest whose
capabilities had not been reviewed and approved. A separate catalog lookup
would be redundant at install time. Future Sprint 5/6 review pipeline /
cloud catalog approval runs BEFORE signing, not at install time.

**Failure response format:**

```json
{
  "error": "<error_code>",
  "message": "<human-readable explanation>",
  "details": {}
}
```

Error codes are enumerated strings (not HTTP status codes) so callers
can pattern-match without depending on HTTP semantics.

---

## Section I — Registration After Install

### Registration Architecture

Mission packages have two registration targets:

1. **`MissionRegistry`** (`cascadia/missions/registry.py`) — the persisted
   source of truth. Tracks which missions are installed, their capabilities,
   workflow IDs, and STITCH registration status.

2. **STITCH `register_workflow()`** (`cascadia/automation/stitch.py`) —
   in-memory step-based workflow registration. Each workflow declared in
   `mission.json` is registered here so STITCH can execute it.

   **Sprint 2B uses `register_workflow()` (in-memory, step-based format).
   Workflows are NOT stored in `workflow_definitions` (nodes/edges SQLite
   table) and will NOT appear in the PRISM visual designer in Sprint 2B.**
   MissionRegistry is the persisted source of truth; STITCH runtime state
   can be rebuilt from MissionRegistry on restart.
   Conversion to WorkflowStore/nodes-edges (for PRISM designer) is deferred
   to Sprint 4+ (PRISM Store frontend track).

MissionRegistry write is the commit point. STITCH registration is
best-effort: if STITCH is unreachable, the install still succeeds and
returns `stitch_pending: true`. The `stitch_registered` flag in the
MissionRegistry record is set to `false` and retried on next STITCH
startup. Do NOT create a separate `data/runtime/stitch_pending.json`
sidecar file — MissionRegistry is the single source of mission install
state.

### What Gets Stored Where

**MissionRegistry stores (in `missions_registry.json`):**

```json
{
  "installed": [
    {
      "id": "lead_qualification",
      "version": "1.2.0",
      "name": "Lead Qualification Pipeline",
      "tier_required": "pro",
      "runtime": "server",
      "author": "zyrcon-labs",
      "signed_by": "zyrcon-labs",
      "key_id": "zyrcon-2026-q2",
      "install_path": "/path/to/missions/lead_qualification",
      "installed_at": "2026-05-12T10:00:00Z",
      "capabilities": ["crm.read", "crm.write", "email.send"],
      "workflow_ids": ["main", "onboarding"],
      "stitch_registered": true
    }
  ]
}
```

`stitch_registered: false` is written when STITCH is unreachable at install
time. On next STITCH startup, STITCH queries MissionRegistry for any record
with `stitch_registered: false` and re-attempts registration.

**STITCH `WorkflowStore` stores** each workflow file content as a
workflow definition row in the `workflow_definitions` SQLite table.
Workflow IDs are namespaced: `"mission:<mission_id>:<workflow_id>"`
to avoid collisions with manually created workflows.

### CREW → STITCH Notification

After writing to `MissionRegistry` with `stitch_registered: false`, CREW
calls STITCH via the new `register_mission` route:

```
POST http://127.0.0.1:<STITCH_PORT>/api/workflows/register_mission
Body: { "mission_id": "lead_qualification", "install_path": "..." }
```

STITCH reads workflow files from `install_path`, builds `WorkflowDefinition`
objects from the step-based JSON format, and registers them via
`register_workflow()` into STITCH's in-memory `self._workflows` dict.
Workflow IDs are namespaced: `"mission:<mission_id>:<workflow_id>"`.
On success, CREW updates the MissionRegistry record to `stitch_registered: true`.

If STITCH is unreachable, the install still returns 201 with
`stitch_pending: true`. The `stitch_registered: false` flag remains in
MissionRegistry. On next STITCH startup, STITCH queries MissionRegistry
for missions with `stitch_registered: false` and re-attempts registration.
Do NOT create a separate `data/runtime/stitch_pending.json` sidecar.

### PRISM / Mobile Exposure

After registration, missions appear in PRISM's mission list via
`MissionRegistry.list_catalog()`. Mobile discovers available missions
via the existing `/api/missions` endpoint (or equivalent PRISM route).

### Uninstall Flow

Uninstall is two-phase:

**Phase 1 — Dry run (impact assessment):**

```
POST /api/crew/uninstall_mission
{ "mission_id": "lead_qualification", "dry_run": true }
```

Response lists:
- Active workflow runs that would be aborted
- Other missions that depend on shared operators
- Data tables owned by this mission (`database.owned_tables`)
- Estimated data loss (rows in owned tables)

**Phase 2 — Confirmed uninstall:**

```
POST /api/crew/uninstall_mission
{ "mission_id": "lead_qualification", "dry_run": false }
```

Steps:
1. Abort any active workflow runs for this mission
2. Remove mission workflow definitions from STITCH
3. Remove mission from `MissionRegistry`
4. Move mission files to `.removed/<mission_id>_<timestamp>/`
   (30-day recovery window, consistent with existing operator uninstall)
5. Write audit log entry

Owned database tables are NOT dropped automatically. They are flagged as
orphaned, and the user is prompted to confirm table deletion separately
(destructive action with explicit confirmation).

---

## Section J — Open Questions

### J1 — JSON vs YAML manifest format `DECIDED — Use mission.json (not mission.yaml)`

**Background:** The build plan specifies `mission.yaml`. The existing
`MissionManifest` implementation loads `mission.json`. STITCH workflow
files are also JSON.

**Options:**

- **Option A (recommended default):** Keep `mission.json`. Consistent
  with existing implementation and STITCH workflow files. No new parser
  dependency. No risk of YAML edge cases (indentation errors, implicit
  type coercion, multi-document streams). Sprint 2B makes zero changes
  to the file format.
- **Option B:** Migrate to `mission.yaml`. More human-readable for complex
  workflow definitions. Requires PyYAML or ruamel.yaml dependency.
  Requires migrating existing `mission.json` files. Requires updating
  `MissionManifest.load()`.
- **Option C:** Support both (`.json` and `.yaml`), detected by extension.
  Adds complexity with no clear benefit. Not recommended.

**Recommended default if Andy doesn't respond:** Option A (JSON). Proceed
with `mission.json` in Sprint 2B unless Andy explicitly requests YAML.

---

### J2 — Tier naming: `free` vs `lite` `DECIDED — Use "lite" as canonical tier name; "free" is deprecated alias`

**Background:** `MissionManifest` accepts `"free"` as a tier value.
`manifest_validator.py` uses `"lite"`. The build plan and LICENSE_GATE
use `"lite"`.

**Options:**

- **Option A (recommended):** Align everything to `"lite"`. Update
  `MissionManifest._VALID_TIERS` in Sprint 2B. Migrate any existing
  `mission.json` files that use `"free"` (currently only test fixtures
  and the existing `missions_registry.json`). This is a one-line code
  change with a small migration.
- **Option B:** Keep `"free"` in missions, `"lite"` in operators/connectors.
  Requires translation layer in tier checking. Not recommended.

**Recommended default if Andy doesn't respond:** Option A (use `"lite"`
everywhere).

---

### J3 — `risk_level: critical` support in `manifest_validator.py` `DECIDED — Add "critical" to risk_level enum`

**Background:** `entitlements.py` defines 4 risk levels: `low`, `medium`,
`high`, `critical`. `manifest_validator.py` only validates 3: `low`,
`medium`, `high`. Critical is missing from VALID_RISK_LEVELS.

**Options:**

- **Option A (recommended):** Add `"critical"` to `VALID_RISK_LEVELS`
  in Sprint 2B. Backward compatible (existing manifests using
  low/medium/high remain valid).
- **Option B:** Keep `manifest_validator.py` at 3 levels; only mission
  packages support `critical`. Not recommended — inconsistency.

**Recommended default if Andy doesn't respond:** Option A.

---

### J4 — Approval gate timeout: per-step or global `DECIDED — Per-step approval timeout`

**Background:** The spec defines per-step `timeout` on approval gates.
An alternative is a global mission-level approval timeout.

**Options:**

- **Option A (recommended):** Per-step timeout, as specified in Section D.
  More flexible; allows different SLAs for different approval gates in
  the same workflow.
- **Option B:** Global timeout in mission manifest. Simpler but less
  flexible.
- **Option C:** Both — global default with per-step override.

**Recommended default if Andy doesn't respond:** Option A (per-step).

---

### J5 — Sub-missions (nested workflows) `DECIDED — Defer sub-missions/nested workflows to v2`

**Background:** Should a mission workflow step be able to call another
mission's workflow as a sub-workflow?

**Options:**

- **Option A (recommended):** Defer to v2. Sub-missions add significant
  complexity to dependency tracking, version resolution, and uninstall
  impact assessment. Phase 1 ships flat workflows only.
- **Option B:** Allow sub-workflows (within the same mission package only).
  This is already partially supported by STITCH's step model.

**Recommended default if Andy doesn't respond:** Option A (no sub-missions
in v1).

---

### J6 — Where does the public key bundle live? `DECIDED — Static bundle cascadia/depot/zyrcon_signing_keys.json for Sprint 2B; API fetch (api.zyrcon.store/v1/signing-keys) in Sprint 5`

**Background:** Verifiers need a map of `key_id → public_key`. This
bundle must be distributed with the Zyrcon AI Server software.

**Options:**

- **Option A (recommended):** Ship a `zyrcon_signing_keys.json` file as
  part of the `cascadia-os` repo (in `cascadia/depot/` or `cascadia/shared/`).
  Updated via normal software releases. No network call at install time.
- **Option B:** Fetch key bundle from `api.zyrcon.store/v1/signing-keys`
  at install time. Allows key rotation without a software release, but
  requires network access and adds an attack surface.
- **Option C:** Embed in config.json. Not recommended — key bundle is
  code, not operator configuration.

**Recommended default if Andy doesn't respond:** Option A (static bundle
in repo, updated with releases).

---

### J7 — Mission install root path `DECIDED — Use existing missions.packages_root key in config.json`

**Background:** Where do mission packages get extracted on disk?

**Options:**

- **Option A (recommended):** `config.json` key `missions.packages_root`
  (already exists in config.json as a `missions` section). Resolved via
  `MissionRegistry._resolve_root()` which already handles env var
  fallback.
- **Option B:** Hardcode adjacent to operators dir. Not recommended — no
  hardcoded paths.

**Recommended default if Andy doesn't respond:** Option A (use existing
`missions.packages_root` config key).
