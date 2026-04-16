# Cascadia OS v0.2

> A local-first, single-node operator platform. Built for one machine, one builder,
> and small businesses that need autonomous operators to work reliably without surprises.

---

## ⚡ One-Click Install

**Mac / Linux:**
```bash
curl -fsSL https://raw.githubusercontent.com/YOUR_USERNAME/cascadia-os/main/install.sh | bash
```

**Windows** — download and run [`install.bat`](https://raw.githubusercontent.com/YOUR_USERNAME/cascadia-os/main/install.bat), or in PowerShell:
```powershell
irm https://raw.githubusercontent.com/YOUR_USERNAME/cascadia-os/main/install.bat -OutFile install.bat; .\install.bat
```

The installer will:
1. Clone this repo to `~/cascadia-os`
2. Create an isolated Python virtual environment
3. Install the package
4. Copy `config.example.json` → `config.json` for you to edit
5. Run first-time setup (`cascadia.installer.once`)
6. Add a `cascadia` launcher command to your PATH

> **Requires:** Python 3.11+ and git

---

## Manual Start

```bash
# First-time setup (if not using the installer)
python -m cascadia.installer.once

# Start the OS (watchdog keeps FLINT alive)
python -m cascadia.kernel.watchdog --config config.json

# Run all tests
python -m unittest discover -s tests -v

# Run crash/recovery drills specifically
python tests/test_crash_recovery.py
```

---

## What is implemented and working

These modules are fully built, tested, and active in v0.2:

### Control plane
| Module | Path | What it does |
|---|---|---|
| FLINT | `kernel/flint.py` | Process supervision, tiered startup, health polling, restart/backoff, graceful shutdown |
| Watchdog | `kernel/watchdog.py` | External FLINT liveness monitor — lives outside the supervision tree |

### Durability layer (the most important part)
| Module | Path | What it does |
|---|---|---|
| run_store | `durability/run_store.py` | Durable run records with process_state + run_state split |
| step_journal | `durability/step_journal.py` | Append-only step log — source of truth for resume |
| resume_manager | `durability/resume_manager.py` | Safe resume-point calculation from committed steps and side effects |
| idempotency | `durability/idempotency.py` | SHA-256 keyed side effect records, UNIQUE DB constraint |
| migration | `durability/migration.py` | Idempotent schema migration, handles legacy DB upgrades |

### Policy and gating
| Module | Path | What it does |
|---|---|---|
| runtime_policy | `policy/runtime_policy.py` | allow / deny / approval_required per action |
| approval_store | `system/approval_store.py` | Persists approval requests and decisions, wakes blocked runs |
| dependency_manager | `system/dependency_manager.py` | Detects missing operators and permissions, writes blocked state |

### Named components
| Brand | Path | What it does |
|---|---|---|
| CREW | `registry/crew.py` | Operator group registry with wildcard capability validation |
| VAULT | `memory/vault.py` | Durable SQLite-backed institutional memory, capability-gated |
| SENTINEL | `security/sentinel.py` | Risk classification and compliance rule evaluation |
| CURTAIN | `encryption/curtain.py` | HMAC-SHA256 envelope signing and field encryption (stdlib only) |
| BEACON | `orchestrator/beacon.py` | Capability-checked routing, operator handoffs |
| STITCH | `automation/stitch.py` | Workflow sequencing with built-in templates |
| VANGUARD | `gateway/vanguard.py` | Inbound channel normalization, outbound dispatch |
| HANDSHAKE | `bridge/handshake.py` | External API connection registry |
| BELL | `chat/bell.py` | Chat sessions and approval response collection |
| ALMANAC | `guide/almanac.py` | Component catalog, glossary (26 terms), runbook (5 entries) |
| PRISM | `dashboard/prism.py` | Aggregated system visibility — runs, approvals, blocked, crew |
| ONCE | `installer/once.py` | First-time setup and validation |

---

## What is thin but present

These exist in the codebase and register correctly, but their internals are stubs or minimal:

- **SENTINEL** — risk classification and compliance rules are real; enforcement hooks into full operator execution are not yet wired end-to-end
- **CURTAIN** — HMAC signing and field encryption work; full asymmetric key exchange is v0.3
- **HANDSHAKE** — connection registry and call logging work; actual HTTP execution to external APIs is v0.3
- **VANGUARD** — normalization and queuing work; real channel adapters (email SMTP, SMS) are v0.3
- **PRISM** — aggregation queries work; real-time push/websocket is not present

---

## What is not built yet (roadmap)

- GRID — decentralized compute network
- DEPOT — operator app store and marketplace
- Scheduler / trigger manager
- Workflow planner
- Workspace manager / merge manager
- MicroVM operator isolation
- Multi-node HA

---

## Reliability guarantees (proven by crash tests)

| Scenario | Behavior |
|---|---|
| Kill operator mid-run | Resumes from last committed step, not step 0 |
| Crash after side effect declared but not committed | Re-attempts the effect on resume |
| Crash after side effect committed | Skips the effect — never duplicates |
| Approval-required run restarted | Stays `waiting_human`, never auto-resumes |
| Poisoned or complete run | Never resumed under any condition |
| State restoration | Restored from last committed step's `output_state` |
| Multiple crashes | `retry_count` increments correctly each time |
| Partial step (started, not completed) | Retried from scratch, not treated as done |
| FLINT startup scan | Finds all interrupted runs, skips complete/poisoned/waiting |
| Blocked run (missing dependency) | Not in resume scan, stays blocked until cleared |

---

## PRISM endpoints

```bash
GET  http://127.0.0.1:18810/api/prism/overview     # Full system snapshot
GET  http://127.0.0.1:18810/api/prism/system       # FLINT component states
GET  http://127.0.0.1:18810/api/prism/crew         # Active operators
GET  http://127.0.0.1:18810/api/prism/runs         # Recent run states
POST http://127.0.0.1:18810/api/prism/run          # {"run_id": "..."} — full run detail
GET  http://127.0.0.1:18810/api/prism/approvals    # Pending human decisions
GET  http://127.0.0.1:18810/api/prism/blocked      # Runs blocked on dependencies
GET  http://127.0.0.1:18810/api/prism/workflows    # Available STITCH workflows
```

## FLINT status

```bash
GET  http://127.0.0.1:18791/api/flint/status       # Component health and process states
GET  http://127.0.0.1:18791/health                 # FLINT liveness check
```

---

## Component ports

| Component | Port |
|---|---|
| FLINT status | 18791 |
| CREW | 18800 |
| VAULT | 18801 |
| SENTINEL | 18802 |
| CURTAIN | 18803 |
| BEACON | 18804 |
| STITCH | 18805 |
| VANGUARD | 18806 |
| HANDSHAKE | 18807 |
| BELL | 18808 |
| ALMANAC | 18809 |
| PRISM | 18810 |

---

## Design rules that will not change

1. FLINT supervises. FLINT does not execute workflows.
2. The kernel is the governor. Operators are the workers.
3. No side effect executes twice. Idempotency is enforced at the DB layer.
4. Every external action must be replay-safe.
5. Resume reads the journal. Resume does not guess.
6. Dangerous actions require policy clearance. Policy is separate from capability.
7. Blocking a run is explicit. Auto-resuming a blocked run is never allowed.
8. The module that owns execution does not own policy. The module that owns policy does not own storage.

---

*Zyrcon Labs — Cascadia OS v0.2*
