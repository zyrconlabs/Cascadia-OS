# Appointment Scheduler

**ID:** appointment-scheduler | **Port:** 8102 | **Tier:** lite | **Category:** operations

Schedule, reschedule, and cancel appointments. Confirmation and reminder emails are approval-gated.

## Actions

| Action | Gate | Description |
|---|---|---|
| `create_appointment` | direct | Create a new appointment; returns appointment data with generated ID |
| `list_appointments` | direct | Filter by `date` and/or `status` |
| `cancel_appointment` | direct | Mark appointment as cancelled |
| `reschedule_appointment` | direct | Update date/time |
| `send_confirmation` | approval | Email confirmation to client |
| `send_reminder` | approval | Email reminder to client |

## NATS Subjects

- Subscribe: `cascadia.operators.appointment-scheduler.call`
- Respond: `cascadia.operators.appointment-scheduler.response`
- Approvals: `cascadia.approvals.request`

## Payload Examples

```json
{
  "action": "create_appointment",
  "client_name": "Jane Smith",
  "client_email": "jane@example.com",
  "date": "2026-05-15",
  "time": "10:00",
  "duration_minutes": 60,
  "notes": "Initial consultation"
}
```

```json
{
  "action": "list_appointments",
  "date": "2026-05-15",
  "status": "scheduled"
}
```

## Health

```
GET http://localhost:8102/health
→ {"status":"ok","operator":"appointment-scheduler","version":"1.0.0","port":8102}
```

## Install

```bash
bash install.sh
python3 operator.py
```
