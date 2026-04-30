# WhatsApp Business Connector (CON-019)

Send messages via the WhatsApp Business Cloud API (Meta Graph API v18.0).

| Property | Value |
|---|---|
| ID | `whatsapp-connector` |
| Version | 1.0.0 |
| Port | **9001** |
| Auth type | Bearer (Meta system user access token) |
| Tier | lite |
| Category | communication |

## NATS Subject

```
cascadia.connectors.whatsapp-connector.>
```

Responses are published to:

```
cascadia.connectors.whatsapp-connector.response
```

All `send_message` actions are gated through `cascadia.approvals.request` before execution.

## Auth

Set the `token` field to a Meta system user access token with `whatsapp_business_messaging` permission.

## Prerequisites

1. A Meta Business account with WhatsApp Business Cloud API access.
2. A registered phone number — you need its **Phone Number ID** (not the number itself).
3. Recipients must have opted-in or be within a 24-hour messaging window (template messages bypass this).

## Payload Examples

### send_message

```json
{
  "action": "send_message",
  "token": "EAAxxxxYOUR_ACCESS_TOKEN",
  "phone_number_id": "123456789012345",
  "to": "15551234567",
  "text": "Hello from Cascadia OS!"
}
```

`to` must be in E.164 format without the leading `+` (e.g. `15551234567` for +1 555 123 4567).

Response:
```json
{
  "ok": true,
  "message_id": "wamid.HBgNMTU1NTEyMzQ1Njc4FQIAERgSMjlBMzBCNTExQ0IzNjg2MzAzAA==",
  "to": "15551234567"
}
```

## Health Check

```
GET http://localhost:9001/
```

```json
{"status": "healthy", "connector": "whatsapp-connector", "version": "1.0.0", "port": 9001}
```

## Running

```bash
python connector.py
```

Requires `nats-py` for NATS integration (`pip install nats-py`). The health server starts on port 9001 regardless of NATS availability.
