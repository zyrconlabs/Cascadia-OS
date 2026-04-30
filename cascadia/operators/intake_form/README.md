# Intake Form Processor

**ID:** intake-form | **Port:** 8105 | **Tier:** lite | **Category:** operations

Process incoming form submissions, validate fields, and route to the right operator. Routing is approval-gated.

## Actions

| Action | Gate | Description |
|---|---|---|
| `define_form` | direct | Define a form schema with typed, required/optional fields |
| `list_forms` | direct | List all defined forms |
| `submit_form` | direct | Validate and record a form submission |
| `list_submissions` | direct | List submissions, optionally filtered by form |
| `get_submission` | direct | Retrieve a single submission by ID |
| `route_submission` | approval | Route a submission to another operator via `cascadia.approvals.request` |

## Field Types

`text`, `email`, `phone`, `number`

## NATS Subjects

- Subscribe: `cascadia.operators.intake-form.call`
- Respond: `cascadia.operators.intake-form.response`
- Approvals: `cascadia.approvals.request`

## Payload Examples

```json
{
  "action": "define_form",
  "form_id": "contact-us",
  "name": "Contact Us",
  "fields": [
    {"name": "full_name", "required": true, "type": "text"},
    {"name": "email", "required": true, "type": "email"},
    {"name": "phone", "required": false, "type": "phone"},
    {"name": "message", "required": true, "type": "text"}
  ]
}
```

```json
{
  "action": "submit_form",
  "form_id": "contact-us",
  "data": {
    "full_name": "Jordan Kim",
    "email": "jordan@example.com",
    "message": "I would like to learn more."
  }
}
```

```json
{
  "action": "route_submission",
  "submission_id": "SUB-ABCDEF123456",
  "target_operator": "appointment-scheduler",
  "target_action": "create_appointment"
}
```

## Health

```
GET http://localhost:8105/health
→ {"status":"ok","operator":"intake-form","version":"1.0.0","port":8105}
```

## Install

```bash
bash install.sh
python3 operator.py
```
