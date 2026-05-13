# Telegram Orchestration

How a Telegram message becomes a completed operator task and a reply.

## Full message flow

```
1. You send a message in Telegram
       ↓
2. Telegram → Telegram Connector :9000/webhook
   (webhook receive, duplicate detection, envelope build)
       ↓
3. Telegram Connector → VANGUARD :6202/inbound
   (message normalization, channel=telegram, chat_id preserved in raw.metadata)
       ↓
4. VANGUARD fires background thread → CHIEF :6210/task
   (returns 202 immediately so Telegram's 5s timeout is not hit)
       ↓
5. CHIEF: keyword match → CREW :5100/crew (operator list + capabilities)
       ↓
6. CHIEF → BEACON :6200/route
   {sender:chief, message_type:run.execute, target:<operator>, message:{task, chat_id, ...}}
       ↓
7. BEACON capability check → forwards to Operator :<port>/message
       ↓
8. Operator executes, returns result
       ↓
9. CHIEF formats reply_text
       ↓
10. VANGUARD → Telegram Connector :9000/send {chat_id, text:reply_text}
       ↓
11. Telegram message delivered to you
```

## Bot setup

### 1. Create a bot with BotFather

```
/newbot → give it a name → BotFather returns a token
```

### 2. Store the token

The Telegram connector reads the token from the Cascadia vault:

```bash
# Store via vault API (VAULT must be running)
curl -X POST http://127.0.0.1:5101/store \
  -H "Content-Type: application/json" \
  -d '{"key": "telegram.bot_token", "value": "YOUR_TOKEN_HERE"}'
```

Or add to `cascadia-os-operators/telegram/telegram.config.json`:
```json
{
  "mode": "live",
  "bot_token": "YOUR_TOKEN_HERE",
  "bot_username": "your_bot_username"
}
```

### 3. Set the webhook

The connector must be publicly reachable. Register the webhook URL:
```bash
curl -X POST http://127.0.0.1:9000/api/setup_webhook \
  -H "Content-Type: application/json" \
  -d '{"webhook_url": "https://your.domain.com/webhook"}'
```

Telegram requires HTTPS. Use ngrok for local development:
```bash
ngrok http 9000
# then register: https://<ngrok-id>.ngrok.io/webhook
```

### 4. Find your chat_id

Send any message to your bot, then:
```bash
curl -s https://api.telegram.org/bot<TOKEN>/getUpdates \
  | python3 -m json.tool | grep '"id"'
```

The `id` under `chat` is your `chat_id`.

## Simulated mode (no real bot)

Set `"mode": "simulated"` in `telegram.config.json`. The connector logs sends instead of calling Telegram. Simulate inbound via curl:

```bash
curl -X POST http://127.0.0.1:9000/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "update_id": 10001,
    "message": {
      "message_id": 1,
      "from": {"id": 123456, "first_name": "Andy", "username": "andy"},
      "chat": {"id": 123456, "type": "private"},
      "text": "Draft a proposal for warehouse mezzanine installation"
    }
  }'
```

Check replies:
```bash
curl -s http://127.0.0.1:9000/api/messages | python3 -m json.tool
```

## Sending a task (direct to CHIEF)

Skip Telegram entirely and call CHIEF directly:
```bash
curl -X POST http://127.0.0.1:6210/task \
  -H "Content-Type: application/json" \
  -d '{
    "task": "Find leads for HVAC contractors in Houston",
    "source_channel": "api",
    "sender": "andy"
  }'
```

## Status commands

Send these in Telegram:
- `/status` — health of all core components
- `/operators` — list registered workers and their capabilities
- `/missions` — last 5 mission runs
- `/help` — command list and task examples

## v1 known limitations

| Limitation | Impact |
|------------|--------|
| No polling fallback (getUpdates) | Bot needs public HTTPS for webhook |
| Synchronous dispatch | Long tasks (>60s) return a timeout message |
| No durable task tracking | Cannot check status of a running task via Telegram |
| Keyword-only selection | Ambiguous tasks may pick the wrong operator |
| Single ack message | User sees ⏳ then final result only |
