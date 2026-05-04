# Changelog

> Cascadia OS uses Calendar Versioning (CalVer): YYYY.M
> Current release: 2026.5 — May 2026

---

## 2026.5 (May 2026)

### Changed
- Renamed internal liveness signal from `heartbeat` to `pulse` throughout the codebase.
  Config keys `heartbeat_file`, `heartbeat_interval_seconds`, `heartbeat_stale_after_seconds`
  renamed to `pulse_file`, `pulse_interval_seconds`, `pulse_stale_after_seconds`.
- Runtime files renamed from `*.heartbeat` to `*.pulse` in `data/runtime/`.
- `_heartbeat_loop()` → `_pulse_loop()` in `service_runtime.py` and `flint.py`.
- FLINT thread renamed from `flint-hb` to `flint-pulse`.
- Error messages: `'heartbeat stale'` → `'pulse stale'`, `'missing heartbeat'` → `'no pulse'`.

### Backward Compatibility
- Config migration shim in `cascadia/shared/config.py` accepts old `heartbeat_*` keys
  with a deprecation warning. Shim scheduled for removal in 2026.7.
- Runtime file migration (`_migrate_heartbeat_files`) runs automatically on watchdog
  startup — renames any existing `.heartbeat` files to `.pulse`.

### Out-of-box experience
- First-run auto-seed: `cascadia/installer/first_run.py` detects fresh
  install and seeds demo data automatically
- PRISM approvals empty state now shows "Your AI is running" + Run Demo
  Workflow button
- POST `/api/prism/demo/trigger` and GET `/api/prism/demo/first-run-status`
  routes added
- First-run welcome banner added to PRISM (dismissable)
- Growth Desk daily campaign wired into scheduler (MON–FRI 09:00)
- `start.sh` attempts to start CHIEF and SOCIAL from operators repo with
  graceful fallback
- `install.sh` now opens Approvals tab on first install
- Setup wizard expanded to 8 steps — new Step 3 covers AI model status
  and demo trigger

---

## 2026.4 (April 2026)

### Added
- Mission system: Revenue Desk, Growth Campaigns, Operations Desk
- Mission Runner with full lifecycle management wrapping STITCH
- Mission Manager API on port 6207
- `mission_items` table and full CRUD API (GET / POST / PATCH)
- Revenue Desk email scanning pipeline — classifies leads, quotes, POs, invoices
- Urgency and value scoring for mission items (0–100 scale)
- PRISM proxy endpoints for mission items (`/api/missions/{id}/items`)
- Gmail OAuth integration for email operator (`send_mode` and `inbound_mode: gmail_api`)
- Schema-driven connector manifests with `connect_flow` and `setup_fields` support
- Google Accounts manifest v1.1.0 with OAuth popup and postMessage completion
- `do_PATCH` HTTP handler in ServiceRuntime
- `_http_patch` helper in PRISM for downstream PATCH proxying

### Changed
- Email operator supports Gmail API mode alongside existing SMTP/IMAP
- `_classify_email` prompt extended with revenue-specific types
  (`lead`, `quote_request`, `purchase_order`, `invoice`, `overdue_invoice`, `unsold_quote`)
- Mission Manager extended with items endpoints (GET, POST, PATCH)
- PRISM route table extended with mission items proxy routes
- Summary strip in Zyrcon iPhone Revenue Desk shows live item count

### Fixed
- Migration idempotency with 16-table baseline (added `mission_items`)
- Test count updated: 1107 passing

---


## [0.46.0] — 2026-04-25

### Summary
14-task Sprint v2. Three competitive moats shipped: Approval Gates (Stripe billing,
timeout daemon, edit-and-approve, analytics, audit log), Hardware + Software Bundle
(zero-touch deployment scripts, SystemMonitor, fleet registry, DEPOT marketplace),
Open Core (LinkedIn connector, post scheduler, operator install, lead recovery, context
builder). 279 tests pass.

### New Modules
- `cascadia/billing/stripe_handler.py` — HMAC-SHA256 webhook verification, replay
  protection, checkout/subscription event processing
- `cascadia/billing/license_generator.py` — key generation, VAULT storage, email delivery
- `cascadia/system/approval_timeout.py` — daemon thread polling for stale approvals;
  escalates then auto-rejects at 2× threshold; `actor='system:timeout'`
- `cascadia/system/audit_log.py` — SHA-256 hash chain; `record()`, `verify_chain()`,
  `query()`, `export_csv()`
- `cascadia/hardware/system_monitor.py` — psutil-backed CPU/RAM/disk snapshot
- `cascadia/fleet/fleet_registry.py` — JSON-persisted node dict, 60s health polling
- `cascadia/marketplace/depot_client.py` — DEPOT API client with fallback catalogue
- `cascadia/memory/context_builder.py` — VAULT-backed per-company context, last-10 outcomes
- `operators/social/connectors/linkedin_connector.py` — LinkedIn UGC API, content scoring
- `operators/social/pipeline/post_scheduler.py` — SQLite queue, 30s dispatch loop
- `cascadia/operators/lead_recovery.py` — inbound email lead scorer; intent/urgency signals
- `hardware/zero_touch_deploy.sh` — one-command server bootstrap with systemd integration
- `hardware/server_health_check.sh` — pre-flight health check with JSON output mode

