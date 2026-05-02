# Cascadia OS API Reference

**Base URL:** `http://localhost:6300`

All endpoints return JSON. All request bodies are JSON (`Content-Type: application/json`).

---

## Authentication

Currently open on local network — no token required for local requests.

**HMAC request signing** is supported for inbound webhooks. The `X-Cascadia-Signature` header carries a HMAC-SHA256 digest of the request body using a shared secret configured in HANDSHAKE.

**API key auth** (`X-Cascadia-Key`) is supported and can be enabled by setting the `CASCADIA_INTERNAL_KEY` environment variable. Full per-client API key management is coming in v0.50.

---

## PRISM Endpoints (port 6300)

PRISM is the API gateway. All mission and operator data flows through it.

---

### System overview

```
GET /api/prism/overview
```

Returns a full system snapshot: operator status, active runs, pending approvals, hardware info.

**Response:**
```json
{
  "operators": {
    "email": { "status": "online", "port": 8010 },
    "scout": { "status": "online", "port": 7002 },
    "recon": { "status": "online", "port": 7001 }
  },
  "runs": {
    "active": 1,
    "waiting_approval": 0,
    "completed_today": 3
  },
  "approvals": {
    "pending": 0
  },
  "hardware": {
    "ram_gb": 16,
    "gpu_type": "apple_silicon",
    "chip": "Apple M1"
  }
}
```

---

### Operator status

```
GET /api/prism/operators
```

Returns status for all registered operators.

**Response:**
```json
{
  "operators": [
    {
      "id": "email",
      "name": "Email Operator",
      "port": 8010,
      "status": "online",
      "version": "1.0.0",
      "last_seen": "2026-05-02T09:14:00Z"
    },
    {
      "id": "scout",
      "name": "SCOUT",
      "port": 7002,
      "status": "online",
      "version": "2.1.0",
      "last_seen": "2026-05-02T09:14:01Z"
    }
  ]
}
```

---

### Active runs

```
GET /api/prism/runs
```

Returns all active and recent runs across all operators.

**Response:**
```json
{
  "runs": [
    {
      "run_id": "run_a1b2c3",
      "operator": "recon",
      "goal": "Find roofing contractors in Houston TX",
      "run_state": "running",
      "current_step": "web_search",
      "started_at": "2026-05-02T09:10:00Z"
    },
    {
      "run_id": "run_d4e5f6",
      "operator": "email",
      "goal": "Send quote to Gulf Coast Realty",
      "run_state": "waiting_human",
      "current_step": "send_email",
      "started_at": "2026-05-02T08:55:00Z"
    }
  ]
}
```

---

### Pending approvals

```
GET /api/prism/approvals
```

Returns all approvals waiting for a human decision.

**Response:**
```json
{
  "approvals": [
    {
      "id": "appr_abc123",
      "run_id": "run_d4e5f6",
      "action_key": "email.send",
      "risk_level": "high",
      "description": "Send quote to Gulf Coast Realty <procurement@gulfcoastrealty.com>",
      "payload": {
        "to": "procurement@gulfcoastrealty.com",
        "subject": "Proposal — Roof Replacement at 2211 Richmond Ave",
        "body": "..."
      },
      "created_at": "2026-05-02T08:55:12Z"
    }
  ]
}
```

---

### Approve an action

```
POST /api/prism/approve
```

**Request:**
```json
{
  "approval_id": "appr_abc123",
  "decision": "approved",
  "note": "Looks good, send it."
}
```

Set `"decision": "rejected"` to deny.

**Response:**
```json
{
  "ok": true,
  "approval_id": "appr_abc123",
  "decision": "approved",
  "run_id": "run_d4e5f6"
}
```

---

### Get mission items (via PRISM proxy)

```
GET /api/missions/{mission_id}/items
```

Proxies to Mission Manager on port 6207. The iPhone app calls this endpoint — it only needs to know the PRISM URL.

**Parameters:**
| Parameter | Description |
|---|---|
| `mission_id` | Mission identifier, e.g. `revenue_desk` |
| `status` | (query) Filter: `new` · `approved` · `dismissed` · `completed` |
| `limit` | (query) Max results, default 20 |

**Response:**
```json
{
  "items": [
    {
      "id": "item_7f3a9b",
      "item_type": "quote_request",
      "title": "Re-roof estimate — 4821 Westheimer",
      "description": "Customer requesting full re-roof, approx 3,000 sq ft",
      "customer_name": "John Martinez",
      "company_name": "Martinez Properties",
      "amount": null,
      "confidence": 0.91,
      "urgency_score": 20,
      "value_score": 30,
      "recommended_action": "Draft quote reply",
      "status": "new",
      "created_at": "2026-05-02T08:47:11Z"
    }
  ],
  "total": 1
}
```

