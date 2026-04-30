# Webhook Broker Connector (CON-109)

**Port:** 9981  
**Tier:** Lite+  
**Category:** Runtime  

Receives inbound webhooks from external services, validates HMAC signatures, and routes events to Cascadia NATS subjects.

## HTTP Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/webhook/{source_id}` | Ingest a webhook event |
| `POST` | `/webhook/{source_id}/{event_type}` | Ingest with explicit event type |
| `POST` | `/sources` | Register a new webhook source |
| `GET` | `/health` | Health check |

## Registering a Source

```bash
curl -X POST http://localhost:9981/sources \
  -H 'Content-Type: application/json' \
  -d '{
    "source_id": "github",
    "secret": "my_webhook_secret",
    "target_subject": "cascadia.events.github",
    "sig_header": "X-Hub-Signature-256",
    "sig_prefix": "sha256="
  }'
```

## Sending a Webhook

```bash
curl -X POST http://localhost:9981/webhook/github/push \
  -H 'Content-Type: application/json' \
  -H 'X-Hub-Signature-256: sha256=<computed_sig>' \
  -d '{"ref": "refs/heads/main", "commits": [...]}'
```

## NATS

**Subscribe (control):** `cascadia.connectors.webhook-broker.>`  
**Publish (events):** configurable per source — default `cascadia.connectors.webhook-broker.events.{event_type}`

### Control events

| Subject | Description |
|---|---|
| `cascadia.connectors.webhook-broker.register` | Register a source via NATS |
| `cascadia.connectors.webhook-broker.deregister` | Remove a source |
| `cascadia.connectors.webhook-broker.registered` | Ack after registration |
| `cascadia.connectors.webhook-broker.deregistered` | Ack after removal |

## Signature validation

Supports HMAC-SHA256 (GitHub, Stripe, Shopify style). Configure `sig_header` and `sig_prefix` per source. Sources with no `secret` accept all inbound payloads (open webhook).

## Envelope format

```json
{
  "connector": "webhook-broker",
  "source": "github",
  "event_type": "push",
  "data": { ... },
  "headers": { "x-github-event": "push" },
  "timestamp": "2025-01-01T00:00:00+00:00"
}
```