### Changed — `cascadia/durability/run_store.py`
- Added `approval_analytics(days=30)` — total/approved/rejected/edited/timed_out,
  avg decision time, by-risk breakdown

### Changed — `cascadia/durability/migration.py`
- Added `risk_level`, `edited_content`, `edit_summary` columns to `approvals` table
  (safe idempotent ALTER TABLE with try/except guard)

### Changed — `cascadia/system/approval_store.py`
- Added `edit_and_approve()` — stores edited content, marks approved, wakes run

### Changed — `cascadia/chat/bell.py`
- Added `POST /approve/edit` route → `edit_and_approve()`

### Changed — `cascadia/registry/crew.py`
- Added `POST /install_operator` → validates zip manifest, extracts to operators/,
  registers in Crew; `_extract_and_validate_manifest()` helper

### Changed — `cascadia/dashboard/prism.py`
- Added 14 new routes: stripe webhook, approve/edit, approval analytics, audit log
  (list/export/verify), fleet (status/register/remove), DEPOT (catalogue/detail),
  social scheduled posts, system monitor live metrics
- `overview()` now includes `hardware` key from `SystemMonitor.snapshot()`

### Changed — `cascadia/dashboard/prism.html`
- Live approval cards now show risk badge + Edit & Approve modal
- Approval Intelligence analytics section (async-loaded on Approvals surface)
- Hardware health mini-card in overview stat grid (CPU%, RAM used/total)
- `openEditApprove()`, `submitEditApprove()`, `loadApprovalAnalytics()` functions

### Changed — `enterprise/dashboard/enterprise_prism.py`
- `demo_overview()` includes demo `hardware` object

### Changed — `enterprise/dashboard/templates/enterprise_dashboard.html`
- Approval cards now have Edit & Approve button with modal
- `openDemoEditApprove()`, `submitDemoEdit()` functions

### Tests
- `tests/test_edit_approval.py` — 7 tests (edit_and_approve, migration columns)
- `tests/test_approval_analytics.py` — 9 tests (empty, approved, denied, timed_out,
  edited, avg time, by_risk, days field, mixed)
- `tests/test_operator_install.py` — 10 tests (valid install, bad zip, missing manifest,
  field validation, registry registration)
- `tests/test_lead_recovery.py` — 12 tests (scoring, signals, batch, filter)


## [0.45.0] — 2026-04-25

### Summary
12-task development sprint. Added response-time tracking, daily/weekly scheduler,
mDNS network discovery with iOS pairing codes, CRM operator manifest, win/loss outcome
tracking, WebSocket push from BELL, HMAC-SHA256 tier licensing, missed lead recovery
via CSV upload, and weekly HTML summary reports. PRISM dashboard updated with avg
response time stat, outcome badge, WebSocket auto-reconnect, and lead recovery UI.
189 tests pass.

### New Modules
- `cascadia/automation/scheduler.py` — `Scheduler` + `ScheduledJob`; supports `HH:MM`,
  `FRI HH:MM`, `MON-FRI HH:MM` schedules; daemon thread, 30s poll, date-keyed fire guard
- `cascadia/network/discovery.py` — optional mDNS via zeroconf (`_cascadia._tcp.local.`),
  `PairingManager` with 6-digit codes, 5-min TTL, single-use, `pending_count()`
- `cascadia/licensing/tier_validator.py` — HMAC-SHA256 key format
  `zyrcon_<tier>_<customer>_<expiry>_<hmac>`; `validate()` / `generate()`
- `cascadia/reports/weekly_summary.py` — HTML report built from SQLite runs table;
  delivers via HANDSHAKE `/call` or writes to `data/reports/weekly/`
- `cascadia/operators/crm_operator.json` — CRM operator manifest
- `scripts/generate_license.py` — CLI for generating signed license keys
- `docs/crm_integration.md` — non-technical CRM integration guide

### Changed — `cascadia/durability/migration.py`
- Added `lead_received_at TEXT`, `outcome TEXT`, `outcome_recorded_at TEXT` columns
  to the ALTER TABLE migration dict

### Changed — `cascadia/durability/run_store.py`
- `avg_response_time_minutes(limit)` — queries completed runs with `lead_received_at`
- `record_outcome(run_id, outcome)` — validates to `won|lost|no_decision`

### Changed — `cascadia/automation/stitch.py`
- `Scheduler` wired in `__init__`; morning brief (07:00) and weekly summary (FRI 17:00)
  registered on start
- Added `GET /scheduler/jobs`, `POST /scheduler/enable` routes

