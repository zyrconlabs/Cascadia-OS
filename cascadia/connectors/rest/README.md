# REST / OpenAPI Connector (CON-108)

**Port:** 9980  
**Tier:** Lite+  
**Category:** Runtime  

Makes authenticated HTTP/REST calls to any external API on behalf of Cascadia operators.

## Features

- Supports bearer token, API key (header or query), HTTP basic auth, HMAC signing
- Automatic retry with exponential backoff on 429/5xx responses (up to 3 retries)
- Approval gate on all write methods (POST, PUT, PATCH, DELETE)
- Response normalized to Cascadia envelope: `{ok, status, data, connector, timestamp}`
- Health endpoint on port 9980

## NATS

**Subscribe:** `cascadia.connectors.rest.>`  
**Publish:** `cascadia.connectors.rest-connector.response`

### Event payload

```json
{
  "method": "GET",
  "url": "https://api.example.com/v1/contacts",
  "headers": {"X-Custom": "value"},
  "params": {"page": "1"},
  "body": {"key": "value"},
  "auth_type": "bearer",
  "credentials": {"token": "sk_live_..."},
  "timeout": 30,
  "retries": 3
}
```

### Auth types

| auth_type | credentials keys |
|---|---|
| `bearer` | `token` |
| `api_key` | `key`, `name` (header name), `location` (`header`/`query`) |
| `basic` | `username`, `password` |
| `hmac` | `secret`, `header` (signature header name) |
| `none` | — |

## Approval gate

Any `POST`, `PUT`, `PATCH`, or `DELETE` request publishes to `cascadia.approvals.request`
before executing. The request does not fire until the approval listener responds.
