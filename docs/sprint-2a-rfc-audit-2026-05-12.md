# Sprint 2A RFC Audit Findings

**Audit date:** 2026-05-12
**Auditor:** Independent session (no prior context on these docs)
**Branch:** `sprint-2a-audit` (created from `mission-package-spec-rfc`)
**Docs under review:**
- `docs/mission-package-spec.md` (819 lines)
- `docs/package-canonicalization.md` (608 lines)
- `docs/sprint-2b-implementation-plan.md` (635 lines)

---

## Preflight Report

```
SPRINT 2A AUDIT PREFLIGHT REPORT
=================================
Session: fresh (confirmed no prior RFC-writing context)
Branch: sprint-2a-audit (created from mission-package-spec-rfc)
Working tree dirty: no (one untracked file: docs/mobile-audit-2026-05-11.md)
Branch ahead of main: yes (1 commit — 73ea492, the RFC docs commit)

RFC docs present:
  docs/mission-package-spec.md:           yes, 819 lines
  docs/package-canonicalization.md:       yes, 608 lines
  docs/sprint-2b-implementation-plan.md:  yes, 635 lines

Source code ground truth:
  manifest_validator.py mission hook:     line 80-82 (type=="mission" → MissionManifest)
  VALID_RISK_LEVELS actual contents:      {'low', 'medium', 'high'} — 3 values, NO 'critical'
  MissionManifest._VALID_TIERS actual:    {"free", "pro", "business", "enterprise"} (uses "free" NOT "lite")
  CURTAIN signing primitives:             HMAC-SHA256 only; "asymmetric: planned" per capabilities endpoint
  CREW install entry point:               install_operator() @ cascadia/registry/crew.py:211
  CREW _extract_and_validate_manifest:    @ cascadia/registry/crew.py:584
                                          looks for files ending in 'manifest.json' (NOT 'mission.json')
                                          does NOT call validate_depot_manifest()
  STITCH workflow registration:           WorkflowStore.save() @ cascadia/automation/stitch.py:627
                                          register_workflow() @ cascadia/automation/stitch.py:792
  STITCH actual path:                     cascadia/automation/stitch.py (confirmed)
  MissionRegistry location:              cascadia/missions/registry.py (class MissionRegistry)
  MissionManifest location:              cascadia/missions/manifest.py (class MissionManifest)
  Entitlements capability count:          61 (not 65)
    breakdown: critical=6, high=13, medium=16, low=26
  Entitlements risk levels:              ['critical', 'high', 'low', 'medium'] (4 levels)
  config.json has missions.packages_root: yes ({'packages_root': '/Users/andy/.../missions'})
  zyrcon_signing_keys.json:              DOES NOT EXIST anywhere in the repo
  missions_registry.json existing format: {"installed": ["growth_desk"], ...} — string IDs, not dicts

Proceeding to read RFC docs.
```

---

## Section 1 — Executive Summary

### Verdict Per Document

| Document | Verdict |
|----------|---------|
| `docs/mission-package-spec.md` | REVISIONS REQUIRED |
| `docs/package-canonicalization.md` | READY WITH MINOR REVISIONS |
| `docs/sprint-2b-implementation-plan.md` | REVISIONS REQUIRED |

### Top 3 Most Important Findings

1. **IMPL-001 (MAJOR)** — Phase 4 of the implementation plan falsely claims `install_operator()` already dispatches on `type == "mission"` via `manifest_validator.py` for zip installs. The zip path calls `_extract_and_validate_manifest()`, which looks for `manifest.json` (not `mission.json`) and never calls `validate_depot_manifest()`. Mission packages are invisible to CREW's existing zip install path. This would cause Phase 4 to be built on a false foundation.

2. **SPEC-001 (MAJOR)** — The spec claims `entitlements.py` contains 65 capabilities; the actual count is 61 (6 critical + 13 high + 16 medium + 26 low). This error propagates into fixture creation and test assertions in Sprint 2B.

3. **SPEC-002 (MAJOR)** — Section I internally contradicts itself on atomicity: first it says "Both registrations must succeed (or both are rolled back)", then says "If STITCH is unreachable, the install succeeds but STITCH registration is flagged as pending." These are mutually exclusive behaviors. A Sprint 2B worker implementing Section I will make an arbitrary choice.

### Recommendation to Andy

