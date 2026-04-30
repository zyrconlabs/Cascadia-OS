# Email Connector — B2

Cascadia OS DEPOT connector for SMTP/IMAP email without cloud account lock-in.

- **Port:** 9010
- **Auth:** `smtp_credentials` — `smtp_host`, `smtp_port`, `username`, `password` included in each payload
- **NATS subject:** `cascadia.connectors.email-connector.>`
- **Response subject:** `cascadia.connectors.email-connector.response`

## Actions

| Action | Approval required |
|---|---|
| `send_email` | Yes — routed to `cascadia.approvals.request` |
| `list_inbox` | No |
| `get_message` | No |
| `search_messages` | No |

## Payload examples

### send_email
```json
{
  "action": "send_email",
  "smtp_host": "smtp.example.com",
  "smtp_port": 587,
  "username": "user@example.com",
  "password": "secret",
  "to": "recipient@example.com",
  "subject": "Hello from Cascadia",
  "body": "This message was sent via the Email Connector.",
  "use_tls": true
}
```

### list_inbox
```json
{
  "action": "list_inbox",
  "imap_host": "imap.example.com",
  "username": "user@example.com",
  "password": "secret",
  "folder": "INBOX",
  "limit": 10
}
```

### get_message
```json
{
  "action": "get_message",
  "imap_host": "imap.example.com",
  "username": "user@example.com",
  "password": "secret",
  "msg_id": "42"
}
```

### search_messages
```json
{
  "action": "search_messages",
  "imap_host": "imap.example.com",
  "username": "user@example.com",
  "password": "secret",
  "criteria": "UNSEEN"
}
```

## Health

```
GET http://localhost:9010/health
→ {"status": "ok", "connector": "email-connector"}
```

## Running

```bash
python connector.py
```
