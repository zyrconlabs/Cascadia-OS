# Component Roles — Cascadia OS

Canonical definition of what each core component owns and does not own.

> **WARNING — naming conflict resolved 2026-05-13:**
> The operator previously named `chief` has been renamed to **`quote_brief`**.
> The name `chief` is now reserved exclusively for the core orchestration service
> described below. Do not create operators named `chief`.

## Core services

| Component | Port | Tier | Role |
|-----------|------|------|------|
| CREW | 5100 | 1 | Operator registry and capability validation |
| VAULT | 5101 | 1 | Secret and credential storage |
| SENTINEL | 5102 | 1 | Authorization and permission enforcement |
| CURTAIN | 5103 | 1 | Encryption |
| LICENSE_GATE | 6100 | 0 | License tier enforcement |
| BEACON | 6200 | 2 | Capability-checked task routing |
| STITCH | 6201 | 2 | Workflow definition and execution runtime |
| VANGUARD | 6202 | 2 | External channel gateway (inbound normalize + outbound dispatch) |
| HANDSHAKE | 6203 | 2 | Outbound HTTP/webhook/email execution |
| BELL | 6204 | 2 | Human-in-the-loop chat interface and approval collection |
| ALMANAC | 6205 | 2 | Scheduling and calendar |
| MISSION_MANAGER | 6207 | 2 | Mission package lifecycle and run tracking |
| DEPOT_API | 6208 | 2 | Operator package store |
| **CHIEF** | **6210** | **3** | **Task orchestrator — routes tasks to operators via BEACON** |
| PURCHASE_WEBHOOK | 6209 | 3 | Stripe/purchase event ingestion |
| VANTAGE | 6212 | 2 | Analytics and reporting |
| SYNC_PUBLISHER | 6213 | 2 | Data sync and pub/sub |
| PRISM | 6300 | 3 | Operator dashboard |

## Component ownership rules

### VANGUARD (6202) — Communication gateway
- **Owns:** inbound channel normalization (telegram, email, webhook, SMS, API), outbound message dispatch, chat_id propagation for Telegram replies
- **Does NOT own:** operator selection, task routing, encryption, sessions

### CHIEF (6210) — Task orchestrator
- **Owns:** operator selection, BEACON dispatch, reply formatting, CREW self-registration
- **Does NOT own:** operator execution, capability validation (BEACON/CREW own that), channel I/O (VANGUARD owns that), session state (BELL owns that)

### BEACON (6200) — Routing layer
- **Owns:** capability-checked routing, HTTP forwarding to operator ports
- **Does NOT own:** operator selection, task state, callbacks

### CREW (5100) — Registry
- **Owns:** operator registration, capability validation
- **Does NOT own:** routing, execution, task state

### BELL (6204) — Human chat interface
- **Owns:** human sessions, approval collection, workflow triggering via WorkflowRuntime
- **Does NOT own:** external channel routing, general task dispatch

## Inbound Telegram task — canonical flow

```
Telegram → Telegram Connector (9000)
         → VANGUARD (6202) /inbound [normalize + preserve chat_id]
         → [background thread]
         → CHIEF (6210) /task [select operator + dispatch]
         → BEACON (6200) /route [capability check + forward]
         → Operator :[port] /message [execute]
         → result returned sync
         → VANGUARD → Telegram Connector (9000) /send [reply]
         → Telegram message delivered
```

VANGUARD must never call BEACON directly.
VANGUARD must never select operators.
CHIEF must never call operators directly (always via BEACON).

## Renamed operators

| Old name | New name | Reason |
|----------|----------|--------|
| `chief` | `quote_brief` | Name `chief` reserved for core orchestration service |

The `quote_brief` operator (port 8006) generates quotes, proposals, and executive briefs.
Capabilities: `brief.generate`, `orchestration.probe`, `synthesis.generate`, `calendar_scheduling`.
