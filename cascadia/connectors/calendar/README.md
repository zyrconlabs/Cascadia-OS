# Calendar Connector — B3

Cascadia OS DEPOT unified calendar connector. Supports Google Calendar (API v3),
Microsoft Outlook (Graph API), and read-only iCal feeds — all from a single
connector with a consistent payload schema.

- **Port:** 9031
- **Auth:** `oauth2` — `access_token` inside `credentials` object (or `ical_url` for iCal)
- **NATS subject:** `cascadia.connectors.calendar-connector.>`
- **Response subject:** `cascadia.connectors.calendar-connector.response`

## Providers

| `provider` value | Backend |
|---|---|
| `google` | Google Calendar API v3 |
| `outlook` | Microsoft Graph API `/me/events` |
| `ical` | Public iCal URL (read-only) |

## Actions

| Action | Approval required |
|---|---|
| `list_events` | No |
| `get_event` | No |
| `create_event` | Yes — routed to `cascadia.approvals.request` |
| `update_event` | Yes — routed to `cascadia.approvals.request` |
| `delete_event` | Yes — routed to `cascadia.approvals.request` |

## Payload examples

### list_events (Google)
```json
{
  "action": "list_events",
  "provider": "google",
  "credentials": {"access_token": "<token>"},
  "calendar_id": "primary",
  "time_min": "2026-04-30T00:00:00Z",
  "time_max": "2026-05-31T23:59:59Z",
  "max_results": 10
}
```

### list_events (iCal)
```json
{
  "action": "list_events",
  "provider": "ical",
  "credentials": {"ical_url": "https://example.com/calendar.ics"},
  "calendar_id": "me",
  "max_results": 20
}
```

### create_event (Outlook)
```json
{
  "action": "create_event",
  "provider": "outlook",
  "credentials": {"access_token": "<token>"},
  "calendar_id": "me",
  "summary": "Team Sync",
  "start": "2026-05-01T10:00:00",
  "end": "2026-05-01T11:00:00",
  "description": "Weekly team sync meeting"
}
```

### update_event
```json
{
  "action": "update_event",
  "provider": "google",
  "credentials": {"access_token": "<token>"},
  "calendar_id": "primary",
  "event_id": "abc123",
  "updates": {"summary": "Renamed Event"}
}
```

### delete_event
```json
{
  "action": "delete_event",
  "provider": "google",
  "credentials": {"access_token": "<token>"},
  "calendar_id": "primary",
  "event_id": "abc123"
}
```

## Health

```
GET http://localhost:9031/health
→ {"status": "ok", "connector": "calendar-connector"}
```

## Running

```bash
python connector.py
```