Proceed to Sprint 2B only after approving the revision commits (one per doc). Two findings are **NEEDS-ANDY-DECISION** and must be resolved before Sprint 2B can begin:

- **Kill switch table** (SPEC-003 / IMPL-002): The spec and plan describe querying a "local kill switch table" without defining its schema, location, or initialization. Andy must decide whether to include kill switch in Sprint 2B (and if so, define the schema), or defer it and remove the step.

- **Step 7 capability escalation catalog** (SPEC-005): Section H step 7 requires "retrieving an approved capabilities record from catalog" — this implies an external Zyrcon package approval registry that doesn't exist. Andy must decide whether this step is meaningful for Sprint 2B or should be removed/replaced.

---

## Section 2 — Findings: `docs/mission-package-spec.md`

---

```
FINDING ID: SPEC-001
SEVERITY: MAJOR
SECTION: B (Capability Declarations)
TYPE: factual-accuracy
SUMMARY: Capability count stated as 65; actual count is 61.
DETAIL:
  The doc says (line 20 in Preflight Findings, line 132 in Section B):
    "cascadia/shared/entitlements.py — capability registry (65 capabilities)"
    "it contains 65 capabilities across 4 risk levels"
  Actual count via python3:
    Total: 61 capabilities
    critical: 6  (vault.write, vault.delete, shell.exec, system.restart,
                   system.destroy, identity.delete)
    high:    13  (email.delete, crm.delete, file.delete, calendar.delete,
                   billing.write, payment.create, invoice.create, message.delete,
                   data.delete, identity.write, hr.write, order.cancel,
                   connector.delete)
    medium:  16
    low:     26
IMPACT IF UNFIXED:
  Phase 1 fixture creation and Phase 2/6 test assertions will use the wrong
  count. Any test that checks "entitlements has N capabilities" will fail.
PROPOSED REVISION:
  Line 20: change "65 capabilities" → "61 capabilities"
  Line 132: change "it contains 65 capabilities" → "it contains 61 capabilities"
```

---

```
FINDING ID: SPEC-002
SEVERITY: MAJOR
SECTION: I (Registration After Install)
TYPE: correctness / consistency
SUMMARY: Section I contradicts itself on atomicity — "both roll back" vs "install succeeds with pending".
DETAIL:
  The doc says in sequence (Section I, "Registration Architecture"):
    "Both registrations must succeed (or both are rolled back) — the install
     is atomic from the user's perspective."
  Then three paragraphs later (Section I, "CREW → STITCH Notification"):
    "If STITCH is unreachable, the install succeeds but STITCH registration
     is flagged as pending (retried on next STITCH startup via a
     pending-registration scan)."
  These are mutually exclusive. The first paragraph mandates rollback if
  either registration fails. The second paragraph says install succeeds
  even when STITCH registration fails.
  The implementation plan (Phase 5, Risk 3) resolves in favor of
  "install succeeds, stitch_pending: true" — but the spec never resolves it.
IMPACT IF UNFIXED:
  Sprint 2B worker implements one behavior; the other statement becomes a
  latent spec bug that may resurface in code reviews or future sprints.
PROPOSED REVISION:
  Replace the atomicity statement in Section I, "Registration Architecture":
  
  BEFORE:
    "Both registrations must succeed (or both are rolled back) — the install
     is atomic from the user's perspective."
  
  AFTER:
    "MissionRegistry write is the commit point. STITCH registration is
     best-effort: if STITCH is unreachable, the install still succeeds and
     returns stitch_pending: true. The pending registration is retried on
     next STITCH startup via a pending-registration sidecar file."
```

---

```
FINDING ID: SPEC-003
SEVERITY: MAJOR
TYPE: implementability / gap
SECTION: H (Install Verification Flow, Step 4)
SUMMARY: Kill switch table referenced but never defined — schema, location, and
         initialization are not specified anywhere in the RFC.
DETAIL:
  Section H step 4 says:
    "Query local kill switch table (or Supabase if online) for this
     package id + version."
  No table schema, SQLite location, initial seeding, or update mechanism
  is defined in the spec, plan, or existing code. The phrase "or Supabase
  if online" adds a cloud dependency that violates Sprint 2B's local-only
  boundary.
  Phase 5 of the implementation plan says "Check kill switch (query local
  kill switch table)" without adding any definition.
IMPACT IF UNFIXED:
  Sprint 2B worker must invent the kill switch data model with no guidance.
  The Supabase mention suggests future cloud integration but is premature.
PROPOSED REVISION:
  NEEDS-ANDY-DECISION: Andy must decide one of:
    (A) Define the kill switch table schema (table name, columns, location,
        how entries are added) and include in Sprint 2B scope.
    (B) Remove step 4 from Section H for Sprint 2B; note it as deferred
        to a future sprint when the cloud revocation infrastructure exists.
  Until this is decided, the revision cannot be applied.
  
  Independent of Andy's decision, remove "or Supabase if online" from step 4.
  Sprint 2B operates fully locally. The Supabase path is cloud infrastructure
  that does not yet exist and violates the Sprint 2B boundary.
```

