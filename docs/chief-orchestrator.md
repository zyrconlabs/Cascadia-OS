# CHIEF Orchestrator

Port: 6210 | Tier: 3 | Module: `cascadia.chief.server`

CHIEF is the task routing layer for Cascadia OS. It receives a natural-language task description, identifies the right registered operator via CREW, dispatches to it via BEACON, and returns the result. It does not execute tasks itself.

## Responsibility boundaries

| Owns | Does NOT own |
|------|-------------|
| Operator selection | Operator execution |
| BEACON dispatch | Capability validation (BEACON/CREW own that) |
| Reply formatting | Channel I/O (VANGUARD owns that) |
| CREW self-registration | Session management (BELL owns that) |

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness check |
| POST | `/task` | Receive and dispatch a task |
| GET | `/tasks/{task_id}` | Task status (v1 stub) |
| GET | `/tasks` | Task history (v1 stub) |

### POST /task

Request:
```json
{
  "task": "Draft a proposal for warehouse mezzanine installation",
  "source_channel": "telegram",
  "reply_channel": "telegram",
  "sender": "andy",
  "tenant_id": "default",
  "metadata": {"chat_id": 123456}
}
```

Response:
```json
{
  "ok": true,
  "task_id": "abc123",
  "mode": "sync",
  "selected_type": "operator",
  "selected_target": "quote_brief",
  "reply_text": "Completed by quote_brief\n\nHere is your proposal...",
  "raw_result": {}
}
```

`selected_type` values: `"operator"` | `"status"` | `"none"`

## Message flow

```
VANGUARD /inbound (telegram)
  → background thread
  → CHIEF POST /task
      → operator_selector: keyword match → CREW /crew → capability match
      → BEACON POST /route {sender:chief, message_type:run.execute, target:..., message:{...}}
          → Operator POST /message (forwarded by BEACON)
          → result returned sync
  → reply_text formatted
  → VANGUARD sends reply via Telegram connector POST /send
```

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `CHIEF_PORT` | `6210` | Port CHIEF binds to |
| `CREW_URL` | `http://127.0.0.1:5100` | CREW registry URL |
| `BEACON_URL` | `http://127.0.0.1:6200` | BEACON orchestrator URL |
| `MISSION_MANAGER_URL` | `http://127.0.0.1:6207` | Mission Manager URL |
| `BELL_URL` | `http://127.0.0.1:6204` | BELL chat interface URL |
| `TELEGRAM_URL` | `http://127.0.0.1:9000` | Telegram connector URL |

All URLs are resolved from `config.json` at startup; env vars override.

## Operator selector

File: `cascadia/chief/operator_selector.py`

Two-pass selection:

**Pass 1 — keyword match** against `_KEYWORD_MAP` (list of keyword groups, each with associated preferred operators and capability signals). First match wins.

**Pass 2 — CREW query** `GET /crew` returns all registered operators with their capability lists. CHIEF scores each operator by capability overlap; preferred operator names receive score 1.0.

### Adding a new operator type

1. Add a group to `_KEYWORD_MAP`:
```python
{
    "keywords": ["invoice", "billing", "payment", "overdue"],
    "preferred_operators": ["collect"],
    "capabilities": ["invoice.create", "payment.collect"],
},
```
2. Ensure the operator registers those capabilities with CREW at startup.

## CREW self-registration

At startup, CHIEF registers itself with CREW as:
```json
{
  "operator_id": "chief",
  "capabilities": [
    "task.orchestrate", "run.execute", "mission.select",
    "operator.assign", "report.request"
  ]
}
```
The `run.execute` capability is required because BEACON validates it before forwarding CHIEF's `/route` calls.

## Status commands

| Command | Response |
|---------|----------|
| `/status` | Health of all core components + operator count |
| `/missions` | Last 5 mission runs |
| `/operators` | All registered operators with capabilities |
| `/help` | Command list + task examples |

## v1 limitations

- Operator selection is deterministic keyword/capability matching only — no LLM planning.
- No durable task state (in-memory only; tasks are not persisted).
- Synchronous dispatch — long-running operators will block the CHIEF /task response.
- Mission Manager integration is stubbed (status summary only).
- BELL not yet wired as a reporting layer.
