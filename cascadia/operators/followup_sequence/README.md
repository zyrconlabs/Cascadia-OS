# Follow-Up Sequence

**ID:** followup-sequence | **Port:** 8103 | **Tier:** lite | **Category:** sales

Manage automated multi-step follow-up email sequences for leads and customers. Email sending is approval-gated.

## Actions

| Action | Gate | Description |
|---|---|---|
| `create_sequence` | direct | Define a sequence with ordered steps |
| `enroll_contact` | direct | Enroll a contact in a sequence |
| `get_next_step` | direct | Get the next pending step for an enrollment |
| `list_enrollments` | direct | List enrollments, optionally filtered by sequence or status |
| `pause_enrollment` | direct | Pause an active enrollment |
| `resume_enrollment` | direct | Resume a paused enrollment |
| `unenroll_contact` | direct | Mark enrollment as unenrolled |
| `send_step` | approval | Send the current step email — routed through `cascadia.approvals.request` |

## NATS Subjects

- Subscribe: `cascadia.operators.followup-sequence.call`
- Respond: `cascadia.operators.followup-sequence.response`
- Approvals: `cascadia.approvals.request`

## Payload Examples

```json
{
  "action": "create_sequence",
  "name": "New Lead Nurture",
  "steps": [
    {"subject": "Welcome!", "body": "Hi {name}, thanks for reaching out.", "delay_days": 0},
    {"subject": "Following up", "body": "Just checking in...", "delay_days": 3},
    {"subject": "Last touch", "body": "Is there anything I can help with?", "delay_days": 7}
  ]
}
```

```json
{
  "action": "enroll_contact",
  "contact_email": "lead@example.com",
  "contact_name": "Alex Lee",
  "sequence_id": "SEQ-ABCDEF123456"
}
```

## Health

```
GET http://localhost:8103/health
→ {"status":"ok","operator":"followup-sequence","version":"1.0.0","port":8103}
```

## Install

```bash
bash install.sh
python3 operator.py
```