### Changed — `cascadia/shared/service_runtime.py`
- WebSocket upgrade handling (RFC 6455): `register_ws_route()`, `broadcast_event()`,
  `_ws_clients` registry; correct mask-key-before-payload frame parsing

### Changed — `cascadia/chat/bell.py`
- Registered `/bell/ws` WebSocket route

### Changed — `cascadia/dashboard/prism.py`
- 6 new routes: `/api/prism/scheduler`, `/api/prism/runs/outcome`,
  `/api/prism/pairing/code`, `/api/prism/pairing/validate`,
  `/api/prism/pairing/status`, `/api/prism/leads/recover`
- `avg_response_time_minutes` included in overview response
- mDNS started on `start()`

### Changed — `cascadia/dashboard/prism.html`
- `/status` overview card grid changed from 3→4 columns; 4th card shows avg response time
- `renderRunRow` expanded detail includes outcome badge / selector; `recordOutcome()`
  calls `POST /api/prism/runs/outcome`
- `connectWS()` — WebSocket client connecting to BELL `/bell/ws`; 3s auto-reconnect;
  triggers `pollLiveRuns()` on approval/run events
- Admin surface: missed lead recovery section with CSV textarea, first-boot banner,
  `scoreMissedLeads()` calling `POST /api/prism/leads/recover`

### Changed — `config.example.json`
- Added `license_secret`, `license_key`, `weekly_summary_email`, `reports_dir`,
  `scheduler` (morning_brief_time, weekly_summary_time)

### Tests
- `tests/test_scheduler.py` — 8 tests
- `tests/test_pairing.py` — 7 tests
- `tests/test_licensing.py` — 11 tests (HMAC validate/generate, expiry, tamper)
- `tests/test_weekly_summary.py` — 7 tests (build HTML, deliver file, subdir creation)

---


## [0.44.0] — 2026-04-23

### Changed
- Relicenced core from MIT to Apache License 2.0. The Apache 2.0
  licence applies from this version forward. Code in repository
  history prior to this tag remains available under the original
  MIT terms.
- First-party commercial operators moved to a private repository
  prior to this release. The public repo now contains the open
  core only.
- Added LICENSING.md documenting the Apache 2.0 core.
- Added COMMERCIAL.md describing the commercial product structure.
- Added license and license-files fields to pyproject.toml (PEP 639).
- Bumped version to 0.44.0.
- Added licensing-of-contributions clause to CONTRIBUTING.md.

## v0.43.0 — 2026-04-18

### Summary
Major execution layer completion. BEACON now forwards HTTP requests to operator
ports. VANGUARD dispatch wired to HANDSHAKE for real outbound delivery.
SENTINEL integrated into the WorkflowRuntime execution loop — risky actions are
checked before execution. Four Qwen model variants added with dynamic model
selection in PRISM. LLM configuration defaults set in config.example.json.
All version strings normalised to 0.43. 165/165 tests pass.

### Changed — `cascadia/orchestrator/beacon.py`
- `route()` now forwards the message payload via HTTP to the target operator's
  registered port after capability check. Previously returned `{ok:true}` and
  stopped there — routing was symbolic, not real.
- `handoff()` now forwards task payload to the target operator's `/task`
  endpoint via HTTP.
- New `forward()` route — `POST /forward` — direct HTTP proxy to any named
  component port. Skips capability check (caller is responsible). Used for
  internal component-to-component calls.
- New `GET /registry` route — returns the full port map BEACON knows about.
- `_forward_http()` helper added — handles GET/POST/PUT to local ports with
  graceful fallback on connection errors (returns 503 with error detail rather
  than raising).
- Port map built from config at init time — all registered components
  automatically available for forwarding.
- MATURITY tag updated from FUNCTIONAL to PRODUCTION.

### Changed — `cascadia/gateway/vanguard.py`
- `dispatch_outbound()` now calls HANDSHAKE `/webhook` for webhook channel
  messages and HANDSHAKE `/call` for email channel messages. Previously queued
  to an in-memory `_outbox` list and never sent.
- HANDSHAKE port looked up from config at init time with graceful fallback when
  HANDSHAKE is unreachable (degrades to queued, does not fail).
- Return code is `200` on real send, `202` on queue.
- MATURITY tag updated from STUB to PRODUCTION.

### Changed — `cascadia/automation/workflow_runtime.py`
- `_check_sentinel()` method added — calls SENTINEL `/check` via HTTP before
  executing any side-effect step (email.send, crm.write). If SENTINEL returns
  `blocked`, the step fails immediately with the policy reason. Falls back
  gracefully if SENTINEL is unreachable (fail open to avoid blocking runs).
- Non-side-effect steps (parse_lead, enrich_company, draft_email) skip the
  SENTINEL check — they have no external effects.
- `sentinel_port` optional parameter added to `__init__`. Auto-discovered from
  config when not provided.
