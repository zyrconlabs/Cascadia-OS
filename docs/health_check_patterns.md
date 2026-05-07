# Health Check Patterns

Every operator and connector must include a `health.py` file in its package root. FLINT and the OperatorWatchdog call this file independently of the main server process to determine whether the operator is alive and responding. DEPOT submission requires it.

---

## Overview

`health.py` is a standalone Python script (not a module imported by the server). It:

1. Reads the manifest to determine which port to check
2. Makes an HTTP GET to `/api/health` on that port
3. Prints a JSON result to stdout
4. Exits with code **0** if healthy, **1** if unhealthy

Exit codes are what matter to FLINT and the watchdog. The JSON output is for human inspection and PRISM display.

---

## Standard health.py Template

This template is extracted directly from the 330+ operator and connector `health.py` files in the operators repository. All files follow this identical structure.

```python
#!/usr/bin/env python3
import json
import os
import urllib.request
import urllib.error
from pathlib import Path

_MANIFEST = json.loads(
    Path(__file__).parent.joinpath("manifest.json").read_text()
)

OPERATOR_ID = _MANIFEST.get("id", "my_operator")
PORT = int(os.environ.get(
    "MY_OPERATOR_PORT",                  # env var override (SCREAMING_SNAKE of id)
    _MANIFEST.get("port", 8000)          # fallback to manifest port
))
VERSION = _MANIFEST.get("version", "0.1.0")


def check() -> dict:
    import time
    url = f"http://127.0.0.1:{PORT}/api/health"
    start = time.time()
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            latency_ms = round((time.time() - start) * 1000, 1)
            body = json.loads(resp.read().decode())
            return {
                "operator_id": OPERATOR_ID,
                "status": body.get("status", "unknown"),
                "port": PORT,
                "version": VERSION,
                "latency_ms": latency_ms,
                "reachable": True,
            }
    except urllib.error.URLError as e:
        return {
            "operator_id": OPERATOR_ID,
            "status": "unreachable",
            "port": PORT,
            "version": VERSION,
            "latency_ms": None,
            "reachable": False,
            "error": str(e.reason),
        }
    except Exception as e:
        return {
            "operator_id": OPERATOR_ID,
            "status": "error",
            "port": PORT,
            "version": VERSION,
            "latency_ms": None,
            "reachable": False,
            "error": str(e),
        }


def main():
    result = check()
    print(json.dumps(result, indent=2))
    return 0 if result["reachable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

**The `raise SystemExit(main())` pattern** is intentional. It ensures the exit code from `main()` is correctly propagated to the shell, even if called through a test runner that intercepts `sys.exit()`.

---

## Adapting for Your Operator

**Port env var naming convention:** The env var override follows `SCREAMING_SNAKE_CASE` of the operator ID with `_PORT` appended.

| Operator ID | Port env var |
|-------------|-------------|
| `airtable` | `AIRTABLE_PORT` |
| `social_chat_operator` | `SOCIAL_CHAT_OPERATOR_PORT` |
| `trade_show_lead_scanner` | `TRADE_SHOW_LEAD_SCANNER_PORT` |
| `customer_360` | `CUSTOMER_360_PORT` |

The env var override is optional — most deployments read directly from the manifest. It exists for testing on a non-default port.

**Timeout:** Use `timeout=3` (3 seconds). Do not use a longer timeout — the watchdog has its own outer timeout and a slow health.py blocks the check loop.

---

## HTTP-Based Health Check

The standard pattern above is HTTP-based. Your server must expose a `GET /api/health` endpoint that returns JSON with a `"status"` key:

```json
{"status": "healthy"}
```

The `health.py` reads `body.get("status", "unknown")` from this response and includes it in the output. Any HTTP 200 response counts as reachable (`"reachable": True`). The `status` field value in the response body is passed through as-is.

Example server endpoint (minimal):

```python
from http.server import BaseHTTPRequestHandler
import json

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/health":
            body = json.dumps({"status": "healthy", "ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
```

---

## What FLINT Does With the Result

FLINT's `_check_health()` method (in `cascadia/kernel/flint.py`) calls `GET /health` on the component's registered port every `health_interval_seconds` (default: **5 seconds**):

```python
def _check_health(self, component: ProcessEntry) -> bool:
    proc = self.processes.get(component.name)
    if proc is None or proc.poll() is not None:
        component.process_state = 'offline'
        component.healthy = False
        return False
    try:
        p = self._http_get(component.port, '/health')
        ok = bool(p.get('ok'))
        component.healthy = ok
        component.process_state = p.get('state', 'ready')
        return ok
    except Exception as exc:
        component.healthy = False
        return False
```

FLINT checks `p.get('ok')` (boolean) from the `/health` response — not `status`. Your server's `/health` endpoint should return `{"ok": true, "state": "ready"}` for FLINT to consider the component healthy.

Note: `health.py` (the standalone file) is separate from the HTTP `/health` endpoint your server exposes. FLINT calls the HTTP endpoint directly. `health.py` is used by the watchdog, PRISM, and the DEPOT reviewer.

### Restart policy (from `config.example.json`)

When `_check_health()` returns `False`, FLINT schedules a restart with exponential backoff:

| Restart attempt | Delay before restart |
|:--------------:|:--------------------:|
| 1st | 5 s |
| 2nd | 30 s |
| 3rd | 120 s |
| 4th+ | 600 s (10 min) |
| After 5 failures | No more restarts — component stays offline |

`max_restart_attempts: 5` and `restart_backoff_seconds: [5, 30, 120, 600]` are configured in `config.example.json` under the `flint` block.

---

## Health Check Requirements for DEPOT Submission

From `docs/connectors.md` submission checklist:

```
cascadia/connectors/myplatform/
├── connector.py        ← main connector process
├── manifest.json       ← DEPOT manifest
├── install.sh          ← starts the process
├── uninstall.sh        ← stops the process cleanly
├── health.py           ← standalone health check    ← REQUIRED
├── README.md           ← setup guide
└── tests/
    └── test_connector.py   ← 4+ tests
```

DEPOT review criteria for `health.py`:
- File must be named exactly `health.py` in the package root
- Must be executable (`chmod +x health.py` or `#!/usr/bin/env python3` shebang)
- Must exit with code `0` when the connector is healthy
- Must exit with code `1` when the connector is not running or unhealthy
- Health endpoint must return `{"status": "healthy"}` on the port in manifest
- Must complete within the 3-second timeout

---

## Running health.py Manually

```bash
# Start your connector first
python3 connector.py &

# Run health check
python3 health.py

# Check exit code
python3 health.py; echo "Exit: $?"
# 0 = healthy, 1 = unhealthy

# Pretty-print the output
python3 health.py | python3 -m json.tool
```

Expected output when healthy:

```json
{
  "operator_id": "my_operator",
  "status": "healthy",
  "port": 9099,
  "version": "1.0.0",
  "latency_ms": 2.3,
  "reachable": true
}
```

Expected output when not running:

```json
{
  "operator_id": "my_operator",
  "status": "unreachable",
  "port": 9099,
  "version": "1.0.0",
  "latency_ms": null,
  "reachable": false,
  "error": "Connection refused"
}
```

---

## Common Mistakes

**Wrong exit code — printing an error but exiting 0.**
If `main()` always returns `0`, FLINT will never detect the connector is down. The return value of `main()` must reflect `result["reachable"]`.

**Port hardcoded instead of read from manifest.**
The port in `health.py` must match the port your connector is actually listening on. Read it from manifest.json so they stay in sync automatically.

**No timeout.**
`urllib.request.urlopen(url)` with no timeout parameter hangs indefinitely if the connector is frozen (not crashed). Always pass `timeout=3`.

**Missing shebang line.**
`health.py` must start with `#!/usr/bin/env python3`. Without it, `chmod +x health.py` and direct execution (`./health.py`) will fail.

**Checking `/health` instead of `/api/health`.**
All Cascadia OS operators expose their health endpoint at `/api/health`. FLINT's internal check uses `/health` at the system component level — your operator server should expose `/api/health` for external checks and `health.py`.

**Importing the server module.**
`health.py` must be a standalone script. Do not import `connector.py` or `server.py` from within `health.py` — it will start the server as a side effect of the import.
