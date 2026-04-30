# Telegram Connector (CON-018)

Send messages and receive updates via the Telegram Bot API.

| Property | Value |
|---|---|
| ID | `telegram-connector` |
| Version | 1.0.0 |
| Port | **9000** |
| Auth type | API key (bot token) |
| Tier | lite |
| Category | communication |

## NATS Subject

```
cascadia.connectors.telegram-connector.>
```

Responses are published to:

```
cascadia.connectors.telegram-connector.response
```

All `send_message` actions are gated through `cascadia.approvals.request` before execution.

## Auth

Obtain a bot token from [@BotFather](https://t.me/BotFather) and set it as `bot_token` in every payload.
Format: `123456789:AABBCCDDEEFFaabbccddeeff-1234567890`

## Payload Examples

### send_message

```json
{
  "action": "send_message",
  "bot_token": "123456789:YOUR-BOT-TOKEN",
  "chat_id": "-1001234567890",
  "text": "Hello from Cascadia OS!"
}
```

`chat_id` can be:
- A numeric group/channel ID (e.g. `-1001234567890`)
- A user's numeric ID (e.g. `987654321`)
- A public username (e.g. `@mychannel`)

Response:
```json
{
  "ok": true,
  "message_id": 42,
  "chat_id": -1001234567890
}
```

## Health Check

```
GET http://localhost:9000/
```

```json
{"status": "healthy", "connector": "telegram-connector", "version": "1.0.0", "port": 9000}
```

## Running

```bash
python connector.py
```

Requires `nats-py` for NATS integration (`pip install nats-py`). The health server starts on port 9000 regardless of NATS availability.
