# Microsoft Teams Connector (CON-016)

Send messages to Microsoft Teams channels and chats via the Microsoft Graph API.

| Property | Value |
|---|---|
| ID | `teams-connector` |
| Version | 1.0.0 |
| Port | **9503** |
| Auth type | OAuth2 Bearer token |
| Tier | lite |
| Category | communication |

## NATS Subject

```
cascadia.connectors.teams-connector.>
```

Responses are published to:

```
cascadia.connectors.teams-connector.response
```

All `send_channel_message` and `send_chat_message` actions are gated through `cascadia.approvals.request` before execution.

## Auth

Set the `access_token` field in every payload to a valid OAuth2 Bearer token issued by Microsoft identity platform (Entra ID / Azure AD).

Required Microsoft Graph API delegated or application permissions:
- `ChannelMessage.Send` — send messages to channels
- `ChatMessage.Send` — send messages to chats
- `Channel.ReadBasic.All` — list channels

## Payload Examples

### send_channel_message

Publishes an approval request to `cascadia.approvals.request` — does **not** execute immediately.

```json
{
  "action": "send_channel_message",
  "access_token": "eyJ0eXAiOi...",
  "team_id": "19:abc123...",
  "channel_id": "19:xyz456...",
  "content": "Hello from Cascadia OS!"
}
```

Response (after approval):
```json
{
  "ok": true,
  "message_id": "1234567890123"
}
```

### send_chat_message

Publishes an approval request to `cascadia.approvals.request` — does **not** execute immediately.

```json
{
  "action": "send_chat_message",
  "access_token": "eyJ0eXAiOi...",
  "chat_id": "19:abc123...@thread.v2",
  "content": "Quick update from Cascadia OS."
}
```

Response (after approval):
```json
{
  "ok": true,
  "message_id": "1234567890456"
}
```

### list_channels

```json
{
  "action": "list_channels",
  "access_token": "eyJ0eXAiOi...",
  "team_id": "19:abc123..."
}
```

Response:
```json
{
  "ok": true,
  "channels": [
    {"id": "19:xyz456...", "displayName": "General"},
    {"id": "19:def789...", "displayName": "Engineering"}
  ]
}
```

## Health Check

```
GET http://localhost:9503/
```

```json
{"status": "healthy", "connector": "teams-connector", "version": "1.0.0", "port": 9503}
```

## Running

```bash
python connector.py
```

Requires `nats-py` for NATS integration (`pip install nats-py`). The health server starts on port 9503 regardless of NATS availability.