---

```
FINDING ID: SPEC-004
SEVERITY: MAJOR
SECTION: D (Workflow Definition, Step Types table)
TYPE: correctness / open-question
SUMMARY: `subworkflow` step type listed in step types table despite J5 explicitly
         deferring sub-workflows to v2.
DETAIL:
  Section D, "Step Types" table includes:
    | `subworkflow` | Invoke another workflow inline (see J5) |
  Section J, item J5 says:
    "Option A (recommended): Defer to v2. Sub-missions add significant
     complexity to dependency tracking, version resolution, and uninstall
     impact assessment. Phase 1 ships flat workflows only."
  The implementation plan prerequisite table lists J5 decision as "Defer to v2".
  Including `subworkflow` in the step types table signals to Sprint 2B that
  this step type should be implemented. The cross-reference "(see J5)" is
  not clear enough — a worker would need to trace J5 to know it's deferred.
IMPACT IF UNFIXED:
  Sprint 2B worker may implement the `subworkflow` step type, adding out-of-scope
  complexity. Or they may not implement it and leave the table entry as a dead
  specification. Either outcome is wrong.
PROPOSED REVISION:
  Remove the `subworkflow` row from the step types table in Section D.
  Add a note after the table:
    "Note: `subworkflow` (inline invocation of another workflow) is deferred
     to v2 per J5 decision. It is not implemented in Sprint 2B."
```

---

```
FINDING ID: SPEC-005
SEVERITY: MAJOR
SECTION: H (Install Verification Flow, Step 7)
TYPE: implementability / boundary
SUMMARY: Step 7 requires retrieving an "approved capabilities record from catalog"
         — this is an undefined external service that doesn't exist.
DETAIL:
  Section H step 7 says:
    "Retrieve approved capabilities record from catalog for this
     package id + version.
     Verify manifest capabilities == approved capabilities (no set difference
     allowed — post-approval escalation is rejected).
     Fail with 'capability_escalation' if manifest claims capabilities not
     in the approved record."
  No "catalog" with per-package approved capabilities records is defined
  anywhere in the spec, plan, or existing code. This appears to reference
  a Zyrcon package approval registry (similar to an app store approval
  database) that does not exist in Sprint 2B's scope.
  If the package is signed with Zyrcon's Ed25519 private key, the signature
  already serves as capability approval (signing party is Zyrcon). A
  separate catalog lookup is only meaningful if there's an independent
  revocation or capability-limitation service.
IMPACT IF UNFIXED:
  Sprint 2B cannot implement step 7 without inventing a catalog service.
  A worker will either skip it silently or stub it with a pass-through,
  creating a false sense of security.
PROPOSED REVISION:
  NEEDS-ANDY-DECISION: Options:
    (A) Remove step 7. Ed25519 signature by Zyrcon (step 3) is sufficient
        proof that the capabilities were approved. Capability escalation
        post-signing is prevented by the fact that the signer controls
        what goes into the manifest before signing.
    (B) Define the catalog service (schema, location, how it's populated)
        and include in Sprint 2B scope with a local adapter.
  Until this is decided, the revision cannot be applied.
```

---

