# Outlook Connector — CON-014

Cascadia OS DEPOT connector for Outlook / Microsoft 365 via the Microsoft Graph API.

- **Port:** 9501
- **Auth:** OAuth2 (`access_token` in every payload)
- **NATS subject:** `cascadia.connectors.outlook-connector.>`
- **Response subject:** `cascadia.connectors.outlook-connector.response`

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
  "body": "This message was sent via the Outlook Connector."
}
```

### list_messages
```json
{
  "action": "list_messages",
  "access_token": "<oauth2-access-token>",
  "filter_query": "isRead eq false",
  "top": 10
}
```

### get_message
```json
{
  "action": "get_message",
  "access_token": "<oauth2-access-token>",
  "message_id": "AAMkADExAmPl3..."
}
```

## Running

```bash
python connector.py
```

The health endpoint is available at `http://localhost:9501/`.