---

### Update mission item status (via PRISM proxy)

```
PATCH /api/missions/items/{item_id}
```

**Request:**
```json
{ "status": "dismissed" }
```

**Valid values:** `new` · `approved` · `dismissed` · `completed` · `in_progress`

**Response:**
```json
{
  "item_id": "item_7f3a9b",
  "status": "dismissed"
}
```

---

## Mission Manager Endpoints (port 6207)

The Mission Manager owns the mission catalog, run lifecycle, and items table. PRISM proxies all mission routes to port 6207 — you generally don't need to call it directly unless building tooling.

---

### Health check

```
GET /healthz
```

```json
{ "status": "ok", "service": "mission_manager", "port": 6207 }
```

---

### Mission catalog

```
GET /api/missions/catalog
```

Returns all available missions (installed and not installed).

---

### Installed missions

```
GET /api/missions/installed
```

Returns only installed missions.

---

### Mission detail

```
GET /api/missions/{mission_id}
```

---

### Mission status

```
GET /api/missions/{mission_id}/status
```

Returns live run counts and required operator/connector status.

```json
{
  "mission_id": "revenue_desk",
  "status": "installed",
  "installed": true,
  "pending_approvals": 0,
  "active_runs": 1,
  "failed_runs_24h": 0,
  "required_operators": { "email": "unknown", "scout": "unknown" },
  "required_connectors": {},
  "tier_required": "free"
}
```

---

### Mission health

```
GET /api/missions/{mission_id}/health
```

Returns a health score (0–100) with per-check results.

```json
{
  "mission_id": "revenue_desk",
  "score": 85,
  "status": "healthy",
  "checks": [
    { "id": "manifest_valid",      "label": "Manifest valid",          "status": "pass" },
    { "id": "mobile_schema_exists","label": "Mobile schema exists",     "status": "pass" },
    { "id": "prism_schema_exists", "label": "PRISM schema exists",      "status": "pass" },
    { "id": "workflow_files_exist","label": "Workflow files exist",     "status": "pass" },
    { "id": "installed",           "label": "Mission installed",        "status": "pass" },
    { "id": "operators",           "label": "Required operators online","status": "unknown" }
  ]
}
```

---

### Run a workflow

```
POST /api/missions/{mission_id}/run/{workflow_id}
```

**Request:**
```json
{
  "trigger_type": "manual",
  "input": {}
}
```

**Response:**
```json
{
  "mission_run_id": "a1b2c3d4-e5f6-...",
  "mission_id": "revenue_desk",
  "workflow_id": "daily_campaign",
  "status": "running"
}
```

---

### List runs

```
GET /api/missions/{mission_id}/runs
```

Returns the 20 most recent runs for the mission.

---

### Resume a run

```
POST /api/missions/{mission_id}/runs/{run_id}/resume
```

**Request:**
```json
{
  "decision": "approved",
  "approval_id": "appr_abc123",
  "note": "Approved."
}
```

---

### Retry a failed run

```
POST /api/missions/{mission_id}/runs/{run_id}/retry
```

Creates a new run using the same workflow and input as the failed run.

---

### Mission items

```
GET  /api/missions/{mission_id}/items
POST /api/missions/{mission_id}/items
PATCH /api/missions/items/{item_id}
```

See [Mission System documentation](./missions.md#mission-runner-api-port-6207) for full details.

---

## WebSocket

```
ws://localhost:6300/ws/prism
```

Real-time events pushed by PRISM. The Zyrcon iPhone app subscribes on connect.

### Events

| Event | When fired | Payload |
|---|---|---|
| `status_update` | Any operator status change | `{ operator, status, port }` |
| `approval_created` | New approval gate fired | `{ approval_id, action_key, risk_level, mission_id }` |
| `run_started` | Mission run begins | `{ mission_run_id, mission_id, workflow_id }` |
| `run_completed` | Mission run finishes | `{ mission_run_id, mission_id, status }` |
| `item_created` | New mission item written | `{ item_id, mission_id, item_type, urgency_score }` |

### Example subscription (JavaScript)

```javascript
const ws = new WebSocket('ws://localhost:6300/ws/prism');
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.event === 'approval_created') {
    showApprovalNotification(msg.payload);
  }
};
```

---

## Error responses

All errors follow this shape:

```json
{
  "error": "mission_not_found",
  "mission_id": "does_not_exist"
}
```

| HTTP status | Meaning |
|---|---|
| 400 | Bad request — missing or invalid field |
| 404 | Resource not found |
| 409 | Conflict — e.g. mission not installed |
| 403 | Tier not allowed for this trigger type |
| 503 | Downstream service unavailable |
| 500 | Internal error |