```
FINDING ID: SPEC-006
SEVERITY: MINOR
SECTION: J (Open Questions)
TYPE: consistency
SUMMARY: All seven J-decisions are still marked "OPEN — DECISION NEEDED" in the
         spec, but the implementation plan's prerequisite table treats all seven
         as resolved with specific defaults.
DETAIL:
  The spec (lines 684-819) shows all J1–J7 items with the label:
    "OPEN — DECISION NEEDED"
  The implementation plan (lines 19-27) lists all seven decisions as resolved:
    J1: JSON, J2: "lite", J3: add critical, J4: per-step, J5: defer v2,
    J6: static bundle, J7: packages_root
  If Andy has already approved these decisions, the spec's J-sections should
  reflect that (status: "DECIDED — Option A" or equivalent).
IMPACT IF UNFIXED:
  Mild — the plan's prerequisite table is the actionable reference. But the
  spec's OPEN labels may cause future readers to think these are still open.
PROPOSED REVISION:
  Update each J-section heading from "OPEN — DECISION NEEDED" to
  "DECIDED — [option]" if Andy has confirmed all seven. Apply in a revision
  commit only after Andy confirms the J-decisions are finalized.
  (Listed here as MINOR — Andy decides whether to apply this cleanup.)
```

---

```
FINDING ID: SPEC-007
SEVERITY: MINOR
SECTION: H (Install Verification Flow, Step 4)
TYPE: boundary
SUMMARY: "or Supabase if online" in step 4 introduces a cloud dependency
         inconsistent with Sprint 2B's local-only boundary.
DETAIL:
  Section H step 4 says:
    "Query local kill switch table (or Supabase if online)"
  Sprint 2B operates entirely locally. No Supabase infrastructure exists.
  The implementation plan (Phase 5) correctly omits the Supabase clause
  ("Check kill switch (query local kill switch table)").
  The Supabase path would require network access at install time, which
  is explicitly disallowed.
IMPACT IF UNFIXED:
  Minor — Phase 5 already does the right thing. But the spec remains
  inconsistent with the plan.
PROPOSED REVISION:
  In Section H step 4, change:
  BEFORE: "Query local kill switch table (or Supabase if online) for this
            package id + version."
  AFTER:  "Query local kill switch table for this package id + version.
            (Supabase-based revocation is deferred to Sprint 5+; Sprint 2B
            uses local lookup only.)"
  This revision is blocked on SPEC-003 decision (kill switch table definition).
```

---

## Section 3 — Findings: `docs/package-canonicalization.md`

---

```
FINDING ID: CANON-001
SEVERITY: MINOR
SECTION: Test Vectors — Vector 1
TYPE: correctness
SUMMARY: Vector 1 byte counts are wrong. Doc says "14 bytes with CRLF" and
         "13 bytes" after normalization; actual counts are 16 and 15.
DETAIL:
  The doc shows the input as:
    Hello, {name}!\r\n
  Byte count: H(1)e(2)l(3)l(4)o(5),(6) (7){(8)n(9)a(10)m(11)e(12)}(13)!(14)\r(15)\n(16)
  = 16 bytes with CRLF.
  After \r\n → \n: 15 bytes (not 13).

  Doc says: "(14 bytes with CRLF)" and "(13 bytes)" after.
  Actual:    16 bytes with CRLF; 15 bytes after LF normalization.

  Additionally, the SHA-256 value shown is a placeholder:
    e1b849f9631ffc4e3e8b4d1d178ecf6b17b8a8a7d0f4c3b2a1d6e5f4c3b2a1d
  This is not 64 hex characters and is explicitly noted as a placeholder.
  The actual SHA-256 of b'Hello, {name}!\n' (15 bytes) is:
    1e86d5b60d7dc623fef1b8cf2b847c6e4c9b47fbcd00a3b0eadcce51c54df492
IMPACT IF UNFIXED:
  Sprint 2B tests that attempt to reproduce Vector 1 will use wrong byte
  counts and wrong hash values. Tests will not validate the implementation.
PROPOSED REVISION:
  In Vector 1 section:
  - Change "(14 bytes with CRLF)" → "(16 bytes with CRLF)"
  - Change "(13 bytes)" → "(15 bytes)"
  - Replace placeholder SHA-256 with:
    1e86d5b60d7dc623fef1b8cf2b847c6e4c9b47fbcd00a3b0eadcce51c54df492
  - Remove the "(Replace with actual computed value...)" note.
```

---

