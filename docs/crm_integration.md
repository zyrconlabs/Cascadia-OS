# CRM Integration Guide

Connect Cascadia OS to your CRM so every lead, proposal, and deal outcome is logged automatically — no manual data entry.

---

## What the CRM Operator Does

The CRM operator runs quietly in the background. When Cascadia OS completes a workflow — qualifying a lead, sending a proposal, following up on an invoice — it automatically:

1. **Logs the activity** in your CRM (call, email, or note)
2. **Updates the deal stage** when a proposal is approved
3. **Creates new contacts** from lead capture runs
4. **Records win/loss outcomes** when you mark a deal closed

---

## Supported CRMs

| CRM | Setup Time | Notes |
|-----|-----------|-------|
| HubSpot | 2 minutes | Free tier works |
| Pipedrive | 2 minutes | Requires API key |
| CSV export | Instant | No account needed |

---

## Setup: HubSpot

1. Log into HubSpot → Settings → Integrations → API Keys
2. Click **Create private app** → name it "Cascadia OS"
3. Under Scopes, enable: `crm.objects.contacts.write`, `crm.objects.deals.write`
4. Copy the API key
5. In Cascadia OS config.json, add:

```json
{
  "crm_provider": "hubspot",
  "crm_api_key": "your_key_here",
  "crm_pipeline_id": "default"
}
```

6. Restart Cascadia OS — the CRM operator auto-connects.

---

## Setup: Pipedrive

1. Log into Pipedrive → Settings → Personal preferences → API
2. Copy your **Personal API token**
3. In config.json:

```json
{
  "crm_provider": "pipedrive",
  "crm_api_key": "your_token_here",
  "crm_pipeline_id": "1"
}
```

---

## Setup: CSV (No CRM account)

No setup needed. The CRM operator writes a `contacts.csv` file to `data/crm/` automatically. Import it into any spreadsheet or CRM at any time.

---

## What Gets Logged Automatically

| Cascadia Event | CRM Action |
|----------------|-----------|
| Lead captured by SCOUT | New contact created |
| Proposal sent by QUOTE | Activity logged + deal stage → "Proposal Sent" |
| Approval clicked in dashboard | Deal stage → "Negotiation" |
| Win/Loss marked in PRISM | Deal closed won/lost |
| Follow-up email sent | Activity logged |

---

## Checking the Connection

Open PRISM (http://localhost:6300) and look for the CRM operator in the sidebar. A green dot means it's connected. Click it to see the last activity logged.

---

## Troubleshooting

**CRM operator shows offline:** Check that port 8012 is not in use by another application. Restart with `bash start.sh`.

**Activities not appearing in HubSpot:** Verify your private app has the correct scopes. Regenerate the API key if needed.

**Duplicate contacts:** Cascadia uses email address as the unique key. If you have duplicate emails in your existing CRM, the operator will update the existing contact rather than creating a new one.

---

## Privacy

All CRM data is sent directly from your machine to your CRM provider's API. Nothing passes through Zyrcon servers. Your customer data never leaves your control.
