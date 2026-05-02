# Mission System

## Overview

Missions are automated business workflows that coordinate multiple operators to complete a business objective end to end. Each mission has a manifest that defines its workflows, required operators, approval gates, and tier limits.

Missions run via the **Mission Manager** on port 6207. The Mission Runner wraps STITCH (port 6201) for workflow sequencing and writes all state to SQLite.

```
Mission Manager (6207)
  ↓
Mission Runner → STITCH (6201) → Operators
  ↓
SQLite (mission_runs, mission_items, approvals)
  ↑
PRISM (6300) — surfaces everything to the iPhone app
```

---

## The Three Missions

### Revenue Desk (`revenue_desk`)

Scans email and attachments for business documents and creates actionable items. Each inbound email is classified by BELL, scored for urgency and dollar value, and written to `mission_items` as a structured record. The Zyrcon iPhone app pulls these items in real time so the business owner can act from anywhere.

**Operators:** SCOUT, RECON, QUOTE, COLLECT, EMAIL
**Triggers:** Inbound email · manual run · daily schedule
**Creates:** `lead` · `quote_request` · `purchase_order` · `invoice` · `overdue_invoice` · `unsold_quote`
**Port:** 6207 (Mission Manager)

**Example — roofing contractor:**
- Customer emails: "Need a quote for 3,000 sq ft re-roof" → `quote_request` created, urgency 20, value 45
- Vendor sends a PO for shingles → `purchase_order` created, urgency 25, value 55
- 30-day-old invoice with no payment → `overdue_invoice` created, urgency 55, value 70

---

### Growth Campaigns (`growth_campaigns`)

Generates and schedules marketing campaigns across email, SMS, and social channels. Turns completed jobs, old leads, and seasonal opportunities into approved campaign drafts. Nothing goes out until a human approves.

**Operators:** SOCIAL, CAMPAIGN, BRIEF, EMAIL
**Triggers:** Manual run · completed job · daily schedule
**Creates:** `campaign_items` · social posts · email drafts
**Port:** 6207 (Mission Manager)

---

### Operations Desk (`operations_desk`)

Reviews business operations and surfaces what needs attention. Produces a daily brief from live operator data — active projects, task backlogs, open risks, and planning gaps.

**Operators:** Aurelia, BRIEF, CHIEF
**Triggers:** Manual run · morning schedule
**Creates:** Daily plans · project reviews · risk flags
**Port:** 6207 (Mission Manager)

---

## Mission Runner API (port 6207)

### List catalog

```
GET /api/missions/catalog
```

Returns all available missions.

```json
{
  "missions": [
    {
      "id": "revenue_desk",
      "type": "mission",
      "name": "Revenue Desk",
      "version": "1.0.0",
      "description": "Email scanning and revenue pipeline",
      "tier_required": "free",
      "installed": true,
      "status": "installed"
    }
  ]
}
```

---

### Get mission detail

```
GET /api/missions/{mission_id}
```

```json
{
  "id": "revenue_desk",
  "name": "Revenue Desk",
  "version": "1.0.0",
  "description": "Email scanning and revenue pipeline",
  "tier_required": "free",
  "installed": true,
  "status": "installed"
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
  "mission_run_id": "a1b2c3d4-...",
  "mission_id": "revenue_desk",
  "workflow_id": "daily_campaign",
  "status": "running"
}
```

If the first workflow step is an external action (email send, quote dispatch), status will be `waiting_approval` instead of `running`.

---

### List all runs

```
GET /api/missions/{mission_id}/runs
```

**Response:**
```json
{
  "mission_id": "revenue_desk",
  "runs": [
    {
      "id": "a1b2c3d4-...",
      "workflow_id": "daily_campaign",
      "status": "completed",
      "trigger_type": "manual",
      "started_at": "2026-05-02T09:14:22Z",
      "completed_at": "2026-05-02T09:14:38Z"
    }
  ]
}
```

---

### Resume a run after approval

```
POST /api/missions/{mission_id}/runs/{run_id}/resume
```

**Request:**
```json
{
  "decision": "approved",
  "approval_id": "appr_abc123",
  "note": "Looks good, send it."
}
```

**Response:**
```json
{
  "mission_run_id": "a1b2c3d4-...",
  "status": "running"
}
```

Set `"decision": "rejected"` to cancel the run.

---

### Get mission items

```
GET /api/missions/{mission_id}/items?status=new&limit=20
```

