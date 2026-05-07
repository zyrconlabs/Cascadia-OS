# NATS Reference

NATS is the internal message bus that connects operators, connectors, and platform components in Cascadia OS. All cross-component communication that is not a direct HTTP call uses NATS subjects.

Port: **4222** (`nats://localhost:4222`)

NATS was chosen because it is lightweight, requires no broker configuration, supports both publish/subscribe and request/reply patterns, and starts embedded alongside the platform stack without additional infrastructure.

---

## Connection

Every connector in the codebase uses the same connect pattern. NATS requires the `nats-py` package:

```bash
pip install nats-py
```

Standard async connection pattern (extracted from `cascadia/connectors/discord/connector.py`, `sms/connector.py`, `slack/connector.py`, et al.):

```python
import asyncio
import nats

NATS_URL = "nats://localhost:4222"

async def _nats_main() -> None:
    try:
        import nats
    except ImportError:
        # Degrade gracefully if nats-py is not installed
        await asyncio.sleep(float("inf"))
        return

    nc = await nats.connect(NATS_URL)

    # subscribe and publish here...

    try:
        await asyncio.sleep(float("inf"))
    finally:
        await nc.drain()   # flush pending messages before disconnect

def main() -> None:
    asyncio.run(_nats_main())
```

`nc.drain()` in the finally block ensures in-flight publishes complete before the process exits. Always include it.

---

## Subject Naming Convention

Three top-level namespaces are used in the codebase:

### Connector subjects

```
cascadia.connectors.[connector-id].[event-type]
```

| Pattern | Meaning |
|---------|---------|
| `cascadia.connectors.[id].>` | Wildcard — subscribe to all events for this connector |
| `cascadia.connectors.[id].response` | Response from connector after executing an action |
| `cascadia.connectors.[id].events` | Inbound events from the external platform |
| `cascadia.connectors.[id].call` | Inbound action call requests |
| `cascadia.connectors.[id].registered` | Webhook source registered (webhook-broker) |
| `cascadia.connectors.[id].deregistered` | Webhook source deregistered (webhook-broker) |
| `cascadia.connectors.[id].created` | Job/schedule created (scheduler connector) |
| `cascadia.connectors.[id].cancelled` | Job/schedule cancelled (scheduler connector) |

Real examples from source:
- `cascadia.connectors.discord-connector.>`
- `cascadia.connectors.telegram-connector.>`
- `cascadia.connectors.sms-connector.>`
- `cascadia.connectors.slack-connector.>`
- `cascadia.connectors.gmail-connector.>`
- `cascadia.connectors.google-calendar-connector.>`
- `cascadia.connectors.zapier-connector.>`
- `cascadia.connectors.webhook-broker.>`

### System operator subjects (zyrcon namespace)

```
zyrcon.operator.[event-type]
zyrcon.mission.[event-type]
zyrcon.chief.[event-type]
zyrcon.beacon.[event-type]
```

Found in `cascadia/automation/supervisor.py` and `cascadia/shared/service_runtime.py`:

| Subject | Publisher | Subscriber | Description |
|---------|-----------|------------|-------------|
| `zyrcon.operator.health` | Every operator (ServiceRuntime) | Supervisor | Health heartbeat — published every 30s |
| `zyrcon.operator.failure` | Operators, watchdog | Supervisor | FailureEvent for crashed/stuck operators |
| `zyrcon.operator.retry` | Supervisor | Operator | Trigger a retry attempt |
| `zyrcon.operator.restart` | Supervisor | FLINT | Request process restart |
| `zyrcon.chief.escalate` | Supervisor | CHIEF | Escalate unresolvable failure to CHIEF |
| `zyrcon.beacon.decision_request` | Supervisor | BEACON | Request routing decision |
| `zyrcon.mission.dead_letter` | Supervisor | Dead-letter handler | Terminal failure — no more retries |

### Mission subjects

```
cascadia.missions.[event-type]
```

Used internally for mission lifecycle events (server-side bus only, not exposed to third-party operators).

### Approval gate subject

```
cascadia.approvals.request
```

Used by connectors that require human approval before executing actions. See Approval Gate Pattern below.

---

## Standard Event Envelope

All events published to NATS should use this envelope (from `docs/connectors.md`):

```json
{
  "connector": "telegram",
  "event_type": "message",
  "timestamp": "2026-04-30T12:00:00Z",
  "message_id": "msg_abc123",
  "from": "user_id_or_phone",
  "to": "channel_or_recipient",
  "text": "The message text",
  "media": null,
  "raw": { "...original platform payload..." }
}
```

| Field | Type | Required | Notes |
|-------|------|:--------:|-------|
| `connector` | string | **yes** | Connector ID from manifest |
| `event_type` | string | **yes** | `message`, `reaction`, `status`, `error` |
| `timestamp` | ISO 8601 string | **yes** | UTC |
| `message_id` | string | **yes** | Platform-provided ID for deduplication |
| `from` | string | **yes** | Sender identifier |
| `to` | string | no | Recipient (for outbound) |
| `text` | string | no | Plain text content |
| `raw` | object | **yes** | Unmodified original platform payload |

---

## Publishing Events

Publish by encoding JSON to bytes. All connectors in the codebase use this pattern:

