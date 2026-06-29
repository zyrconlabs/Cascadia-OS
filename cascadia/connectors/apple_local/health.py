#!/usr/bin/env python3
"""Health checker for the Apple Local connector."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _manifest_path() -> Path:
    return Path(__file__).with_name("manifest.json")


def _manifest_port() -> int:
    data = json.loads(_manifest_path().read_text())
    return int(data["port"])


def _valid_health(body: dict[str, Any], port: int) -> bool:
    readiness = body.get("readiness")
    return (
        body.get("connector") == "apple-local-connector"
        and body.get("port") == port
        and body.get("phase") == 1
        and body.get("status") in {"healthy", "degraded"}
        and isinstance(readiness, dict)
        and all(domain in readiness for domain in ("calendar", "reminders", "notes"))
    )


def main() -> int:
    try:
        port = _manifest_port()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if _valid_health(body, port):
            print(json.dumps({"status": "ok", "connector": "apple-local-connector", "port": port}))
            return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError, urllib.error.URLError):
        pass
    print(json.dumps({"status": "error", "connector": "apple-local-connector"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