- `json`, `urllib.request`, `urllib.error` imports added.

### Changed — `cascadia/dashboard/prism.py`
- New `GET /api/prism/models` route — reads `models` array from `config.json`
  and returns it with `active_model_id`, `llm_base_url`, and `llm_provider`.
  Falls back to a single entry from `llm.model` if no models array exists.

### Changed — `cascadia/dashboard/prism.html`
- `MODELS` constant replaced with dynamic `let MODELS` initialised with four
  Qwen variants as local defaults.
- `loadModelsFromPRISM()` async function added — called on startup, fetches
  `/api/prism/models` and replaces the MODELS array with config-sourced data.
  Adding a new model to `config.json` now reflects in PRISM automatically.
- All hardcoded `zyrcon-ai` model references replaced with `S.model` (dynamic).
- Active model from config set as selected model on load.

### Changed — `config.example.json`
- New `models` array with four Qwen variants:
  - `qwen2.5-3b` — Qwen2.5-3B-Instruct-Q4_K_M, 3B, fast, 4096 context
  - `qwen2.5-7b` — Qwen2.5-7B-Instruct-Q4_K_M, 7B, balanced, 8192 context
  - `qwen2.5-14b` — Qwen2.5-14B-Instruct-Q4_K_M, 14B, powerful, 8192 context
  - `qwen2.5-vl-7b` — Qwen2.5-VL-7B-Instruct-Q4_K_M, 7B, vision, 8192 context
- `llm.model` updated to `qwen2.5-3b-instruct-q4_k_m.gguf`
- `llm.active_model_id` field added
- `llm.note` field added with Ollama configuration guidance

### Changed — `pyproject.toml`
- Version bumped to `0.43.0`

### Changed — `tests/test_flint_runtime.py`
- Hardcoded version assertions updated from `0.2` to `0.40`
  (pre-existing test that had not tracked version changes)

---

## v0.42.0 — 2026-04-18

### Summary
HANDSHAKE upgraded from a call-logging stub to a real HTTP execution bridge.
Webhook and HTTP connections make actual network requests. Email connections
use smtplib with STARTTLS. A new convenience route fires webhooks without
pre-registering a connection. All existing tests pass.

### Changed — `cascadia/bridge/handshake.py`
- `proxy_call()` now routes to real executors based on `service_type`:
  - `webhook` and `http` — real HTTP execution via `urllib.request`
  - `email` — real SMTP send via `smtplib` with STARTTLS
  - All other types — logged and queued with a clear roadmap note
- `check_connection()` now performs a real HTTP GET to `base_url` and marks
  the connection `healthy`, `degraded`, or `unreachable` based on response.
  Previously always returned `healthy`.
- New `_execute_http()` private method — handles GET/POST/PUT/PATCH/DELETE,
  sends JSON body for POST/PUT/PATCH, returns real HTTP status and response
  body. Graceful error handling for HTTP errors and network failures.
- New `_execute_email()` private method — SMTP with STARTTLS,
  `MIMEMultipart` with plain + HTML body parts, configurable from/to/cc.
- New `POST /webhook` convenience route — register and fire a one-off webhook
  in a single call without pre-registering a connection. Accepts `url`,
  `body`, `headers`, `method`, `operator_id`.
- New `GET /capabilities` route — documents which channels are live vs roadmap.
- `ServiceConnection` extended with `headers`, `smtp_host`, `smtp_port`,
  `smtp_user`, `smtp_password`, `smtp_from` fields.
- `register_connection()` accepts all new SMTP and header fields.
- Call log now records real `status` (`completed` / `failed` / `queued`)
  and response details including `http_status` and `response_body`.
- `_LIVE_TYPES` constant added: `{'webhook', 'http', 'email'}`.
- `service_types()` now returns split `live_execution` and `roadmap` lists.
- MATURITY tag updated from STUB to PRODUCTION.

### Tests
- 7 new integration tests using a local HTTP test server:
  real POST execution, fire_webhook convenience route, proxy_call routing,
  call log population, graceful failure on bad URL, email without SMTP config,
  service_types split verification.

---

## v0.41.0 — 2026-04-18

### Summary
Major repository cleanup, PRISM dashboard redesign, documentation overhaul.
Dead files removed. PRISM nav rail updated to purple with PRISM wordmark.
Operator terminology corrected throughout. QUICKSTART.md added. README
fully rewritten. pyproject version corrected. install.sh header updated.

### Changed — `cascadia/dashboard/prism.html`
- Nav rail background changed to `#a78bfa` (purple) matching Zyrcon design system.
- Nav rail icons changed to white with white active/hover states.
- `C` logo replaced with PRISM wordmark — "PRISM" in small white caps with a
  four-colour gradient bar (blue → purple → green → pink).
- Sidebar panel (header, search, Beacon, operators list) remains white/light —
  only the narrow icon rail is purple.
