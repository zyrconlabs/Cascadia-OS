"""
crew_heartbeat.py — Cascadia OS shared utility
Re-registers an operator with CREW every 30 seconds.

Solves H1: operators that start before CREW, or survive a CREW restart,
re-register automatically without any manual intervention.

Usage (in an operator's __main__ block, best-effort so a missing module
or path never crashes the operator):

    if __name__ == "__main__":
        ...
        try:
            import sys as _sys
            if "/Users/zyrcon/cascadia-os" not in _sys.path:
                _sys.path.insert(0, "/Users/zyrcon/cascadia-os")
            from cascadia.shared.crew_heartbeat import start_crew_heartbeat
            start_crew_heartbeat(Path(__file__).parent / "manifest.json")
        except Exception as exc:
            log.warning("CREW heartbeat unavailable: %s", exc)
        app.run(...)

The heartbeat:
  1. Reads the operator's manifest.json (full payload — preserves
     name/version/type/health_hook/task_hook/capabilities)
  2. Retries every 3s until CREW accepts registration (boot phase)
  3. Re-registers every 30s forever (steady state)
  4. Runs as a daemon thread — no cleanup needed
  5. Logs only on state change (quiet when healthy)
"""

import json
import logging
import threading
import time
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

CREW_URL            = "http://127.0.0.1:5100"
HEARTBEAT_INTERVAL  = 30   # seconds between re-registrations
BOOT_RETRY_INTERVAL = 3    # seconds between retries on first boot


def _load_manifest(manifest_path: Path) -> dict:
    """Load manifest.json, ensuring operator_id is set (from 'id' if needed)."""
    try:
        d = json.loads(Path(manifest_path).read_text())
        if "operator_id" not in d and "id" in d:
            d["operator_id"] = d["id"]
        return d
    except Exception as e:
        log.error("[CREW] Failed to read manifest %s: %s", manifest_path, e)
        return {}


def _register_once(manifest: dict) -> bool:
    """POST the full manifest to CREW /register. Returns True on success.
    CREW only requires operator_id; posting the full manifest preserves
    health_hook, task_hook, type, capabilities, etc."""
    if not manifest.get("operator_id"):
        log.error("[CREW] Cannot register: no operator_id in manifest")
        return False
    payload = json.dumps(manifest).encode()
    try:
        req = urllib.request.Request(
            f"{CREW_URL}/register", data=payload, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            resp = json.loads(r.read())
        return bool(
            resp.get("registered") or resp.get("ok") or
            resp.get("status") == "registered"
        )
    except Exception:
        return False


def _heartbeat_loop(manifest_path: Path):
    """Daemon thread: boot-retry every 3s until CREW accepts, then re-register
    every 30s. Re-reads manifest each attempt so config changes are picked up.
    Logs only on state change."""
    op_id = "unknown"
    was_registered = False

    # Boot phase — retry until success
    while True:
        manifest = _load_manifest(manifest_path)
        op_id = manifest.get("operator_id", "unknown")
        if _register_once(manifest):
            log.info("[CREW] %s registered (port %s)",
                     op_id, manifest.get("port", "?"))
            was_registered = True
            break
        log.debug("[CREW] %s waiting for CREW...", op_id)
        time.sleep(BOOT_RETRY_INTERVAL)

    # Steady state — re-register every 30s
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        manifest = _load_manifest(manifest_path)
        op_id = manifest.get("operator_id", op_id)
        ok = _register_once(manifest)
        if ok and not was_registered:
            log.info("[CREW] %s re-registered after CREW outage", op_id)
            was_registered = True
        elif not ok and was_registered:
            log.warning(
                "[CREW] %s re-registration failed "
                "(CREW may be restarting — retry in %ds)",
                op_id, HEARTBEAT_INTERVAL,
            )
            was_registered = False


def start_crew_heartbeat(manifest_path):
    """Start the CREW registration heartbeat. Call once in __main__ before
    app.run(). `manifest_path` is the path to the operator's manifest.json."""
    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        log.error("[CREW] manifest not found at %s — heartbeat not started",
                  manifest_path)
        return
    manifest = _load_manifest(manifest_path)
    op_id = manifest.get("operator_id", manifest.get("id", "?"))
    threading.Thread(
        target=_heartbeat_loop, args=(manifest_path,),
        daemon=True, name=f"crew-hb-{op_id}",
    ).start()
    log.info("[CREW] Heartbeat started — %s re-registers every %ds",
             op_id, HEARTBEAT_INTERVAL)
