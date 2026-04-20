# Cascadia OS

> **The execution layer for AI operators that actually finish the work.**

---

I was five years old the first time I took apart a telephone. Not for school. Because I needed to understand how the sound got through the wire.

Decades later — aerospace engineering in Moscow, automation projects for Amazon and the US Navy, building at 2am while my daughter slept — I kept running into the same problem: AI that was impressive in demos and unreliable in production.

I didn't want a chatbot. I wanted an operator I could trust. Something that remembers, asks before acting, and picks up where it left off after a crash. Something designed for the moment when things go wrong at three in the morning and nobody is watching.

**That's what this is.** → [Full story](./STORY.md)

---

## ⚡ One-Command Install

```bash
curl -fsSL https://raw.githubusercontent.com/zyrconlabs/cascadia-os/main/install.sh | bash
```

Installs Homebrew (if needed), SwiftBar, Cascadia OS, registers a login agent, and links the menu bar controller. Everything starts automatically at boot. No manual steps.

> **Requires:** Python 3.11+ and git. Everything else is handled automatically.

→ [Full quickstart guide](./QUICKSTART.md)

---

## 🎬 Run the Demo

After installing, run the demo — ~90 seconds end-to-end:

```bash
bash demo.sh
```

**What you'll see:**
1. Lead arrives → workflow starts automatically
2. System classifies, enriches, drafts a response
3. Approval gate fires — email held until a human approves
4. **System crashes mid-run** (deliberate)
5. Restarts — resumes from exact same step, zero duplication
6. Approval given → email sent → CRM logged → complete

---

## See it working

**One-click install — done in under a minute:**
![Install](./assets/install.png)

**Watchdog running — all 11 kernel components healthy:**
![Watchdog](./assets/watchdog.png)

**PRISM dashboard — live system status:**
![PRISM Dashboard](./assets/prism.png)

**Crash recovery — 21/21 tests passed:**
![Crash Recovery](./assets/crash_recovery.png)

---

## Real operator outputs

These were generated on a MacBook Air M1, Qwen 3B via llama.cpp. No cloud API.

| Output | What it shows |
|---|---|
| [Houston warehouse leads](./samples/recon-houston-warehouse-leads-2026-04-18.csv) | RECON — 25+ search cycles, hallucination-filtered |
| [Gulf Coast Logistics proposal](./samples/proposal-Gulf-Coast-Logistics-2026-04-18.md) | Full proposal from one-paragraph RFQ in 30 seconds |
| [Morning brief](./samples/chief-brief-2026-04-18.md) | CHIEF — 90-second executive brief from live operator data |
| [Post-call debrief](./samples/debrief-gulf-coast-logistics-2026-04-18.md) | Action items and follow-up draft from raw call notes |

---

## What it does

Cascadia OS coordinates AI operators that:

- **Remember** — context, decisions, and state persist across sessions and crashes
- **Ask** — approval gates block risky actions until a human says yes
- **Never duplicate** — idempotency enforced at the database layer, not by hope
- **Recover** — resume from the last committed step, not from scratch
- **Run supervised** — FLINT watches every process; the watchdog watches FLINT

---

## Architecture

### Control plane
| Module | What it does |
|---|---|
| FLINT | Process supervision, tiered startup, health polling, restart/backoff |
| Watchdog | External FLINT liveness monitor — lives outside the supervision tree |

### Durability layer
| Module | What it does |
|---|---|
| VAULT | SQLite-backed memory, context and state across sessions |
| VAULT | SQLite-backed memory, context and state across sessions and crashes |
| CURTAIN | AES-256-GCM field encryption, HMAC-SHA256 envelope signing |

### Named components
| Name | Port | What it does |
|---|---|---|
| CREW | 5100 | Operator registry with wildcard capability validation |
| VAULT | 5101 | Durable SQLite-backed memory, CREW-validated access |
| SENTINEL | 5102 | Risk classification, blocks denied actions in execution loop |
| CURTAIN | 5103 | AES-256-GCM field encryption, HMAC-SHA256 signing |
| BEACON | 6200 | Capability-checked routing, HTTP forwarding to operator ports |
| STITCH | 6201 | Workflow sequencing with built-in templates |
| VANGUARD | 6202 | Inbound channel normalization, outbound dispatch via HANDSHAKE |
| HANDSHAKE | 6203 | Webhook/HTTP/SMTP execution, external API registry |
| BELL | 6204 | Chat sessions, workflow execution, approval collection |
| ALMANAC | 6205 | Component catalog, glossary, runbooks |
| PRISM | 6300 | Live system visibility — runs, approvals, operators |

---

## Reliability guarantees

Tested in `tests/test_crash_recovery.py`. Not just claimed.

| Scenario | Behavior |
|---|---|
| Kill operator mid-run | Resumes from last committed step, not step 0 |
| Crash after side effect declared but not committed | Re-attempts on resume |
| Crash after side effect committed | Skips — never duplicates |
| Approval-required run restarted | Stays `waiting_human`, never auto-resumes |
| Multiple crashes in sequence | `retry_count` increments correctly each time |

---

## PRISM Dashboard

Open `http://localhost:6300/` while Cascadia is running.

**Surfaces:** Live operator status · Run timeline · Approvals · Observability · Studio · Admin

**API:**
```bash
GET  :6300/api/prism/overview    # Full system snapshot
GET  :6300/api/prism/runs        # Live run states  
GET  :6300/api/prism/approvals   # Pending human decisions
POST :6300/api/prism/approve     # Approve or deny a gated action
GET  :6300/api/prism/crew        # Active operators
```

Full documentation: [PRISM_MANUAL.md](./PRISM_MANUAL.md)

---

## Operators

| Operator | Category | Status | What it does |
|---|---|---|---|
| RECON | Intelligence | Production | Autonomous web research, extracts contacts to CSV |
| SCOUT | Inbound | Production | Chat widget, qualifies leads, routes to workflow |
| QUOTE | Sales | Beta  | RFQ to proposal in under 5 minutes |
| QUOTE | Sales | Beta | RFQ to proposal in under 5 minutes |
| CHIEF | Intelligence | Beta | Daily brief synthesizing all operators |
| Aurelia | Executive | Beta | EA — commitments, priorities, weekly CEO report |
| Debrief | Sales | Beta | Post-call logger — action items, follow-up drafts |

Operator registry: [cascadia/operators/registry.json](./cascadia/operators/registry.json)

---

## Design rules

1. FLINT supervises. FLINT does not execute workflows.
2. No side effect executes twice. Idempotency is enforced at the DB layer.
3. Resume reads the journal. Resume does not guess.
4. Dangerous actions require policy clearance. Policy is separate from capability.
5. Blocking a run is explicit. Auto-resuming a blocked run is never allowed.
6. The module that owns execution does not own policy. The module that owns policy does not own storage.

---

## Docs

- [Quickstart](./QUICKSTART.md)
- [Manual](./MANUAL.md)
- [PRISM Manual](./PRISM_MANUAL.md)
- [Contributing](./CONTRIBUTING.md)
- [Security Policy](./SECURITY.md)
- [Story behind the project](./STORY.md)

---

*Built in Houston, Texas — [Zyrcon Labs](https://github.com/zyrconlabs)*
