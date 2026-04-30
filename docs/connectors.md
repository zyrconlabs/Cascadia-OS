# Connector Development Guide

Connectors are lightweight bridge services that link Cascadia OS to external
platforms (Telegram, Slack, SMS, etc.). This guide covers everything needed to
build, test, and publish a connector to the DEPOT marketplace.

---

## Section 1: What is a Connector

### Connectors vs Operators

| | Connector | Operator |
|---|---|---|
| **Purpose** | Bridge to an external system | Business logic / automation |
| **Direction** | Inbound webhooks **or** outbound API calls | Triggered by workflows |
| **Approval** | Outbound sends require approval | Side effects require approval |
| **Typical port range** | 9000–9099 | 7000–7999 |

A connector handles the *transport layer*. An operator handles the *intent layer*.
For example: the Telegram connector knows how to deliver a message to Telegram;
the Aurelia operator decides *what* to send and *when*.

### The Two Connector Patterns

**Inbound webhook** — the external service calls your connector:
```
Telegram → POST /webhook → connector.py → NATS publish
```

**Outbound API** — your connector calls the external service:
```
NATS subscribe → connector.py → POST api.telegram.org
```

Most connectors implement both.

### Authentication Patterns

| `auth_type` | How credentials are stored | Example platforms |
|---|---|---|
| `bot_token` | Single token in env var | Telegram, Slack, Discord |
| `api_key` | Account SID + secret in env vars | Twilio SMS |
| `oauth2` | Client ID + secret; token refresh | WhatsApp, Google |
| `hmac` | Shared secret for webhook signatures | Stripe, GitHub |

---

## Section 2: Quick Start

### Minimum Viable Connector

```python
#!/usr/bin/env python3
"""Minimal Cascadia OS connector — replace PLATFORM with your service name."""
import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import nats

PLATFORM = "myplatform"
PORT = 9099
NATS_URL = os.getenv("NATS_URL", "nats://127.0.0.1:4222")


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "healthy", "connector": PLATFORM, "port": PORT})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(n)) if n else {}
        if self.path == "/webhook":
            # Publish inbound event to NATS
            asyncio.run(publish_inbound(payload))
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "unknown path"})

    def log_message(self, *_):
        pass


async def publish_inbound(payload: dict) -> None:
    nc = await nats.connect(NATS_URL)
    subject = f"cascadia.connectors.{PLATFORM}.inbound"
    await nc.publish(subject, json.dumps(payload).encode())
    await nc.drain()


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"{PLATFORM} connector on :{PORT}")
    server.serve_forever()
```

### Registering with DEPOT

Add your connector to `cascadia/connectors/registry.json`:

```json
{
  "id": "myplatform",
  "name": "My Platform Connector",
  "type": "connector",
  "port": 9099,
  "health_path": "/health",
  "status": "production"
}
```

### Local Development Setup

```bash
# 1. Start NATS
nats-server &

# 2. Set credentials
export MYPLATFORM_TOKEN=test_token

# 3. Run the connector
python cascadia/connectors/myplatform/connector.py

# 4. Verify health
curl http://localhost:9099/health

# 5. Test inbound webhook
curl -X POST http://localhost:9099/webhook \
  -H "Content-Type: application/json" \
  -d '{"text": "hello", "from": "user123"}'
```

---

## Section 3: Manifest Reference

### Connector-Specific Fields

```json
{
  "id": "myplatform",
  "name": "My Platform Connector",
  "type": "connector",
  "version": "1.0.0",
  "description": "One sentence description.",
  "author": "Your Name or Company",
  "price": 0,
  "tier_required": "lite",
  "port": 9099,
  "source_path": "cascadia/connectors/myplatform/",
  "entry_point": "connector.py",
  "install_hook": "install.sh",
  "uninstall_hook": "uninstall.sh",
  "category": "communication",
  "industries": ["general"],
  "installed_by_default": false,
  "safe_to_uninstall": true,
  "approval_required_for_writes": true,
  "nats_subjects": ["cascadia.connectors.myplatform.>"],
  "auth_type": "bot_token",
  "setup_required": true,
  "setup_instructions": "Set MYPLATFORM_TOKEN env var."
}
```

### Field Reference

