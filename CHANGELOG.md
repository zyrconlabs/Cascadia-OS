# Changelog

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