Returns actionable items surfaced from email scanning and operator runs.

**Response:**
```json
{
  "items": [
    {
      "id": "item_7f3a...",
      "item_type": "quote_request",
      "title": "Re-roof estimate for 4821 Westheimer",
      "description": "Customer requesting quote for full re-roof, approx 3,000 sq ft",
      "customer_name": "John Martinez <jmartinez@example.com>",
      "company_name": "Martinez Properties",
      "amount": null,
      "confidence": 0.91,
      "urgency_score": 20,
      "value_score": 30,
      "recommended_action": "Draft quote reply",
      "status": "new",
      "created_at": "2026-05-02T08:47:11Z"
    },
    {
      "id": "item_9c2b...",
      "item_type": "overdue_invoice",
      "title": "Invoice #1042 — 30 days overdue",
      "description": "Job at 2211 Richmond Ave, invoice sent 2026-04-02, no payment received",
      "customer_name": "Gulf Coast Realty",
      "company_name": "Gulf Coast Realty LLC",
      "amount": 8400.00,
      "confidence": 0.97,
      "urgency_score": 55,
      "value_score": 70,
      "recommended_action": "Call customer immediately",
      "status": "new",
      "created_at": "2026-05-02T07:30:05Z"
    }
  ],
  "total": 2
}
```

**Query parameters:**
| Parameter | Default | Description |
|---|---|---|
| `status` | (all) | Filter by status: `new`, `approved`, `dismissed`, `completed` |
| `limit` | 20 | Max items to return |

---

### Update item status

```
PATCH /api/missions/items/{item_id}
```

**Request:**
```json
{ "status": "approved" }
```

**Valid status values:** `new` · `approved` · `dismissed` · `completed` · `in_progress`

**Response:**
```json
{
  "item_id": "item_7f3a...",
  "status": "approved"
}
```

---

## Approval Flow

Certain operator actions are considered high-risk and are held at an approval gate before dispatch. The mission run transitions to `waiting_approval` status and waits for a human decision.

| Action | Risk level | What triggers it |
|---|---|---|
| `email.send` | High | Outbound email to customer |
| `sms.send` | Medium | SMS to customer or lead |
| `quote.send` | High | Proposal sent to customer |
| `invoice.send` | High | Invoice dispatched |
| `campaign.post` | Medium | Social or email campaign published |
| `crm.write` | Low | CRM record created or updated |
| `payment.request` | High | Payment link or request sent |

Approvals can be acted on via:
- **PRISM dashboard** — `http://localhost:6300`
- **Zyrcon iPhone app** — Approvals tab
- **API** — `POST /api/missions/{id}/runs/{run_id}/resume`

---

## Mission Item Types

| Item type | Label | Triggered by | Recommended action |
|---|---|---|---|
| `lead` | New Lead | Email from new contact expressing interest | Qualify and schedule follow-up |
| `quote_request` | Quote Request | Customer requests pricing or estimate | Draft quote reply |
| `purchase_order` | PO Received | Vendor or customer sends a purchase order | Create job and confirm receipt |
| `invoice` | Invoice Due | Invoice created, awaiting payment | Send invoice reminder |
| `overdue_invoice` | Overdue Invoice | Invoice 30+ days past due | Call customer immediately |
| `unsold_quote` | Unsold Quote | Quote sent, no response in 14+ days | Send reactivation message |

### Urgency scoring

Urgency is scored 0–100. Items scoring above 60 are flagged as "Urgent" in the iPhone app.

| Condition | Points added |
|---|---|
| Item type is `overdue_invoice` | +40 |
| Item type is `purchase_order` | +25 |
| Item type is `quote_request` | +20 |
| `days_waiting` > 2 | +15 |

### Value scoring

Value is scored 0–100 based on dollar amount and item type.

| Condition | Points added |
|---|---|
| Amount > $10,000 | +30 |
| Amount > $1,000 | +15 |
| Item type is `purchase_order` | +25 |

---

## Database tables

| Table | Purpose |
|---|---|
| `mission_runs` | One row per run, tracks status lifecycle |
| `mission_run_steps` | Step-level execution state |
| `mission_items` | Revenue items surfaced from email scanning |
| `approvals` | Approval gate records, linked to runs |
| `missions` | Installed mission registry |

Schema: [`cascadia/missions/schema.sql`](../cascadia/missions/schema.sql)
Migration: [`cascadia/missions/migrate.py`](../cascadia/missions/migrate.py)
