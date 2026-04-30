# Gmail Connector — CON-013

Cascadia OS DEPOT connector for Gmail via the Google Gmail REST API v1.

- **Port:** 9500
- **Auth:** OAuth2 (`access_token` in every payload)
- **NATS subject:** `cascadia.connectors.gmail-connector.>`
- **Response subject:** `cascadia.connectors.gmail-connector.response`

## Actions

| Action | Approval required |
|---|---|
| `send_email` | Yes — routed to `cascadia.approvals.request` |
| `list_messages` | No |
| `get_message` | No |

## Payload examples

### send_email
```json
{
  "action": "send_email",
  "access_token": "<oauth2-access-token>",
  "to": "recipient@example.com",
  "subject": "Hello from Cascadia",
  "body": "This message was sent via the Gmail Connector.",
  "sender": "me"
}
```

### list_messages
```json
{
  "action": "list_messages",
  "access_token": "<oauth2-access-token>",
  "query": "is:unread",
  "max_results": 10
}
```

### get_message
```json
{
  "action": "get_message",
  "access_token": "<oauth2-access-token>",
  "message_id": "18d2f3a4b5c6e7f8"
}
```

## Running

```bash
python connector.py
```

The health endpoint is available at `http://localhost:9500/`.