- Operator avatar circles (S, V, H, B, A, P) changed to dark gray `#4a5568`
  with white text — previously light gray with blue text.
- `--navy-800` CSS variable changed from `#0a0a0a` to `#374151` — softer
  dark for operator names and body text throughout.
- Empty state icons and text darkened for better visibility.
- `OPERATORS` section label corrected — was `Cells`.
- Search placeholder corrected — was "Search cells…", now "Search operators…".
- Operator tags corrected — were labelled `Cell`, now labelled `Operator`.
- Empty state message corrected — was "Select a cell to begin".
- Version string updated to `v0.34` throughout.

### Changed — `README.md`
- Full rewrite. Sections: one-command install, demo script, real operator
  outputs table, architecture with ports, reliability guarantees, PRISM API,
  operator roster, design rules, docs links.
- Windows install section removed (install.bat deleted).
- `demo.sh` featured prominently in the opening section.
- `samples/` folder linked with direct file links.
- Architecture table updated to reflect v0.43 capabilities.

### Added — `QUICKSTART.md`
- New one-page quickstart: install → verify → PRISM → demo → tests → config →
  where things live → troubleshooting.
- Covers common errors: SwiftBar all red, port conflicts, cryptography module
  missing, BELL startup timing.

### Changed — `pyproject.toml`
- Version corrected to `0.40.0` (was `0.33.0` — had not tracked changes).

### Changed — `install.sh`
- Header updated to `Cascadia OS v0.40 Installer`.
- `start.sh` hardcoded personal paths to QUOTE and CHIEF operators removed.
  Replaced with a clearly labelled optional section.

### Changed — `.gitignore`
- `venv/` added.
- `.vscode/` and `.idea/` added.
- `dist/` and `build/` added.

### Deleted
- `install.bat` — Windows installer with YOUR_USERNAME placeholder. Never
  worked. Removed.
- `swiftbar-plugins/` — duplicate copy of `cascadia.5s.sh`. Superseded by
  the symlink approach.
- `tools/swiftbar/` — old 1-minute polling version of the menu bar script.
  Superseded by `cascadia.5s.sh`.
- `cascadia/installer/__pycache__/` — compiled bytecode that had been
  committed to git.
- `config.json` — contained a signing secret. Should never be committed.
  Only `config.example.json` belongs in the repo.

---

## v0.40.0 — 2026-04-18

### Summary
`install.sh` now fully automates the client install experience. Homebrew and
SwiftBar are installed automatically if missing. SwiftBar is added to macOS
Login Items programmatically. No manual steps required after running the
installer.

### Changed — `install.sh`
- New section 0: Mac prerequisites.
  - Checks for Homebrew. Installs via the official install script if missing.
  - Checks for SwiftBar at `/Applications/SwiftBar.app` and
    `~/Applications/SwiftBar.app`. Installs via `brew install --cask swiftbar`
    if missing.
  - Launches SwiftBar to initialise its plugin folder on first run.
- SwiftBar Login Items registration updated to use working AppleScript syntax:
  `make new login item at end of login items with properties {...}` with
  `exists login item` guard to prevent duplicates. Previous syntax
  (`make login item at end with properties`) did not work on macOS Sequoia.
- SwiftBar path detection uses both hardcoded paths and `mdfind` fallback.

---

## v0.39.0 — 2026-04-18

### Summary
Cascadia OS now starts automatically at macOS boot. SwiftBar is added to Login
Items automatically during install. `flint-link.sh` added for one-command
plugin wiring.

### Changed — `install.sh`
- New section 12: auto-start on login.
  - Writes `~/Library/LaunchAgents/com.zyrconlabs.cascadia.plist` with correct
    `WorkingDirectory`, `RunAtLoad: true`, `KeepAlive: true`, and log paths.
  - Calls `launchctl load` immediately so Cascadia starts without a reboot.
  - Finds SwiftBar via `mdfind` and adds it to Login Items via AppleScript.
- SwiftBar plugin installation changed from `cp` to `ln -sf` — one source of
  truth, changes in repo reflect immediately in SwiftBar without re-copying.
  Same change applied to xbar and Argos targets.

### Added — `flint-link.sh`
- Standalone script to wire the Flint menu bar plugin to SwiftBar/xbar/Argos.
- Run once after cloning. Uses `ln -sf` — no re-linking needed after `git pull`.
- Removes any old copy before creating the symlink.
- Clear output: confirms link path, prints manual instructions if
  SwiftBar/xbar/Argos not found, suggests `python -m cascadia.flint.tray` as
  fallback.

---

## v0.38.0 — 2026-04-18

### Summary
Flint menu bar plugin now uses a symlink — single source of truth, no copying
required. install.sh updated accordingly.

### Changed — `install.sh`
- SwiftBar/xbar/Argos plugin installation changed from `cp` to `ln -sf`.
  The repo file (`cascadia/flint/cascadia.5s.sh`) is the single source of
  truth. Changes to the script reflect in SwiftBar immediately after `git pull`
  with no re-copy step.
