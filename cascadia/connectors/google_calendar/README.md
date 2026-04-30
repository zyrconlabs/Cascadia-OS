# Google Calendar Connector (CON-015)

Read and create Google Calendar events via the Google Calendar API v3.

| Property | Value |
|---|---|
| ID | `google-calendar-connector` |
| Version | 1.0.0 |
| Port | **9502** |
| Auth type | OAuth2 Bearer token |
| Tier | lite |
| Category | productivity |

## NATS Subject

```
cascadia.connectors.google-calendar-connector.>
```

Responses are published to:

```
cascadia.connectors.google-calendar-connector.response
```

All `create_event` actions are gated through `cascadia.approvals.request` before execution.

## Auth

Set the `access_token` field in every payload to a valid OAuth2 Bearer token scoped for the Google Calendar API.

Recommended OAuth2 scopes:
- `https://www.googleapis.com/auth/calendar.readonly` — list and get events
- `https://www.googleapis.com/auth/calendar.events` — create events

## Payload Examples

### list_events

```json
{
  "action": "list_events",
  "access_token": "ya29.YOUR_TOKEN",
  "calendar_id": "primary",
  "time_min": "2026-04-30T00:00:00Z",
  "time_max": "2026-05-07T00:00:00Z",
  "max_results": 10
}
```

Response:
```json
{
  "ok": true,
  "events": [
    {
      "id": "abc123",
      "summary": "Team Standup",
      "start": {"dateTime": "2026-05-01T09:00:00Z"},
      "end": {"dateTime": "2026-05-01T09:30:00Z"},
      "location": "",
      "status": "confirmed"
    }
  ]
}
```

### create_event

Publishes an approval request to `cascadia.approvals.request` — does **not** execute immediately.

```json
{
  "action": "create_event",
  "access_token": "ya29.YOUR_TOKEN",
  "calendar_id": "primary",
  "summary": "Quarterly Review",
  "start": "2026-05-10T14:00:00Z",
  "end": "2026-05-10T15:00:00Z",
  "description": "Q2 review meeting",
  "location": "Conf Room A",
  "attendees": ["alice@example.com", "bob@example.com"]
}
```

Response (after approval):
```json
{
  "ok": true,
  "event_id": "xyz789",
  "html_link": "https://www.google.com/calendar/event?eid=..."
}
```

### get_event

```json
{
  "action": "get_event",
  "access_token": "ya29.YOUR_TOKEN",
  "calendar_id": "primary",
  "event_id": "abc123"
}
```

Response:
```json
{
  "ok": true,
  "id": "abc123",
  "summary": "Team Standup",
  "start": {"dateTime": "2026-05-01T09:00:00Z"},
  "end": {"dateTime": "2026-05-01T09:30:00Z"}
}
```

## Health Check

```
GET http://localhost:9502/
```

```json
{"status": "healthy", "connector": "google-calendar-connector", "version": "1.0.0", "port": 9502}
```

## Running

```bash
python connector.py
```

Requires `nats-py` for NATS integration (`pip install nats-py`). The health server starts on port 9502 regardless of NATS availability.