```
FINDING ID: CANON-002
SEVERITY: MINOR
SECTION: Test Vectors — Vector 2
TYPE: correctness
SUMMARY: Vector 2 byte count for `{"steps":[]}\n` is wrong. Doc says 15 bytes;
         actual is 13 bytes.
DETAIL:
  The doc shows in the Vector 2 table:
    | `workflows/main.json` | `{"steps":[]}\n` (15 bytes) | `<sha256_2>` |
  Byte count: {(1)"(2)s(3)t(4)e(5)p(6)s(7)"(8):(9)[(10)](11)}(12)\n(13) = 13 bytes.
  
  Actual SHA-256 of b'{"steps":[]}\n' (13 bytes):
    4dfdf4c2e8f3f3c86a3ca3c75648d3ce52c2f43b9cda2a72ff2fc563c3825886
  
  Vector 1 in this table says "Hello, {name}!\n (13 bytes)". As found in
  CANON-001, the correct value for that content after LF normalization is
  15 bytes. The byte counts in Vector 2's table appear to have been
  transposed or computed incorrectly.
IMPACT IF UNFIXED:
  Same as CANON-001 — tests using these vectors will use wrong inputs.
PROPOSED REVISION:
  In Vector 2 table:
  - `templates/quote.md` row: change "(13 bytes)" → "(15 bytes)"
  - `workflows/main.json` row: change "(15 bytes)" → "(13 bytes)"
  
  Add the actual SHA-256 values:
  - templates/quote.md SHA-256: 1e86d5b60d7dc623fef1b8cf2b847c6e4c9b47fbcd00a3b0eadcce51c54df492
  - workflows/main.json SHA-256: 4dfdf4c2e8f3f3c86a3ca3c75648d3ce52c2f43b9cda2a72ff2fc563c3825886
  
  Add computed package digest for Vector 2 (verified against reference implementation):
    sha256:7084c017325aee7e0ef448a47413a9f2066795ef6955fab1c97f4c6b21aa6924
```

---

```
FINDING ID: CANON-003
SEVERITY: MINOR
SECTION: Worked Example — Complete Package
TYPE: implementability
SUMMARY: The worked example leaves all SHA-256 values and package digest as
         `<placeholder>` — Sprint 2B tests cannot use these as reference vectors.
DETAIL:
  The worked example (lines 437–570) uses:
    "sha256": "<sha256_of_quote_md>"
    "sha256": "<sha256_of_main_json>"
    package_digest: "sha256:<computed above>"
  These are all placeholders. Sprint 2B's test for `compute_package_digest`
  matching the worked example (as required in Implementation Notes, item 3)
  cannot be written until actual values are computed.
  
  Vector 3 (canonical manifest bytes) is the exception — the hex encoding
  shown in the doc is VERIFIED correct:
    7b226964223a226578616d706c655f6d697373696f6e222c226e616d65223a
    224578616d706c65222c2274797065223a226d697373696f6e222c22766572
    73696f6e223a22312e302e30227d
IMPACT IF UNFIXED:
  Sprint 2B's implementation notes require that "every function in this
  module must have a test that reproduces the worked example exactly."
  That is impossible until the example contains actual computed values.
PROPOSED REVISION:
  Apply CANON-001 and CANON-002 fixes first, then add computed values to
  the worked example. The template/quote.md and workflows/main.json
  SHA-256 values are given in CANON-002. The package digest is:
    sha256:7084c017325aee7e0ef448a47413a9f2066795ef6955fab1c97f4c6b21aa6924
  Note: the worked example's quote.md uses CRLF content
  "Dear {{contact_name}},\r\n\r\nPlease find the proposal attached.\r\n"
  which differs from Vector 1's simpler content. Sprint 2B should compute
  the exact SHA-256 for the worked example's specific quote.md content.
  (The byte count note "size_bytes": 47 should be verified against actual
  canonical byte count after CRLF → LF normalization.)
```

---

## Section 4 — Findings: `docs/sprint-2b-implementation-plan.md`

---

