# CONNECT Integration Operator (C9)

**ID:** connect | **Port:** 8200 | **Tier:** pro

Webhook ingest, HTTP outbound calls, and CRM write integration hub. Accepts inbound webhooks via HTTP POST /ingest, dispatches outbound HTTP calls (approval-gated), and formats/publishes CRM write payloads (approval-gated).

## Actions

| Action | Approval Required | Description |
|---|---|---|
| `register_webhook` | No | Register a webhook ID with a target NATS subject |
| `list_webhooks` | No | List all registered webhooks |
| `ingest` | No | Internal — triggered by HTTP POST /ingest |
| `http_outbound` | **Yes** | Send an HTTP request to an external URL |
| `crm_write` | **Yes** | Format and publish a CRM write payload |

## CRM Types

`salesforce`, `hubspot`, `generic`

## NATS Subjects

- Call: `cascadia.operators.connect.call`
- Response: `cascadia.operators.connect.response`
- Events (ingest): `cascadia.operators.connect.event`
- CRM writes: `cascadia.operators.connect.crm`
- Approvals: `cascadia.approvals.request`

## HTTP Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/ingest` | Receive webhook payload → publishes to connect.event |
| `GET` | `/health` | Health check |

## Example Payloads

```json
{ "action": "register_webhook", "params": { "webhook_id": "gh-push", "name": "GitHub Push", "target_subject": "cascadia.operators.connect.event" } }
{ "action": "http_outbound", "params": { "url": "https://api.example.com/notify", "method": "POST", "headers": {"Content-Type": "application/json"}, "body": {"event": "deploy"} } }
{ "action": "crm_write", "params": { "crm_type": "hubspot", "record_type": "contact", "data": {"email": "user@example.com", "name": "Test User"} } }
```

## Health

```
GET http://localhost:8200/health
→ {"status":"ok","operator":"connect"}
```

## Running

```bash
bash install.sh
python3 operator.py
```