- Install instructions in the "not found" path updated to show `ln -sf`
  instead of `cp`.

---

## v0.37.0 — 2026-04-18

### Summary
VAULT now validates capabilities against CREW via HTTP instead of trusting
payload-declared capabilities. Investor demo script added. 136/136 tests pass.

### Changed — `cascadia/memory/vault.py`
- `_check_cap()` now calls CREW `/validate` endpoint via HTTP before granting
  vault access. If CREW returns `ok: false`, the request is denied with 403.
- Graceful fallback: if CREW is unreachable (startup, testing, network error),
  falls back to payload-declared capabilities so the system degrades gracefully
  rather than failing completely.
- `_crew_port` looked up from config at init time.
- `json` and `urllib.request` imports added.
- Previous behaviour (trust any capability claimed in the request payload) was
  a security gap — any operator could claim any capability.

### Added — `demo.sh`
- 240-line investor demo script. Six steps, approximately 90 seconds end-to-end.
- Step 1: verify Cascadia is running, auto-start if not.
- Step 2: submit a real lead via BELL — workflow starts, lead classified,
  enriched, draft email generated, approval gate fires.
- Step 3: show PRISM reporting the pending approval.
- Step 4: deliberately kill the Cascadia process mid-run.
- Step 5: restart Cascadia — run resumes from exact same step, nothing
  re-executed, no duplication.
- Step 6: approve via PRISM API — email sent, CRM logged, run complete.
- Usage: `bash demo.sh` (manual pacing) or `bash demo.sh --auto`
  (fully automated, no Enter prompts).

---

## v0.36.0 — 2026-04-18

### Summary
BELL is now fully wired to WorkflowRuntime. Messages trigger real workflow
execution. Approvals resume the workflow. PRISM has a working approve endpoint.
STITCH gains execute_run and resume_run methods. 136/136 tests pass.

### Changed — `cascadia/chat/bell.py`
- `receive_message()` now calls `WorkflowRuntime.execute()` on every message
  instead of returning `"queued_for_operator: True"`. The run ID, state, and
  pending approval ID are returned immediately.
- `receive_approval()` now calls `ApprovalStore.record_decision()` which wakes
  the run, then re-calls `WorkflowRuntime.execute(run_id)` to resume from the
  exact step after the approval gate. The resume result is included in the
  response.
- When `run_id` is not provided in the approval payload, BELL falls back to the
  most recent linked run from the session's `linked_run_ids` list.
- `_StitchShim` inner class added — provides workflow definitions to BELL
  without requiring the STITCH service to be running. Mirrors the built-in
  workflows from `StitchService._register_builtins()`.
- `WorkflowRuntime` instantiated at `__init__` time using `database_path` from
  config.
- Session messages now include the assistant response from workflow results
  (draft preview or approval-required message).
- MATURITY tag updated from FUNCTIONAL to PRODUCTION.

### Changed — `cascadia/automation/stitch.py`
- `execute_run()` method added — delegates to `WorkflowRuntime.execute()`.
  Looks up workflow definition from registered workflows.
- `resume_run()` method added — looks up run from RunStore to determine
  workflow ID, then delegates to `WorkflowRuntime.execute(run_id=...)`.
- `POST /run/execute` and `POST /run/resume` HTTP routes registered.

### Changed — `cascadia/dashboard/prism.py`
- `POST /api/prism/approve` route added — calls `ApprovalStore.record_decision()`
  and re-executes `WorkflowRuntime` with the approved run ID. The Approve and
  Reject buttons in the PRISM live approvals surface now work end-to-end.
- `approve_action()` method added with full implementation including run lookup
  from the approvals table when `run_id` is not provided in the payload.

### Tests
- `TestBellToStitchWorkflow.test_bell_can_start_and_approve_workflow` — was
  ERROR (run_state KeyError), now passes. End-to-end: session start → message
  → waiting_human → approval → complete → history check.
- All 136 tests pass.

---

## v0.35.0 — 2026-04-18

### Summary
Version string cleanup across all 32 affected files. All modules now
consistently report `v0.34`. Stub language neutralised. MATURITY tags updated.

### Changed — 32 files
- All `__init__.py` files: `Cascadia OS v0.2` → `Cascadia OS v0.34`
- All module docstrings: first-line version updated
- FLINT and Watchdog startup log messages: now say `v0.34`
- PRISM API response: `"cascadia_os": "v0.34"` (was `"v0.2"`)
- HANDSHAKE inline comments: "Real execution in v0.3" language removed,
  replaced with neutral phrasing
- VAULT capability comment: stub language removed
- BELL approval comment: stub language removed
- All 5 test print banners: now say `Cascadia OS v0.34`
- `MATURITY: STUB` → `MATURITY: FUNCTIONAL` across affected modules
- `MANUAL.md` header updated to v0.34
- `MANUAL.md` roadmap version targets updated from v0.32 to v0.35
- `PRISM_MANUAL.md` example JSON updated to v0.34
- `install.sh` version string updated
- `cascadia/installer/once.py` version docstring cleaned