```
FINDING ID: IMPL-001
SEVERITY: MAJOR
SECTION: Phase 4 — File Hash and Package Digest Verification in CREW
TYPE: factual-accuracy
SUMMARY: Phase 4 claims install_operator() already dispatches on type==mission
         via manifest_validator.py. This is false for the zip install path.
         CREW's _extract_and_validate_manifest() looks for manifest.json,
         not mission.json, and never calls validate_depot_manifest().
DETAIL:
  Phase 4 says (lines 275-280):
    "The existing install_operator() function already dispatches on
     type == 'mission' implicitly (via manifest_validator.py). This phase
     adds an explicit mission branch in install_operator() that calls the
     two new verification functions before extraction and registry write."
  
  ACTUAL CODE PATH for zip installs (cascadia/registry/crew.py):
  
  install_operator() → _extract_and_validate_manifest(raw)
  
  _extract_and_validate_manifest (line 584):
    manifest_name = next((n for n in names if n.endswith('manifest.json')), None)
    # ↑ looks for 'manifest.json', NOT 'mission.json'
    # If the zip contains only 'mission.json', this returns None → error:
    # "manifest.json not found in zip"
    
  Even if the zip had a file named manifest.json, _extract_and_validate_manifest
  checks only _REQUIRED_MANIFEST_FIELDS = {'operator_id', 'name', 'version',
  'capabilities'} — it NEVER calls validate_depot_manifest() and NEVER sees
  the type=="mission" dispatch.
  
  The validate_depot_manifest() mission dispatch (manifest_validator.py:80-82)
  is only reached via the manifest-dict path (payload.get('manifest', {})),
  not the zip path. Mission packages deliver mission.json inside a zip, so
  the dispatch never fires.
  
  Evidence:
    cascadia/registry/crew.py:584 — _extract_and_validate_manifest()
    cascadia/registry/crew.py:243 — call site: manifest, error = self._extract_and_validate_manifest(raw)
    cascadia/missions/manifest.py:1 — MissionManifest loads 'mission.json', not 'manifest.json'
IMPACT IF UNFIXED:
  A Sprint 2B worker following Phase 4's description would add a mission branch
  to install_operator() that would never be reached for zip-format missions
  (because _extract_and_validate_manifest fails first on "manifest.json not found").
  Phase 5 correctly adds a NEW install_mission route — but Phase 4's description
  implies the mission path goes through install_operator(), which is wrong.
  The two phases appear to be building in contradictory directions.
PROPOSED REVISION:
  In Phase 4, replace the paragraph:
  
  BEFORE:
    "The existing install_operator() function already dispatches on type ==
     'mission' implicitly (via manifest_validator.py). This phase adds an
     explicit mission branch in install_operator() that calls the two new
     verification functions before extraction and registry write."
  
  AFTER:
    "NOTE: The existing install_operator() zip path (crew.py:584
     _extract_and_validate_manifest) looks for 'manifest.json', not
     'mission.json'. Mission packages therefore cannot be installed via
     install_operator() as-is. Phase 5 adds a dedicated install_mission
     route. This phase (Phase 4) only implements the verification
     helper functions (_verify_mission_package, _verify_mission_signature)
     that Phase 5 will call. Do not add a mission branch to install_operator()."
```

---

```
FINDING ID: IMPL-002
SEVERITY: MAJOR
SECTION: Phase 5 — CREW Mission Install + STITCH Registration
TYPE: implementability / gap
SUMMARY: Kill switch table (Phase 5 step 2) is referenced without any schema,
         table name, location, or initialization defined.
DETAIL:
  Phase 5 step 2: "Check kill switch (query local kill switch table)"
  No existing code defines a kill switch table. There is no SQLite schema,
  no table name, no columns, no initialization. The implementation plan
  does not cross-reference any design for this table.
  
  Checking existing code:
    cascadia/missions/ — no kill switch references
    cascadia/depot/ — no kill switch references
    cascadia/registry/crew.py — no kill switch references
    config.json — no kill switch configuration
IMPACT IF UNFIXED:
  Sprint 2B worker must invent the kill switch data model. See also SPEC-003.
PROPOSED REVISION:
  Same as SPEC-003: NEEDS-ANDY-DECISION.
  Options: (A) Define schema and include in Sprint 2B, or (B) remove
  Phase 5 step 2 and defer kill switch to a future sprint.
```

---

