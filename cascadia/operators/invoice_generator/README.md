# Invoice Generator

**ID:** invoice-generator | **Port:** 8101 | **Tier:** lite | **Category:** finance

Generates plain-text professional invoices from structured data and emails them to clients.

## Actions

| Action | Gate | Description |
|---|---|---|
| `generate_invoice` | direct | Build a text invoice; returns invoice text and totals |
| `send_invoice` | approval | Email invoice to client — routed through `cascadia.approvals.request` |
| `save_invoice` | approval | Write invoice to `~/invoices/` — routed through `cascadia.approvals.request` |

## NATS Subjects

- Subscribe: `cascadia.operators.invoice-generator.call`
- Respond: `cascadia.operators.invoice-generator.response`
- Approvals: `cascadia.approvals.request`

## Payload Examples

```json
{
  "action": "generate_invoice",
  "client_name": "Acme Corp",
  "client_email": "billing@acme.com",
  "company_name": "My Agency",
  "currency": "USD",
  "due_days": 30,
  "items": [
    {"description": "Web design", "quantity": 1, "unit_price": 2500.00},
    {"description": "Hosting setup", "quantity": 1, "unit_price": 150.00}
  ]
}
```

## Health

```
GET http://localhost:8101/health
→ {"status":"ok","operator":"invoice-generator","version":"1.0.0","port":8101}
```

## Install

```bash
bash install.sh
python3 operator.py
```