| Field | Type | Values | Notes |
|---|---|---|---|
| `id` | string | lowercase, hyphens | Must be unique across DEPOT |
| `type` | string | `"connector"` | Always `"connector"` for connectors |
| `tier_required` | string | `"lite"`, `"pro"`, `"enterprise"` | Minimum tier to install |
| `port` | integer | 9000–9099 | Connector port range |
| `auth_type` | string | `"bot_token"`, `"api_key"`, `"oauth2"`, `"hmac"` | See Section 4 |
| `approval_required_for_writes` | boolean | `true` / `false` | Should be `true` for outbound sends |
| `safe_to_uninstall` | boolean | `true` / `false` | `false` if other operators depend on it |
| `setup_required` | boolean | `true` / `false` | Whether credentials must be configured |

### `auth_type` Values

| Value | What it means |
|---|---|
| `bot_token` | Single long-lived token from the platform's developer console |
| `api_key` | Account identifier + secret pair (e.g. Twilio SID + auth token) |
| `oauth2` | Standard OAuth2 flow; connector handles token refresh |
| `hmac` | Shared signing secret used to verify incoming webhook signatures |

### `setup_instructions` Format

Write one to three sentences in plain English. Include the name of each
environment variable the user must set. Example:

```
"Create a bot at discord.com/developers. Set DISCORD_BOT_TOKEN to the bot's token."
```

---

## Section 4: Authentication Handling

### Never Hardcode Credentials

Always read credentials from environment variables:

```python
import os

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]  # raises KeyError if missing — good
```

Or use the Cascadia keystore for at-rest encryption:

```python
from cascadia.vault import get_secret

token = get_secret("telegram.bot_token")
```

### OAuth2 Flow Implementation

```python
import os
import json
import urllib.request
import urllib.parse

CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
REDIRECT_URI = "http://localhost:9020/oauth2/callback"
TOKEN_FILE = os.path.expanduser("~/.cascadia/google_token.json")


def get_auth_url(scopes: list[str]) -> str:
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
    }
    return "https://accounts.google.com/o/oauth2/auth?" + urllib.parse.urlencode(params)


def exchange_code(code: str) -> dict:
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        token = json.loads(resp.read())
    with open(TOKEN_FILE, "w") as f:
        json.dump(token, f)
    return token
```

### API Key Storage Pattern

```python
import os

# Read at startup — fail fast if missing
TWILIO_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM = os.environ["TWILIO_FROM_NUMBER"]
```

### Webhook Signature Verification (HMAC-SHA256)

```python
import hashlib
import hmac
import os

SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"].encode()


def verify_slack_signature(body: bytes, timestamp: str, signature: str) -> bool:
    base = f"v0:{timestamp}:".encode() + body
    expected = "v0=" + hmac.new(SIGNING_SECRET, base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

---

## Section 5: Inbound Webhook Pattern

```python
import hashlib
import hmac
import json
import os
import asyncio
import nats

SIGNING_SECRET = os.environ.get("PLATFORM_SIGNING_SECRET", "").encode()
NATS_URL = os.getenv("NATS_URL", "nats://127.0.0.1:4222")


def handle_webhook(self):
    body = self.rfile.read(int(self.headers.get("Content-Length", 0)))

    # 1. Verify signature
    sig = self.headers.get("X-Platform-Signature", "")
    expected = hmac.new(SIGNING_SECRET, body, hashlib.sha256).hexdigest()
    if SIGNING_SECRET and not hmac.compare_digest(expected, sig):
        self._json(401, {"error": "invalid signature"})
        return

    # 2. Parse payload
    payload = json.loads(body)

    # 3. Normalize into Cascadia event envelope
    event = {
        "connector": "myplatform",
        "event_type": payload.get("type", "message"),
        "from": payload.get("sender_id"),
        "text": payload.get("text", ""),
        "raw": payload,
    }

    # 4. Publish to NATS
    asyncio.run(publish(f"cascadia.connectors.myplatform.inbound", event))

    self._json(200, {"ok": True})


async def publish(subject: str, event: dict) -> None:
    nc = await nats.connect(NATS_URL)
    await nc.publish(subject, json.dumps(event).encode())
    await nc.drain()
```

---

## Section 6: Outbound API Pattern

```python
import json
import time
import urllib.error
import urllib.request