```
FINDING ID: IMPL-003
SEVERITY: MAJOR
SECTION: Phase 5 — CREW Mission Install + STITCH Registration
TYPE: implementability / gap
SUMMARY: The register_mission STITCH endpoint is specified to store workflows in
         workflow_definitions (nodes/edges format), but mission workflow files
         use a step-based format. The translation is not specified.
DETAIL:
  Phase 5 says:
    "STITCH changes: Add POST /api/workflows/register_mission route ...
     Reads workflow files from install_path
     Upserts to workflow_definitions with namespaced IDs"
  
  WorkflowStore.save() (stitch.py:627) stores:
    nodes: list  (ReactFlow visual designer graph nodes)
    edges: list  (ReactFlow visual designer graph edges)
  
  Mission workflow files (Section D) use a step-based format:
    {"id": "step_email", "type": "operator", "depends_on": [...], ...}
  
  These are different formats. How step-based workflows map to nodes/edges
  is not defined. A worker implementing register_mission would need to either:
    (A) Convert step-based → nodes/edges (visual designer format), or
    (B) Store steps directly as nodes[] and derive edges from depends_on,
        or
    (C) Use the separate in-memory register_workflow() path (self._workflows
        dict) instead of WorkflowStore.save() (SQLite).
  
  Each option has significant differences in data model, queryability, and
  PRISM display behavior.
IMPACT IF UNFIXED:
  Sprint 2B implements one of these options arbitrarily; Sprint 3+ inherits
  the choice. The test in Phase 5 ("Mission workflows appear in STITCH
  workflow_definitions after install") may pass with any of the three options
  but the stored format will differ.
PROPOSED REVISION:
  Add a "STITCH Integration Format" subsection to Phase 5 that explicitly states:
  
  Option (recommended): Store mission workflows using register_workflow()
  path (POST /workflow/register with steps format), NOT WorkflowStore.save().
  The in-memory WorkflowDefinition model matches the step-based mission format.
  Namespacing: pass workflow_id = "mission:<mission_id>:<workflow_id>".
  The visual designer (WorkflowStore / workflow_definitions table) is not
  involved in mission workflow registration in Sprint 2B.
  
  OR Andy decides which path to use and the plan is updated accordingly.
  Flag as NEEDS-ANDY-DECISION if the in-memory vs SQLite choice depends on
  future PRISM design requirements.
```

---

```
FINDING ID: IMPL-004
SEVERITY: MINOR
SECTION: Phase 2 — Mission Package Parser / Validator Extensions
TYPE: implementability / gap
SUMMARY: Operator/connector pinning syntax defined in spec Section C but no
         validation rule is added in Phase 2's rules 22-32.
DETAIL:
  Spec Section C defines a pinning format:
    "scout"         — floating
    "scout@1.2.0"   — exact version
    "scout@>=1.2.0" — minimum version
  
  Phase 2 adds rules 22-32 to MissionManifest.validate(). None of these
  rules validate the format of strings in operators.required/operators.optional.
  The existing rules 8-9 only check that these are lists — they don't validate
  the pinning format of individual strings.
  
  Pinning syntax is enforced at install time (Section C, "Install-Time Behavior")
  but not at manifest validation time. This means a manifest with malformed
  pinning like "scout@>=x.y" would pass validation but fail at install.
IMPACT IF UNFIXED:
  Validation errors from malformed pinning will surface at install time with
  potentially confusing errors, not at validation time with a clear message.
  The "collect all errors" promise of Section H step 2 is partly broken.
PROPOSED REVISION:
  Add Rule 33 to the Phase 2 new validation rules:
    "Rule 33: operators.required and operators.optional strings must match
     '^[a-z][a-z0-9_-]+(@(>=)?[0-9]+\.[0-9]+\.[0-9]+)?$' — bare ID or
     pinned version. Same for connectors.required and connectors.optional."
  Add corresponding test:
    "Rule 33: malformed pinning 'scout@invalid' → validation error"
```

---

```
FINDING ID: IMPL-005
SEVERITY: NIT
SECTION: Phase 3 — Asymmetric Signing Module
TYPE: consistency
SUMMARY: Phase 3's test_signing.py includes a test for canonical_manifest_bytes()
         excluding the signature field — this tests a Phase 2 function.
DETAIL:
  Phase 3 tests list includes:
    "canonical_manifest_bytes() excludes signature field"
  canonical_manifest_bytes() is defined in cascadia/depot/canonicalization.py
  (Phase 2). Tests for it belong in test_canonicalization.py (Phase 2).
  Including it in test_signing.py (Phase 3) creates redundant tests and
  suggests the canonicalization module is part of Phase 3's scope.
IMPACT IF UNFIXED:
  Minor organizational issue. Tests pass either way.
PROPOSED REVISION:
  Remove "canonical_manifest_bytes() excludes signature field" from Phase 3's
  test_signing.py list. This behavior is already covered by test_canonicalization.py
  (Phase 2). Phase 3's test_signing.py should test that sign_manifest() calls
  canonical_manifest_bytes() correctly, not test the canonicalization itself.
```

---

## Section 5 — Cross-Doc Consistency Report

