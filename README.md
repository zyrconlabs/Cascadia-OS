# Cascadia OS

> AI-powered workflow missions for small businesses — running on Mac, Linux, or Windows.

> Architecturally compliant with EU AI Act Articles 8-15 for high-risk AI systems — by design, not by retrofit.
> → [EU AI Act Compliance Reference](./docs/eu_ai_act_compliance.md)

**Version:** 2026.5 · May 2026 · [Changelog](CHANGELOG.md) · ![Tests](https://img.shields.io/badge/tests-1242%20passing-green)

---

I was five years old the first time I took apart a telephone. Not for school. Because I needed to understand how the sound got through the wire.

Before I built AI operators, I built machines that could not be allowed to fail.

Decades later — after aerospace engineering, industrial automation, large-scale warehouse and government infrastructure work, and building always-on systems under real heat, cost, and security constraints — I kept running into the same problem: AI that looked impressive in demos and became unreliable the moment it touched real work.

I didn't want a chatbot. I wanted an operator I could trust. Something that remembers, asks before acting, resumes after failure, and stays bounded when the stakes are real.

**That's what this is.** → [Full story](./STORY.md)

---

## What It Does

Zyrcon AI helps small businesses find customers, improve sales, organize work, and automate daily operations through AI-powered workflow missions — powered by Cascadia OS.

Three mission desks run automatically:

**Revenue Desk** — Scans email for leads, quotes, POs, invoices, and follow-ups. Classifies each one, scores urgency and value, and flags opportunities for your approval before taking action. Connects to the Zyrcon iPhone app so you can act from anywhere.

**Growth Desk** — Turns completed jobs, old leads, and seasonal opportunities into approved email, SMS, and social media campaigns. Generates drafts, routes for approval, and dispatches on your schedule.

**Operations Desk** — Reviews projects, tasks, assistant activity, planning gaps, and risks. Surfaces what needs attention without requiring you to ask.

---

**What runs automatically**

| Schedule | What happens |
|----------|-------------|
| On first start | Demo workflow seeded — approval card ready within 60 seconds |
| MON–FRI 09:00 | Growth Desk generates campaign drafts for your approval |
| Daily 02:00 | SQLite backup with integrity verification |
| Continuously | Health Monitor checks all components, escalates on failure |

---

## Architecture

```
Zyrcon iPhone App
     ↓ REST + WebSocket
PRISM (6300) — Dashboard and API gateway
     ↓
Mission Layer
  ├── Mission Manager (6207) — catalog, runs, and items API
  ├── Mission Runner — lifecycle management via STITCH
  └── Approval Center — BELL (6204)
     ↓
Operator Layer
  ├── CHIEF (orchestrator)
  ├── Revenue:    SCOUT · RECON · QUOTE · COLLECT
  ├── Campaigns:  SOCIAL · CAMPAIGN · BRIEF
  └── Assistant:  Aurelia
     ↓
Infrastructure
  ├── VAULT (5101)     — secure storage and cross-operator secrets
  ├── STITCH (6201)    — workflow sequencing
  ├── BEACON (6200)    — capability routing
  ├── VANGUARD (6202)  — inbound normalization
  └── HANDSHAKE (6203) — webhooks and SMTP
```

### Full component table

| Name | Port | What it does |
|---|---:|---|
| FLINT | 4011 | Process supervisor — tiered startup, health polling, restart with backoff |
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
| CONDUIT | 6206 | IoT device bridge and sensor event router |
| Mission Manager | 6207 | Mission catalog, runs, items, and approval lifecycle |
| VANTAGE | 6208 | Capability enforcement gateway — validates operator permissions before every connector call |
| Health Monitor | 6209 | 24/7 health daemon — detects failures and triggers escalation chain |
| PRISM | 6300 | Live system visibility — runs, approvals, operators, mobile API |

### Message fabric

| Component | Port | What it does |
|-----------|-----:|-------------|
| NATS JetStream | 4222 | Health events, failure signals, escalation routing between components |

On component failure: FailureEvent → NATS → supervisor → retry / escalate / dead-letter queue.

---

## Local AI inference

Cascadia OS runs inference locally via llama.cpp. On Apple Silicon (M1–M4) this uses the Metal GPU. No cloud API key required. No per-token cost. No data leaves your hardware.

| Model | Best for |
|-------|----------|
| 3B (fast) | Quick classifications, short drafts |
| 7B (balanced) | Most business workflows |
| 14B (quality) | Complex proposals, analysis |

The iPhone app supports the same models for fully offline on-device AI.

---

## Zyrcon iPhone App

The Zyrcon app connects to PRISM over your local network or VPN. From the app you can:

- See live item counts for all three mission desks
- Review and approve pending AI actions with one tap
- Approve email sends, quotes, and invoices before they go out
- Trigger missions from anywhere
- View lead and campaign items with full context

Approvals sync instantly to PRISM. Background refresh keeps item counts current.

---

## Quick Start

**macOS:**
```bash
curl -fsSL https://raw.githubusercontent.com/zyrconlabs/cascadia-os/main/install.sh | bash
```

Installs Homebrew (if needed), SwiftBar, Cascadia OS, and registers a login agent. Everything starts automatically at boot.

**Requirements:** Python 3.11+ and git
→ [macOS quickstart guide](./QUICKSTART_MACOS.md)

---

**Linux:**
```bash
curl -fsSL https://raw.githubusercontent.com/zyrconlabs/cascadia-os/main/install.sh | bash
```

Installs dependencies, Cascadia OS, and sets up system tray integration (Argos for GNOME).

**Requirements:** Python 3.11+ and git
**Optional:** Install [Argos](https://github.com/p-e-w/argos) for GNOME menu bar integration
→ [Linux quickstart guide](./QUICKSTART_LINUX.md)

---

**Windows:**
```powershell
git clone https://github.com/zyrconlabs/cascadia-os.git
powershell -ExecutionPolicy Bypass -File cascadia-os\windows\install.ps1
```

→ [Windows installation guide](./windows/README.md)

---

## Run the Demo

After installing, run the demo — ~90 seconds end-to-end:

```bash
bash demo.sh
```

The demo uses a sample business — Gulf Coast HVAC Services — as its context. All data is seeded locally. Nothing is sent externally.

**What you'll see:**
1. Lead arrives → workflow starts automatically
2. System classifies, enriches, drafts a response
3. Approval gate fires — email held until a human approves
4. **System crashes mid-run** (deliberate)
5. Restarts — resumes from exact same step, zero duplication
6. Approval given → email sent → CRM logged → complete

---

## Mission System

Three pre-built missions ship with Cascadia OS. Each one coordinates multiple operators to complete a business objective end-to-end.

### Revenue Desk
Scans your inbox continuously. Each inbound email is classified by BELL, scored for urgency and value, and written to the `mission_items` table as an actionable item. The Zyrcon iPhone app surfaces these items in real time.

**Triggers:** Inbound email · manual run · daily schedule
**Creates:** `lead` · `quote_request` · `purchase_order` · `invoice` · `overdue_invoice` · `unsold_quote`

### Growth Desk
Generates and schedules marketing campaigns across email, SMS, and social channels. Each campaign draft routes through the approval gate before anything is published.

**Triggers:** Manual run · completed job · daily schedule
**Creates:** Campaign drafts · social posts · email sequences

### Operations Desk
Reviews business operations and surfaces what needs attention. Produces a daily brief from live operator data.

**Triggers:** Manual run · morning schedule
**Creates:** Daily plans · project reviews · risk flags

### Triggering a mission

```bash
# Via PRISM API
curl -X POST http://localhost:6300/api/missions/revenue_desk/run/daily_campaign

# Via Mission Manager directly
curl -X POST http://localhost:6207/api/missions/revenue_desk/run/daily_campaign
```

### Approval flow

High-risk actions (email sends, quote dispatch, invoice sends) are held at an approval gate until a human approves — either via PRISM dashboard or the Zyrcon iPhone app.

→ [Full mission system documentation](./docs/missions.md)

---

## Connectors

| Connector | Port | What it connects |
|---|---:|---|
| Google Accounts | 9020 | Gmail (send + inbound), Calendar, Drive, Contacts |
| Telegram | 9000 | Inbound messages, bot notifications |
| WhatsApp Business | 9001 | WhatsApp Business API |
| Slack | 9003 | Channel messages, notifications |
| Email / SMTP | built-in | SMTP + IMAP, Gmail API mode |

342+ operators and connectors for Pro and Business tiers are available at [zyrcon.store](https://zyrcon.store).

→ [Connector documentation](./docs/connectors.md)

---

## PRISM Dashboard

Open `http://localhost:6300/` while Cascadia is running.

**Surfaces:** Live operator status · Run timeline · Approvals · Revenue items · Observability · Studio · Admin

```bash
GET  :6300/api/prism/overview          # Full system snapshot
GET  :6300/api/prism/runs              # Live run states
GET  :6300/api/prism/approvals         # Pending human decisions
POST :6300/api/prism/approve           # Approve or deny a gated action
GET  :6300/api/missions/{id}/items     # Revenue Desk items
PATCH :6300/api/missions/items/{id}    # Update item status
```

→ [Full API reference](./docs/api.md)

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

**Outbox pattern**

External actions (email sends, webhook calls) write to an outbox before executing. Each gets a SHA-256 idempotency key. A crash between writing and sending triggers exactly one retry on restart — never duplicated, never lost. Enable with `OUTBOX_ENABLED=true` in config.

---

## Tests

1242 tests passing, 0 failing.

```bash
cd cascadia-os
pytest
```

Test coverage includes: crash recovery, durability layer, operator registry, approval gates, SENTINEL security, VAULT persistence, FLINT process supervision, BELL messaging, PRISM dashboard API, mission system lifecycle, mission items pipeline, schema-driven connectors, and connector framework.

---

## Design rules

1. FLINT supervises. FLINT does not execute workflows.
2. No side effect executes twice. Idempotency is enforced at the DB layer.
3. Resume reads the journal. Resume does not guess.
4. Dangerous actions require policy clearance. Policy is separate from capability.
5. Blocking a run is explicit. Auto-resuming a blocked run is never allowed.
6. The module that owns execution does not own policy. The module that owns policy does not own storage.

---

## Screenshots

| Asset | What it shows |
|---|---|
| [`assets/prism.png`](./assets/prism.png) | Main PRISM dashboard with operators online |
| [`assets/settings.png`](./assets/settings.png) | Hardware detection and AI mode selection |
| [`assets/health.png`](./assets/health.png) | Health & observability page |
| [`assets/approvals.png`](./assets/approvals.png) | Approval gate UI with risk badges |
| [`assets/chief.png`](./assets/chief.png) | CHIEF with Almanac help pane |
| [`assets/recon_dashboard.png`](./assets/recon_dashboard.png) | RECON worker dashboard |
| [`assets/crash_recovery.png`](./assets/crash_recovery.png) | Deliberate mid-run crash + correct resume |
| [`assets/gpu_inference.png`](./assets/gpu_inference.png) | Local Apple Silicon inference |

---

## Docs

- [Quickstart — macOS](./QUICKSTART_MACOS.md)
- [Quickstart — Linux](./QUICKSTART_LINUX.md)
- [Windows Installation](./windows/README.md)
- [PRISM Manual](./PRISM_MANUAL.md)
- [Mission System](./docs/missions.md)
- [API Reference](./docs/api.md)
- [Connectors](./docs/connectors.md)
- [Operators](./docs/operators.md)
- [Tiers & Pricing](./docs/tiers_and_pricing.md)
- [EU AI Act Compliance](./docs/eu_ai_act_compliance.md)
- [Production Checklist](./docs/production_checklist.md)
- [Contributing](./CONTRIBUTING.md)
- [Security Policy](./SECURITY.md)
- [Story behind the project](./STORY.md)
- [Full docs index](./docs/)

---

## Why 2026

AI Infrastructure Software: $126B → $230B (83% growth)
On-premise AI: 46% of total market, 24% CAGR
EU AI Act Articles 8-15 deadline: August 2, 2026
First responder wins 78% of leads in field services
Industry average lead response: 47 hours
Cascadia OS average lead response: 4 minutes

---

## Licence

Cascadia OS core is licensed under the **Apache License 2.0**.
See [LICENSE](./LICENSE) for full terms.

**Dependencies:** llama.cpp (MIT) · Qwen3 (Apache 2.0)

---

*Built in Houston, Texas — [Zyrcon Labs](https://github.com/zyrconlabs) · 2026.5*