def send_with_retry(url: str, payload: dict, headers: dict,
                    max_retries: int = 3) -> dict:
    """POST to external API with exponential backoff."""
    data = json.dumps(payload).encode()
    delay = 1.0

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:  # rate limited
                retry_after = int(e.headers.get("Retry-After", delay))
                time.sleep(retry_after)
                delay *= 2
                continue
            if e.code >= 500:  # server error — retry
                time.sleep(delay)
                delay *= 2
                continue
            raise  # 4xx client error — don't retry
        except urllib.error.URLError:
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise

    raise RuntimeError(f"Failed after {max_retries} attempts")
```

### Error Handling

```python
try:
    result = send_with_retry(api_url, payload, auth_headers)
except urllib.error.HTTPError as e:
    # Log and publish failure event to NATS
    asyncio.run(publish(
        f"cascadia.connectors.myplatform.error",
        {"error": str(e), "code": e.code, "payload": payload}
    ))
```

---

## Section 7: NATS Event Schema

### Subject Naming

```
cascadia.connectors.[connector_id].[event_type]
```

Examples:
- `cascadia.connectors.telegram.inbound`
- `cascadia.connectors.telegram.send`
- `cascadia.connectors.telegram.error`
- `cascadia.connectors.telegram.status`

### Normalized Event Envelope

All events published to NATS should use this envelope:

```json
{
  "connector": "telegram",
  "event_type": "message",
  "timestamp": "2026-04-30T12:00:00Z",
  "message_id": "msg_abc123",
  "from": "user_id_or_phone",
  "to": "channel_or_recipient",
  "text": "The message text",
  "media": null,
  "raw": { "...original platform payload..." }
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `connector` | string | yes | Connector ID from manifest |
| `event_type` | string | yes | `message`, `reaction`, `status`, `error` |
| `timestamp` | ISO 8601 string | yes | UTC |
| `message_id` | string | yes | Platform-provided ID for deduplication |
| `from` | string | yes | Sender identifier |
| `to` | string | no | Recipient (for outbound) |
| `text` | string | no | Plain text content |
| `raw` | object | yes | Unmodified original payload |

---

## Testing Your Connector

### Unit Test Example

```python
import json
import unittest
from unittest.mock import AsyncMock, patch

from cascadia.connectors.telegram.connector import TelegramConnector


class TestTelegramConnector(unittest.TestCase):
    def test_normalizes_inbound_message(self):
        raw = {"message": {"from": {"id": 1}, "text": "hello"}}
        connector = TelegramConnector(token="test")
        event = connector.normalize(raw)
        self.assertEqual(event["connector"], "telegram")
        self.assertEqual(event["text"], "hello")
        self.assertEqual(event["event_type"], "message")

    @patch("nats.connect", new_callable=AsyncMock)
    def test_publishes_to_correct_subject(self, mock_nats):
        ...

    def test_rejects_invalid_signature(self):
        ...

    def test_retries_on_429(self):
        ...
```

### Minimum Test Requirements

Every connector submitted to DEPOT must have at least **4 passing tests**:

1. Inbound message normalization
2. NATS subject published correctly
3. Signature verification (if `auth_type` is `hmac`)
4. Retry logic on transient error or rate limit

Run the test suite:

```bash
python -m pytest tests/connectors/test_myplatform.py -v
```

---

## Submitting to DEPOT

### Required Files Checklist

```
cascadia/connectors/myplatform/
├── connector.py        ← main connector process
├── manifest.json       ← DEPOT manifest
├── install.sh          ← starts the process
├── uninstall.sh        ← stops the process cleanly
├── health.py           ← standalone health check
├── README.md           ← setup guide
└── tests/
    └── test_connector.py   ← 4+ tests
```

### Submission Process

1. Fork `github.com/zyrconlabs/cascadia-os`
2. Add your connector under `cascadia/connectors/[name]/`
3. Open a pull request with the title: `feat: add [Platform] connector`
4. The DEPOT review bot checks: manifest validity, health endpoint, test count
5. A Zyrcon Labs reviewer approves and merges

### Review Criteria

- Manifest fields are complete and valid
- Health endpoint returns `{"status": "healthy"}` on port specified
- No credentials hardcoded; all secrets via env vars
- At least 4 unit tests passing
- README covers setup end-to-end
- Webhook signature verification implemented (if applicable)

### Revenue Share

| Monthly revenue | Split |
|---|---|
| First $25,000 | 100% to developer |
| Above $25,000 | 80% developer / 20% Zyrcon Labs |

Free connectors (price: 0) are always 100% to the developer — the revenue share
applies to paid connectors only.