---

## v0.34.0 — 2026-04-18

### Summary
PRISM live poll loop. Runs and Approvals surfaces now pull real data from the
Cascadia OS API every 3 seconds. Approve/Reject buttons in PRISM are wired to
the backend. Version strings in PRISM updated to v0.34.

### Changed — `cascadia/dashboard/prism.html`
- `pollLiveRuns()` async function added — polls `/api/prism/runs` and
  `/api/prism/approvals` every 3 seconds via the existing `prismFetch()` helper.
  Only re-renders if data changed (JSON diff check — no unnecessary repaints).
- `startLivePoll()` function added — starts the poll loop on init.
- Runs surface — live runs section injected at top of Run Timeline. Shows real
  `run_state` badges (running, waiting_human, complete, failed, blocked). Live
  indicator dot in topbar.
- Approvals surface — live approvals from PRISM API injected above session
  approvals. Each card shows run_id, action_key, age, and Approve/Reject buttons.
- `approveFromPrism(id, decision)` — POSTs to `/api/prism/approve`, removes
  card optimistically, re-polls after 800ms to pick up resumed run state.
- `renderLiveRunRow()` — renders a single live run row with goal, run_id,
  current step, and timestamp.
- `renderLiveApprovalCard()` — renders a live approval card with inline
  approve/reject actions.
- `runStateBadge(state)` — generates coloured badge for any run_state value.
- CSS additions: `.live-dot` (animated green pulse), `.run-live-row`,
  `.rls-badge` + state variants, `.prism-live-header`, `.approval-live-card`.
- Version strings updated: `v0.21` → `v0.34` in title, sidebar, system
  overview messages, health report, and help dialog.
- `startLivePoll()` called in init block alongside `refreshCells()`.


---

## v0.34-ops — 2026-04-18 (operator ecosystem release)

> Note: This entry documents the earlier v0.34 operator release which added
> RECON, SCOUT, QUOTE, CHIEF, and the full operator stack. The PRISM live poll
> loop changes (same date) are documented in v0.34.0 above.


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


---


## v0.33 — 2026-04-18

### Summary
CURTAIN field encryption upgraded from XOR placeholder to AES-256-GCM.
Public interface unchanged — no callers require modification.
All existing tests pass. 11 additional security tests added.

### Changed — `cascadia/encryption/curtain.py`
- `encrypt_field()` — replaced XOR+SHA256 keystream (v0.2 placeholder, 32-byte limit,
  no authentication) with AES-256-GCM (authenticated encryption, arbitrary length,
  tamper-evident, 96-bit random nonce per call)
- `decrypt_field()` — now raises `ValueError` on authentication failure (tampered
  ciphertext or tag) rather than silently returning garbage
- `MATURITY` tag updated from `STUB` to `PRODUCTION`
- Docstring updated — removed "v0.3 placeholder" references
- Added `derive_field_key(signing_secret)` — derives a 32-byte AES key from the
  master signing secret using PBKDF2-HMAC-SHA256 with a fixed label salt
- Added `GET /capabilities` route — reports signing and encryption algorithms in use
- Added `POST /encrypt` and `POST /decrypt` HTTP routes on CurtainService
- `CurtainService.__init__` now derives `_field_key` from signing_secret automatically

### Added — `pyproject.toml`
- `cryptography>=42.0.0` declared as a project dependency
- `[project.optional-dependencies]` section added:
  - `operators` — flask, flask-cors, requests, ddgs
  - `tray` — pystray, pillow
- Version bumped to `0.33.0`

### Security properties of AES-256-GCM vs previous XOR implementation
| Property | XOR (v0.2) | AES-256-GCM (v0.33) |
|---|---|---|
| Authentication | None | 128-bit GCM tag |
| Tamper detection | No | Yes — raises ValueError |
| Max plaintext length | 32 bytes | Unlimited |
| Nonce reuse risk | Per-call random | Per-call random (12 bytes) |
| Diligence safe | No | Yes |

### Unchanged
All other modules unchanged from v0.32. HMAC-SHA256 envelope signing was already
correct in v0.2 and is not modified.

---

## v0.31 — 2026-04-18

### Summary
First release with working operators. SCOUT and RECON ported from Zyrcon AI v0.2, updated to the Cascadia port scheme and directory structure. All operator source files verified and port references corrected.

