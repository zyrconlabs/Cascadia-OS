# Approval Gate Connector (CON-116)

**Port:** 9988  
**Tier:** Lite+  
**Category:** Runtime  

Human-in-the-loop approval workflow. Connectors and operators publish approval requests here; approvers review and decide via HTTP or NATS; outcomes are published back to the requester's reply subject.

## HTTP Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/requests` | Create an approval request |
| `GET` | `/requests` | List all requests |
| `GET` | `/requests/pending` | List pending requests only |
| `GET` | `/requests/{id}` | Get a specific request |
| `POST` | `/requests/{id}/approve` | Approve a request |
| `POST` | `/requests/{id}/deny` | Deny a request |
| `GET` | `/health` | Health check |

## Creating a Request

```json
{
  "connector": "rest-connector",
  "description": "POST https://api.crm.com/leads — approval required before REST write",
  "action": {"method": "POST", "url": "https://api.crm.com/leads", "body": {"name": "Acme"}},
  "reply_subject": "cascadia.connectors.rest-connector.approved",
  "timeout_seconds": 3600
}
```

## Approving

```bash
curl -X POST http://localhost:9988/requests/{id}/approve \
  -H 'Content-Type: application/json' \
  -d '{"decided_by": "andy@example.com", "reason": "Looks good"}'
```

## Outcome envelope

Published to `reply_subject` after decision:

```json
{
  "connector": "approval-gate",
  "request_id": "abc-123",
  "decision": "approved",
  "decided_by": "andy@example.com",
  "reason": "Looks good",
  "action": { ... },
  "timestamp": "2025-01-01T12:00:00+00:00"
}
```

On denial, `action` is `null`.

## NATS

**Subscribe:** `cascadia.approvals.>`

| Subject | Description |
|---|---|
| `cascadia.approvals.request` | Inbound approval request from a connector |
| `cascadia.approvals.decide` | Submit a decision (`{request_id, decision, decided_by, reason}`) |
| `cascadia.approvals.queued` | Published after request is queued |
| `cascadia.approvals.outcome` | Published after a decision is made |

## Timeout

Pending requests that exceed `timeout_seconds` (default 3600) are automatically marked `timed_out` and the outcome is published to `reply_subject`.
