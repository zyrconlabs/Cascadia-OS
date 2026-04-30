# Slack Connector (CON-017)

Send messages and receive events from Slack channels and DMs via bot token or OAuth2.

| Property | Value |
|---|---|
| ID | `slack-connector` |
| Version | 1.0.0 |
| Port | **9003** |
| Auth type | Bearer (bot token) |
| Tier | lite |
| Category | communication |

## NATS Subject

```
cascadia.connectors.slack-connector.>
```

Responses are published to:

```
cascadia.connectors.slack-connector.response
```

All `send_message` actions are gated through `cascadia.approvals.request` before execution.

## Auth

Set the `token` field in every payload to your Slack bot token (`xoxb-…`).

The token must have the following OAuth scopes:
- `chat:write` — send messages
- `channels:read` — list channels
- `users:read` — get user info
- `users:read.email` — read email from user profile

## Payload Examples

### send_message

```json
{
  "action": "send_message",
  "token": "xoxb-YOUR-BOT-TOKEN",
  "channel": "C0123456789",
  "text": "Hello from Cascadia OS!"
}
```

Response:
```json
{
  "ok": true,
  "ts": "1234567890.123456",
  "channel": "C0123456789"
}
```

### list_channels

```json
{
  "action": "list_channels",
  "token": "xoxb-YOUR-BOT-TOKEN",
  "limit": 100
}
```

### get_user

```json
{
  "action": "get_user",
  "token": "xoxb-YOUR-BOT-TOKEN",
  "user_id": "U0123456789"
}
```

## Health Check

```
GET http://localhost:9003/
```

```json
{"status": "healthy", "connector": "slack-connector", "version": "1.0.0", "port": 9003}
```

## Running

```bash
python connector.py
```

Requires `nats-py` for NATS integration (`pip install nats-py`). The health server starts on port 9003 regardless of NATS availability.
