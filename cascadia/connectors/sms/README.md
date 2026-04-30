# SMS / Twilio Connector (CON-021)

Send SMS messages via the Twilio Programmable Messaging API.

| Property | Value |
|---|---|
| ID | `sms-connector` |
| Version | 1.0.0 |
| Port | **9002** |
| Auth type | Basic (account_sid + auth_token) |
| Tier | lite |
| Category | communication |

## NATS Subject

```
cascadia.connectors.sms-connector.>
```

Responses are published to:

```
cascadia.connectors.sms-connector.response
```

**SMS dispatch ALWAYS requires human approval.** Every `send_sms` action is routed to `cascadia.approvals.request` before execution — the message is never sent until approval is granted.

## Auth

Pass `account_sid` and `auth_token` directly in each payload. These map to HTTP Basic auth credentials sent to the Twilio API.

## Payload Examples

### send_sms

```json
{
  "action": "send_sms",
  "account_sid": "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "auth_token": "your_auth_token_here",
  "from_number": "+15551234567",
  "to_number": "+15559876543",
  "body": "Hello from Cascadia OS!"
}
```

Both `from_number` and `to_number` must be in E.164 format (e.g. `+15551234567`).  
`from_number` must be a Twilio-provisioned number or a verified Caller ID.

Response (after approval):
```json
{
  "ok": true,
  "sid": "SMxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "status": "queued",
  "to": "+15559876543",
  "from_number": "+15551234567"
}
```

## Approval Flow

1. Cascadia OS publishes the payload to `cascadia.approvals.request`.
2. The approval system notifies the designated approver.
3. On approval, the approved payload is re-published to the connector's execute path.
4. The connector sends the SMS and publishes the result to `cascadia.connectors.sms-connector.response`.

## Health Check

```
GET http://localhost:9002/
```

```json
{"status": "healthy", "connector": "sms-connector", "version": "1.0.0", "port": 9002}
```

## Running

```bash
python connector.py
```

Requires `nats-py` for NATS integration (`pip install nats-py`). The health server starts on port 9002 regardless of NATS availability.