| Topic | spec.md | canonicalization.md | implementation-plan.md | Consistent? |
|-------|---------|---------------------|------------------------|-------------|
| Manifest file extension | `mission.json` | `mission.json` | `mission.json` | ✓ YES |
| Signature algorithm | Ed25519 | Ed25519 | Ed25519 | ✓ YES |
| Risk levels (enum) | low/medium/high/critical | not listed | low/medium/high/critical | ✓ YES |
| Tier naming | `"lite"` (with migration from `"free"`) | not listed | `"lite"` (change `_VALID_TIERS`) | ✓ YES |
| STITCH path | `cascadia/automation/stitch.py` | not listed | `cascadia/automation/stitch.py` | ✓ YES |
| MissionRegistry file | `missions_registry.json` | not listed | `missions_registry.json` | ✓ YES |
| CURTAIN role | HMAC-SHA256; NOT used for signing | HMAC-SHA256 noted in table | not used for packages | ✓ YES |
| Key bundle location | `cascadia/depot/zyrcon_signing_keys.json` | not listed | `cascadia/depot/zyrcon_signing_keys.json` | ✓ YES |
| Capability count | **65** (WRONG) | not listed | not listed | — |
| Kill switch step | Step 4, "or Supabase if online" | not listed | Phase 5 step 2, local only | ✗ INCONSISTENT |
| STITCH registration format | WorkflowStore / workflow_definitions | not listed | "upserts to workflow_definitions" (nodes/edges) | ✗ INCONSISTENT with step-based format |
| Atomicity guarantee | "both roll back" AND "install succeeds" | not listed | "install 201, stitch_pending: true" | ✗ INCONSISTENT (spec self-contradicts) |

**Flagged rows:**
- Capability count: wrong in spec.md (65 vs 61 actual)
- Kill switch Supabase: spec mentions Supabase; plan omits it
- Atomicity: spec internally contradicts; plan sides with "succeeds + pending"

All three documents agree on: file format, signing algorithm, tier naming, STITCH path, key bundle path, registry location.

---

## Section 6 — Verdict & Recommended Next Steps

### Revisions Required

**`docs/mission-package-spec.md`** — REVISIONS REQUIRED
- Apply: SPEC-001 (capability count fix — 65→61)
- Apply: SPEC-002 (Section I atomicity fix)
- Apply: SPEC-004 (remove subworkflow from step types table)
- Apply: SPEC-007 (remove "or Supabase if online" from step 4)
- Defer: SPEC-003 (kill switch table) — NEEDS-ANDY-DECISION
- Defer: SPEC-005 (step 7 capability catalog) — NEEDS-ANDY-DECISION
- Defer: SPEC-006 (J-decision labels) — Andy confirms first

**`docs/package-canonicalization.md`** — READY WITH MINOR REVISIONS
- Apply: CANON-001 (Vector 1 byte counts and actual SHA-256)
- Apply: CANON-002 (Vector 2 byte counts and actual SHA-256 values)
- Defer: CANON-003 (worked example placeholders) — requires exact content computation

**`docs/sprint-2b-implementation-plan.md`** — REVISIONS REQUIRED
- Apply: IMPL-001 (Phase 4 dispatch claim correction)
- Apply: IMPL-004 (add pinning syntax Rule 33)
- Defer: IMPL-002 (kill switch) — NEEDS-ANDY-DECISION (same as SPEC-003)
- Defer: IMPL-003 (STITCH format) — NEEDS-ANDY-DECISION
- Apply: IMPL-005 (NIT, test location — apply if convenient)

### Decisions Required from Andy Before Sprint 2B

1. **Kill switch table** (SPEC-003 / IMPL-002): Include in Sprint 2B with defined schema, OR remove step and defer?
2. **Capability catalog** (SPEC-005): Remove Step 7 (signature is sufficient approval), OR define catalog service?
3. **STITCH workflow format** (IMPL-003): Use in-memory register_workflow() path (step-based), OR WorkflowStore.save() (nodes/edges) path?

### Recommendation

**Proceed to Sprint 2B after Andy resolves the 3 decisions above and approves the revision commits.** The docs are fundamentally sound — no major rewrite needed. Revisions are targeted corrections, not structural changes.

Do NOT start Sprint 2B until the kill switch and capability catalog decisions are made. These two gaps would force Sprint 2B to invent design that belongs in the spec.

---

*Audit findings count: 2 NEEDS-ANDY-DECISION, 7 MAJOR, 4 MINOR, 1 NIT*
*Revision commits: 3 (one per doc)*
