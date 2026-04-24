# Building a Custom Operator for Cascadia OS

Operators are independent services that run alongside Cascadia OS, register their capabilities with CREW, and are invoked by the workflow engine (STITCH/BEACON). This guide walks through building a fully functional custom operator from scratch.

## What an operator is

An operator is a Python HTTP service that:
1. Exposes a `/health` endpoint
2. Exposes action endpoints (e.g. `/parse_lead`, `/enrich_company`)
3. Registers itself with CREW on startup
4. Declares its capabilities in a manifest file

Cascadia OS routes workflow steps to your operator by `operator_id`. The workflow engine calls your action endpoint, waits for a response, and continues execution.

---

## Quickstart: a working example

Below is a complete, minimal operator called `my_operator` that handles a single action: `summarise_text`.

### 1. Directory layout

```
cascadia/operators/my_operator/
├── manifest.json
├── my_operator_server.py
└── requirements.txt          # optional — only if you add deps
```

### 2. `manifest.json`

```json
{
  "id": "my_operator",
  "name": "My Operator",
  "version": "0.1.0",
  "type": "service",
  "capabilities": ["summarise_text"],
  "required_dependencies": [],
  "requested_permissions": [],
  "autonomy_level": "assistive",
  "health_hook": "/health",
  "description": "Summarises text payloads and returns a condensed result."
}
```

**Field reference**

| Field | Type | Valid values | Notes |
|---|---|---|---|
| `id` | string | lowercase, underscores only | Must match directory name |
| `type` | string | `system`, `service`, `skill`, `composite` | Use `service` for most operators |
| `autonomy_level` | string | `manual_only`, `assistive`, `semi_autonomous`, `autonomous` | Controls SENTINEL risk tier |
| `capabilities` | list | Any strings | Used by CREW capability validation |
| `requested_permissions` | list | dot-notation strings | e.g. `gmail.send`, `crm.write` — enforced by SENTINEL |

### 3. `my_operator_server.py`

```python
#!/usr/bin/env python3
"""
my_operator — minimal Cascadia OS operator example.
Registers with CREW on startup and handles summarise_text actions.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PORT = 7100
CREW_PORT = 5100


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _payload(self) -> dict:
        n = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(n)) if n else {}

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._json(200, {"status": "online", "version": "0.1.0", "operator": "my_operator"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        payload = self._payload()
        if self.path == "/summarise_text":
            text = payload.get("text", "")
            summary = text[:120] + ("…" if len(text) > 120 else "")
            self._json(200, {"summary": summary, "char_count": len(text)})
        else:
            self._json(404, {"error": f"unknown action: {self.path}"})

    def log_message(self, *_):
        pass  # silence access log


def register_with_crew(crew_port: int, retries: int = 6) -> None:
    """Register this operator with CREW — retry until CREW is reachable."""
    payload = json.dumps({
        "operator_id": "my_operator",
        "type": "service",
        "autonomy_level": "assistive",
        "capabilities": ["summarise_text"],
        "health_hook": "/health",
    }).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{crew_port}/register",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=3)
            print(f"Registered with CREW on :{crew_port}")
            return
        except Exception as exc:
            print(f"CREW not ready (attempt {attempt + 1}/{retries}): {exc}")
            time.sleep(5)
    print("Warning: CREW registration failed — operator is running but unregistered")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--crew-port", type=int, default=CREW_PORT)
    args = parser.parse_args()

    threading.Thread(
        target=register_with_crew,
        args=(args.crew_port,),
        daemon=True,
    ).start()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.allow_reuse_address = True
    print(f"my_operator listening on 127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
```

### 4. Register in `cascadia/operators/registry.json`

Add your operator entry so PRISM can discover and display it:

```json
{
  "version": "0.44",
  "operators": [
    {
      "id": "my_operator",
      "name": "My Operator",
      "category": "automation",
      "description": "Summarises text payloads and returns a condensed result.",
      "status": "production",
      "port": 7100,
      "health_path": "/health",
      "autonomy": "assistive"
    }
  ]
}
```

### 5. Start the operator

```bash
python3 cascadia/operators/my_operator/my_operator_server.py --port 7100
```

The operator registers with CREW automatically. Verify registration:

```bash
curl http://127.0.0.1:5100/crew
# → {"crew_size": 1, "operators": {"my_operator": {...}}}
```

---

## Using your operator in a workflow

Define a workflow step that routes to your operator:

```python
from cascadia.automation.stitch import WorkflowDefinition, WorkflowStep

my_workflow = WorkflowDefinition(
    workflow_id='summarise_report',
    name='Summarise Report',
    steps=[
        WorkflowStep('summarise', 'my_operator', 'summarise_text'),
    ],
)
```

When the workflow engine executes the `summarise` step, it calls `POST /summarise_text` on your operator's port.

---

## Key contracts

### Health endpoint

`GET /health` must return HTTP 200 with at minimum:

```json
{"status": "online"}
```

PRISM and the tray monitor this endpoint every 5 seconds.

### Action endpoints

Action endpoints receive a JSON payload and must return HTTP 200 with a JSON object. The workflow engine merges the returned keys into the run's `state_snapshot`, making them available to subsequent steps.

### CREW registration payload

```json
{
  "operator_id": "my_operator",
  "type": "service",
  "autonomy_level": "assistive",
  "capabilities": ["my_action"],
  "health_hook": "/health"
}
```

### Permissions and SENTINEL

If your operator performs side effects (sending email, writing to a CRM, etc.), declare them in `requested_permissions` in your manifest. SENTINEL enforces these at runtime. Workflows that require permissions you haven't declared will be blocked.

Side-effect actions that require explicit approval (like `email.send`) are configured in the runtime policy in `config.json`:

```json
{
  "policy": {
    "email.send": "approval_required",
    "crm.write": "allowed"
  }
}
```

---

## Packaging for production

1. Pin your dependencies in `requirements.txt`
2. Start your operator before or alongside `start.sh`
3. Register it in `cascadia/operators/registry.json` so PRISM shows it

Commercial operator packages (from `cascadia-os-operators`) follow this same interface and self-register on startup. The SDK is identical — there is no separate commercial SDK.
