# Testing Operators and Connectors

This guide covers how to test your operator or connector before DEPOT submission. The platform test suite verifies core infrastructure — it does not test your operator's business logic. That is your responsibility.

---

## Overview

The platform test suite (`tests/`) covers the runtime, manifest validator, approval store, connectors infrastructure, and all core components. It does not run your operator's code.

Before DEPOT submission, you must verify:
1. Your `manifest.json` passes the validator
2. Your `health.py` exits 0 when running, 1 when not
3. Your server responds on the declared port
4. Your connector has at least 4 passing unit tests

---

## Running the Full Test Suite

From the repo root:

```bash
python -m pytest tests/ -v
```

Current baseline: **1169 passing, 17 skipped** (as of Cascadia OS 2026.5).

Run tests for a specific area:

```bash
# Manifest validation tests
python -m pytest tests/test_depot_manifest.py tests/test_manifest.py -v

# Connector packaging tests
python -m pytest tests/test_connector_packaging.py -v

# Approval flow tests
python -m pytest tests/test_approvals.py tests/test_e2e_approval_flow.py -v

# VANTAGE gateway
python -m pytest tests/test_vantage.py -v
```

To run quietly and see only failures:

```bash
python -m pytest tests/ -q
```

---

## Testing Your Operator Locally

### Manifest Validation

Validate your `manifest.json` before submitting. Two validators exist depending on whether your manifest is for an operator (uses the core schema) or a DEPOT-published item (uses the DEPOT schema):

**Core operator manifest** (for operators registered with CREW):

```bash
python3 -c "
from cascadia.shared.manifest_schema import validate_manifest
import json, sys
data = json.load(open('manifest.json'))
try:
    m = validate_manifest(data)
    print(f'Valid: {m.id} v{m.version}')
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    sys.exit(1)
"
```

**DEPOT manifest** (for operators and connectors submitted to the marketplace):

```bash
python -m cascadia.depot.manifest_validator path/to/manifest.json
```

Or from Python:

```python
from cascadia.depot.manifest_validator import validate_depot_manifest_file

result = validate_depot_manifest_file("manifest.json")
if not result.valid:
    for err in result.errors:
        print(f"ERROR: {err}")
```

Common validation errors and fixes:

| Error | Fix |
|-------|-----|
| `Missing keys: ['health_hook']` | Add `"health_hook": "http://localhost:[port]/api/health"` |
| `Invalid type: skill` | Use one of: `system`, `service`, `skill`, `composite` |
| `quality_level must be one of...` | Use `"apprentice"`, `"professional"`, or `"advanced"` |
| `setup_fields entry 'api_key' has invalid type 'text'` | Use `"secret"` or `"string"` instead of `"text"` |
| `Manifest id must be lowercase and underscored` | Use underscores in the `id` field, not hyphens |

---

### Health Check Test

```bash
# Make sure your server is running first
python3 server.py &

# Run the health check
python3 health.py

# Verify the exit code
python3 health.py; echo "Exit code: $?"
# 0 = healthy, 1 = unhealthy or unreachable
```

Test the exit code with the server stopped:

```bash
# Kill the server
kill %1

# Should exit 1
python3 health.py; echo "Exit code: $?"
```

---

### Server Startup Test

```bash
# Start your server
python3 server.py &
SERVER_PID=$!

# Wait for startup
sleep 2

# Verify health endpoint
curl -s http://localhost:PORT/api/health | python3 -m json.tool

# Verify status endpoint (if your server exposes one)
curl -s http://localhost:PORT/api/status | python3 -m json.tool

# Clean up
kill $SERVER_PID
```

Replace `PORT` with the port in your `manifest.json`.

---

### NATS Integration Test

Requires NATS to be running (it starts automatically with `bash start.sh`):

```bash
# Install nats CLI (macOS)
brew install nats-io/nats-tools/nats

# In terminal 1: watch for responses
nats sub "cascadia.connectors.my-connector.>"

# In terminal 2: send a test action
nats pub "cascadia.connectors.my-connector.call" \
  '{"action": "my_action", "param": "value"}'
```

Your connector should receive the message in terminal 2 and publish a response that appears in terminal 1.

For the subject format, see `docs/NATS_reference.md`.

---

### Unit Test — Minimum Requirements

Every connector submitted to DEPOT must have at least **4 passing tests**. Required test cases (from `docs/connectors.md`):

1. Inbound message normalization
2. NATS subject published correctly
3. Signature verification (required if `auth_type` is `hmac`)
4. Retry logic on transient error or rate limit

Example test file structure (from `docs/connectors.md`):

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
        # verify nc.publish is called with the right subject
        ...

    def test_rejects_invalid_signature(self):
        # for hmac auth_type connectors only
        ...

    def test_retries_on_429(self):
        # verify retry behavior on rate limit
        ...
```

Run your connector tests:

```bash
python -m pytest tests/connectors/test_myplatform.py -v
```

---

### install.sh Dry Run

```bash
# Syntax check only — does not execute
bash -n install.sh && echo "Syntax OK"

# Full run with required env vars set
export MY_CONNECTOR_API_KEY="test_key"
export MY_CONNECTOR_SECRET="test_secret"
bash install.sh
```

---

## DEPOT Submission Checklist

Extracted from `docs/connectors.md`. Work through this before opening a pull request:

```
cascadia/connectors/myplatform/
  [ ] connector.py        — main connector process
  [ ] manifest.json       — DEPOT manifest (passes manifest validator)
  [ ] install.sh          — installs dependencies, registers with CREW
  [ ] uninstall.sh        — stops process, deregisters from CREW
  [ ] health.py           — exits 0 when healthy, 1 when not
  [ ] README.md           — setup guide (credentials, env vars, health check)
  [ ] tests/
        [ ] test_connector.py   — 4+ tests passing
```

Review criteria (from `docs/connectors.md`):

- [ ] Manifest fields are complete and valid
- [ ] Health endpoint returns `{"status": "healthy"}` on the port in manifest
- [ ] No credentials hardcoded; all secrets via env vars
- [ ] At least 4 unit tests passing
- [ ] README covers setup end-to-end
- [ ] Webhook signature verification implemented (if `auth_type` is `hmac`)

---

## CI Recommendations

Minimal GitHub Actions workflow for a connector repository. Covers manifest validation, health check, and server startup ping on every push:

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install nats-py httpx pytest
      - run: python -m pytest tests/ -v
      - name: Validate manifest
        run: |
          python3 -c "
          import json, sys
          data = json.load(open('manifest.json'))
          required = {'id','name','version','type','tier_required','port','entry_point'}
          missing = required - set(data)
          if missing:
              print('Missing fields:', missing); sys.exit(1)
          print('Manifest OK')
          "
      - name: Health check exit code
        run: |
          python3 server.py &
          sleep 3
          python3 health.py
          python3 health.py; test $? -eq 0
```

This is intentionally minimal. Add steps for your specific connector's integration test if you have a test API key available in CI secrets.
