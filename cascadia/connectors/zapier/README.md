# Zapier Inbound Connector (B4)

Receive Zapier webhook triggers and send data to Zapier webhook actions.

| Property | Value |
|---|---|
| ID | `zapier-connector` |
| Version | 1.0.0 |
| Port | **9030** |
| Auth type | None (open webhook receiver) |
| Tier | lite |
| Category | integration |

## NATS Subjects

| Subject | Direction | Purpose |
|---|---|---|
| `cascadia.connectors.zapier-connector.>` | inbound | Action calls from operators |
| `cascadia.connectors.zapier-connector.response` | outbound | Action results |
| `cascadia.connectors.zapier-connector.event` | outbound | Inbound Zapier webhook payloads |

Write actions (`send_to_zapier`, `register_hook`, `delete_hook`) are gated through
`cascadia.approvals.request` before execution.

## HTTP Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Returns `{"status":"ok","connector":"zapier-connector"}` |
| `POST` | `/webhook/{hook_id}` | Receives a Zapier trigger payload and publishes to NATS |

## Actions

### receive_webhook _(read — executes immediately)_

Returns the current hook registry (informational — actual ingest happens over HTTP).

```json
{"action": "receive_webhook"}
```

### list_hooks _(read — executes immediately)_

```json
{"action": "list_hooks"}
```

Response:
```json
{
  "ok": true,
  "hooks": [
    {
      "hook_id": "my-hook",
      "name": "My Zap",
      "target_operator": "lead-intake",
      "created_at": "2026-04-30T12:00:00+00:00"
    }
  ]
}
```

### register_hook _(requires approval)_

Registers a hook ID and returns the full webhook URL to paste into Zapier.

```json
{
  "action": "register_hook",
  "hook_id": "my-hook",
  "name": "My Zap",
  "target_operator": "lead-intake"
}
```

Response (after approval):
```json
{
  "ok": true,
  "hook_id": "my-hook",
  "webhook_url": "http://127.0.0.1:9030/webhook/my-hook"
}
```

### delete_hook _(requires approval)_

```json
{
  "action": "delete_hook",
  "hook_id": "my-hook"
}
```

### send_to_zapier _(requires approval)_

POST a JSON payload to an external Zapier webhook catch URL.

```json
{
  "action": "send_to_zapier",
  "webhook_url": "https://hooks.zapier.com/hooks/catch/12345/abcdef/",
  "payload": {
    "name": "Acme Corp",
    "email": "contact@acme.com"
  }
}
```

Response (after approval):
```json
{
  "ok": true,
  "status_code": 200,
  "response": "1"
}
```

## Inbound Webhook Flow

1. Configure a Zap in Zapier with a **Webhooks by Zapier** trigger set to "Catch Hook".
2. Call `register_hook` (via NATS) to obtain the Cascadia-side webhook URL.
3. Paste that URL into Zapier's webhook trigger.
4. When Zapier fires, it POSTs to `POST /webhook/{hook_id}`.
5. The connector publishes an event envelope to `cascadia.connectors.zapier-connector.event`:

```json
{
  "connector": "zapier-connector",
  "hook_id": "my-hook",
  "registered": true,
  "target_operator": "lead-intake",
  "data": { "<zapier payload fields>" },
  "timestamp": "2026-04-30T12:00:00+00:00"
}
```

Target operators subscribe to `cascadia.connectors.zapier-connector.event` and
filter on `hook_id` or `target_operator`.

## Health Check

```
GET http://localhost:9030/health
```

```json
{"status": "ok", "connector": "zapier-connector"}
```

## Running

```bash
bash install.sh
python connector.py
```

Requires `nats-py` (`pip install nats-py`). The HTTP server starts on port 9030
regardless of NATS availability.