```python
response = {
    "connector": NAME,
    "action": action,
    "result": result
}
await nc.publish(
    RESPONSE_SUBJECT,          # e.g. "cascadia.connectors.discord-connector.response"
    json.dumps(response).encode("utf-8"),
)
```

For inbound events received from external platforms (e.g. a Telegram message arriving):

```python
envelope = {
    "connector": NAME,
    "event_type": "message",
    "timestamp": datetime.utcnow().isoformat() + "Z",
    "message_id": str(msg_id),
    "from": sender,
    "text": text,
    "raw": original_payload,
}
await nc.publish(
    f"cascadia.connectors.{NAME}.events",
    json.dumps(envelope).encode("utf-8"),
)
```

---

## Subscribing to Events

Subscribe using a wildcard subject (`>`) to receive all events addressed to your connector:

```python
NAME = "discord-connector"

subject = f"cascadia.connectors.{NAME}.>"

async def _cb(msg):
    await handle_event(nc, msg.subject, msg.data)

await nc.subscribe(subject, cb=_cb)
```

Inside `handle_event`, use `msg.subject` to distinguish sub-topics (`.call`, `.events`, `.response`, etc.) and `msg.data` as the raw bytes payload to decode.

The callback must be `async`. Use `await nc.subscribe(subject, cb=_cb)` — synchronous callbacks are not supported by nats-py for async connections.

---

## Approval Gate Pattern

Connectors that write to external systems must request approval before executing write actions. The pattern is consistent across all connectors (discord, slack, sms, etc.):

**Constants (top of connector.py):**

```python
APPROVAL_SUBJECT = "cascadia.approvals.request"
ACTIONS_REQUIRING_APPROVAL = {"send_message"}
```

**In the event handler:**

```python
if action in ACTIONS_REQUIRING_APPROVAL:
    approval_request = {
        "connector": NAME,
        "subject": subject,
        "action": action,
        "payload": payload,
        "reason": f"Action '{action}' requires human approval before execution.",
    }
    await nc.publish(
        APPROVAL_SUBJECT,
        json.dumps(approval_request).encode("utf-8"),
    )
    return   # do NOT execute — halt here and wait for approval
```

The ApprovalStore receives the request, creates an approval record, and surfaces it in PRISM. When the human approves, the approved payload is forwarded to the connector for execution. The connector does not need to implement the resume path — the approval system handles re-delivery.

---

## System Subjects Reference Table

All subjects confirmed in source code. Not exhaustive — internal routing subjects may exist that are not listed here.

| Subject | Namespace | Publisher | Subscriber | Description |
|---------|-----------|-----------|------------|-------------|
| `cascadia.connectors.[id].>` | connector | External platform events | Connector itself | All connector events |
| `cascadia.connectors.[id].response` | connector | Connector | Operator / Workflow | Result of executing an action |
| `cascadia.connectors.[id].events` | connector | Connector | VANGUARD / operators | Normalized inbound event |
| `cascadia.approvals.request` | system | Connector, Operator | ApprovalStore | Approval required before action |
| `zyrcon.operator.health` | system | ServiceRuntime (all) | Supervisor | 30s health heartbeat |
| `zyrcon.operator.failure` | system | Operator / Watchdog | Supervisor | Failure event (crash, timeout, block) |
| `zyrcon.operator.retry` | system | Supervisor | Operator | Trigger retry |
| `zyrcon.operator.restart` | system | Supervisor | FLINT | Request process restart |
| `zyrcon.chief.escalate` | system | Supervisor | CHIEF | Unresolvable failure escalation |
| `zyrcon.beacon.decision_request` | system | Supervisor | BEACON | Routing decision request |
| `zyrcon.mission.dead_letter` | system | Supervisor | Dead-letter handler | Terminal failure |
| `cascadia.missions.*` | system | Mission runner | Mission subscribers | Mission lifecycle (internal) |

---

## Development Tips

### Monitor NATS traffic during development

Install the NATS CLI and subscribe to everything:

```bash
# Install nats CLI (macOS)
brew install nats-io/nats-tools/nats

# Watch all subjects
nats sub ">"

# Watch only connector events
nats sub "cascadia.connectors.>"

# Watch only system events
nats sub "zyrcon.>"
```

### Test publish from the CLI

```bash
# Publish a test action to the discord connector
nats pub "cascadia.connectors.discord-connector.call" \
  '{"action": "send_message", "channel_id": "123", "content": "test"}'
```

### Common mistakes

**Wrong subject format.** Use hyphens, not underscores, in connector IDs that contain multiple words. The ID comes from your manifest `id` field. If your manifest has `"id": "google-calendar-connector"`, your subject is `cascadia.connectors.google-calendar-connector.>` — not `google_calendar`.

**Missing envelope fields.** The `connector`, `event_type`, `timestamp`, `message_id`, `from`, and `raw` fields are required. A missing `message_id` means the platform cannot deduplicate events.

**Sync publish in async context.** Use `await nc.publish(...)`. Calling `nc.publish(...)` without await in an async function silently discards the message.

**Not draining on shutdown.** If you don't call `await nc.drain()` before exiting, in-flight published messages may be lost. Always wrap the main loop in try/finally.

**Subscribing before connecting.** Call `await nats.connect(NATS_URL)` first, then subscribe. Subscribing on an unconnected client raises an error.
