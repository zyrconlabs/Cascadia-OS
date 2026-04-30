# Discord Connector (CON-020)

Send messages to Discord channels via a bot token.

| Property | Value |
|---|---|
| ID | `discord-connector` |
| Version | 1.0.0 |
| Port | **9004** |
| Auth type | Bearer (bot token) |
| Tier | lite |
| Category | communication |

## NATS Subject

```
cascadia.connectors.discord-connector.>
```

Responses are published to:

```
cascadia.connectors.discord-connector.response
```

All `send_message` actions are gated through `cascadia.approvals.request` before execution.

## Auth

Set the `token` field to your Discord bot token (without the `Bot ` prefix — the connector adds it internally).

The bot must be added to the target server with the `Send Messages` permission in the target channel.

## Getting a Bot Token

1. Go to https://discord.com/developers/applications and create an application.
2. Under **Bot**, click **Reset Token** to get the token.
3. Under **OAuth2 > URL Generator**, select the `bot` scope and `Send Messages` permission, then invite the bot to your server.

## Payload Examples

### send_message

```json
{
  "action": "send_message",
  "token": "YOUR.BOT.TOKEN",
  "channel_id": "1234567890123456789",
  "content": "Hello from Cascadia OS!"
}
```

`channel_id` is the numeric snowflake ID of the Discord channel. Enable Developer Mode in Discord settings, then right-click a channel and select "Copy Channel ID".

Response:
```json
{
  "ok": true,
  "message_id": "9876543210987654321",
  "channel_id": "1234567890123456789"
}
```

## Health Check

```
GET http://localhost:9004/
```

```json
{"status": "healthy", "connector": "discord-connector", "version": "1.0.0", "port": 9004}
```

## Running

```bash
python connector.py
```

Requires `nats-py` for NATS integration (`pip install nats-py`). The health server starts on port 9004 regardless of NATS availability.