### Added — SCOUT operator (`cascadia/operators/scout/`)
- `scout_server.py` — Flask server, SSE streaming chat, session management, lead save/load, `/bell` and `/doorbell` UI routes, `/api/leads`, `/api/stats`, `/api/health`
- `scout_worker.py` — AI brain: system prompt builder from persona folders, lead extraction with AI + regex double-pass fallback, deal value estimator by project type and square footage, Groq cloud fallback
- `scouts/lead-engine/job_description/role.md` — Scout persona: who it is, what it knows, conversation flow
- `scouts/lead-engine/company_policy/policy.md` — Rules, hot/warm/cold signals, escalation language, hard limits
- `scouts/lead-engine/current_task/task.md` — Current focus: Houston industrial lead capture
- `web/bell.html` — Streaming chat widget for website embedding
- `web/doorbell.html` — Standalone iframe-embeddable lead capture page
- `manifest.json` — FLINT-compatible operator manifest, port 7000
- `scout.config.json` — Config with corrected `bridge_url: http://127.0.0.1:4011`
- `requirements.txt` — flask, flask-cors, requests

### Added — RECON operator (`cascadia/operators/recon/`)
- `recon_worker.py` — Research agent: task.md-driven queries, DuckDuckGo search, CSV output, deduplication, thoughts ring buffer (40 entries), graceful SIGTERM shutdown
- `dashboard.py` — SSE live dashboard server
- `dashboard.html` — Real-time research progress UI
- `tasks/current/task.md` — Current research task configuration
- `policy/guardrails.md` — Research guardrails and ethical constraints
- `policy/source-standards.md` — Source quality and reliability standards
- `job/job-description.md` — Recon agent role definition
- `manifest.json` — FLINT-compatible operator manifest, port 7001
- `recon.config.json` — Config with corrected port references
- `requirements.txt` — flask, requests, ddgs

### Changed
- Version bumped to `0.31` across `once.py`, `setup.html`, `pyproject.toml`
- `README.md` — SCOUT and RECON sections added, operator endpoints documented, port table updated with 7000/7001
- `MANUAL.md` — Full operator runbooks added: start commands, endpoints, persona system, deal value table, troubleshooting entries for both operators

### Port corrections in ported files
- `scout_worker.py` — `bridge_url` default updated from `localhost:18790` (old bridge) to `localhost:4011` (FLINT)
- `recon_worker.py` — LLM endpoint updated from `127.0.0.1:8080` to `127.0.0.1:4011`, vault path updated from `~/.zyrcon/recon-worker` to `./data/vault/operators/recon`
- `scout.config.json` — `server_port` 8000 → 7000, `bridge_url` → `http://127.0.0.1:4011`, `vault_dir` → relative path
- `recon.config.json` — `worker_port` 8002 → 7001, `cascadia_port` 7000 → 4011, paths made relative

### Known issues (queued for v0.32)
- Two simultaneous Recon worker processes can cause state conflicts — run one instance only
- Inline YAML comments in `task.md` frontmatter break the parser — keep frontmatter values clean
- `state.json` model name must match the actual running model exactly

### Unchanged
All 27 kernel/durability/component Python files are identical to v0.30. No changes to FLINT, Watchdog, durability layer, policy/gating, or named components.

---

## v0.30 — 2026-04-17

### Summary
Full merge of v0.21 (GitHub) and v0.29 (Mac local). Port rebanding to clean banded scheme. PRISM UI and setup wizard restored.

### Added
- Browser setup wizard (`cascadia/installer/setup.html`) — 4-step browser UI at `:4010`
- System detection — `_detect_ram_gb()`, `_detect_ollama()` in ONCE
- AI setup flow — `setup_ai()`, `_apply_llm_config()`, `--no-browser` flag
- `_send_html()` in `service_runtime.py` — enables HTML responses from any service module
- PRISM live UI — `serve_ui()` at `GET /`, dashboard at `localhost:6300/`
- `cascadia/dashboard/prism.html` — 60KB single-file dashboard
- `CHANGELOG.md` — this file

### Changed
- All ports rebanded: `18780–18810` → `4010, 4011, 5100–5103, 6200–6205, 6300`
- `README.md`, `MANUAL.md` — full rewrites
- `pyproject.toml` — version `0.21.0` → `0.30.0`

---

## v0.21 — 2026-04-17 (GitHub release)

- Browser setup wizard, system detection, AI setup flow added to ONCE
- `_send_html()` in `service_runtime.py`
- PRISM `serve_ui()` route — dashboard at `localhost:18810/`

---

## v0.29 — 2026-04-14 (Mac local build)

- Stripped installer — setup wizard removed
- `prism.html` removed (backend-only)
- All kernel, durability, and policy modules identical to v0.21

---

## v0.2 — 2026-04-11

- FLINT process supervisor with tiered startup, health polling, restart/backoff
- Watchdog external liveness monitor
- Full durability layer: run_store, step_journal, resume_manager, idempotency, migration
- Policy and gating: runtime_policy, approval_store, dependency_manager
- Named components: CREW, VAULT, SENTINEL, CURTAIN, BEACON, STITCH, VANGUARD, HANDSHAKE, BELL, ALMANAC, PRISM
- 21/21 crash recovery tests passing