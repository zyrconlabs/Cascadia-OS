"""
Health Watchdog — silent-death alerting for Cascadia OS.

Checks all critical services every 5 minutes and sends a Telegram alert to the
owner when a service is down (and a recovery notice when it returns). Runs as a
standalone LaunchAgent, independent of BEACON/OperatorManager, so it can detect
an OM failure too.

Standalone by design — not imported by any operator.
"""
import json
import logging
import os
import re
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ── config ───────────────────────────────────────────────────────────────────
CHECK_INTERVAL = 300          # 5 minutes
OWNER_CHAT_ID = os.environ.get("TELEGRAM_OWNER_CHAT_ID", "")
TELEGRAM_URL = "http://127.0.0.1:9000/send"

# Node identity — derived from node_role so the same file works unchanged on
# every machine (no per-host hand-editing). Reads current_release.json, falls
# back to hostname if that's missing/unreadable.
RELEASE_JSON_PATH = Path(__file__).resolve().parents[2] / "data" / "runtime" / "current_release.json"


def _node_role() -> str:
    try:
        with open(RELEASE_JSON_PATH) as f:
            role = json.load(f).get("node_role")
        if role in ("mini", "air"):
            return role
    except Exception:
        pass
    import socket
    return "air" if "air" in socket.gethostname().lower() else "mini"


NODE_ROLE = _node_role()
NODE_NAME = "zyrcon-node-b" if NODE_ROLE == "air" else "zyrcon-node-a"

# (name, port, health_path, critical?). Health paths verified against live
# services — note vault answers on /health, NOT /api/health.
SERVICES = [
    ("llm",      8080, "/v1/models",   True),
    ("chief",    6211, "/health",      True),
    ("scout",    7002, "/api/health",  True),
    ("recon",    8002, "/api/health",  True),
    ("email",    8010, "/api/health",  True),
    ("scout-tg", 9002, "/api/health",  True),
    ("beacon",   6210, "/health",      True),
    ("vault",    5101, "/health",      True),
    ("collect",  8003, "/api/health",  False),
    ("quote",    8007, "/api/health",  False),
    ("crm",      8015, "/api/health",  False),
]

# Per-node service filtering. The SERVICES list above is the canonical
# (mini/superset) set — we keep ONE list and subtract what a given role does
# NOT run, rather than maintaining a parallel per-node list that could drift.
# Air is a lean node (no scout-telegram, quote, or CRM operators).
# NOTE: stable.json's required_services is names-only AND a different
# vocabulary (core-plane release-gate names, not these HTTP operator names),
# so it can't drive this list without dropping most of the mini's services —
# hence a role-keyed exclusion here instead.
_NODE_SERVICE_EXCLUSIONS = {
    "air": {"scout-tg", "quote", "crm"},
}
_excluded = _NODE_SERVICE_EXCLUSIONS.get(NODE_ROLE, set())
_filtered = [s for s in SERVICES if s[0] not in _excluded]
# Never monitor nothing: if a bad exclusion would empty the list, keep the
# full set (a noisy watchdog beats a blind one). main() logs if this fires.
_SERVICES_FALLBACK = not _filtered
SERVICES = SERVICES if _SERVICES_FALLBACK else _filtered

CRITICAL_THRESHOLD = 1   # alert after 1 failed check (~5 min)
STANDARD_THRESHOLD = 3   # alert after 3 failed checks (~15 min)
REMINDER_EVERY = 6       # re-alert every 6 checks (~30 min) while still down
AUDIT_PROBE_EVERY = 12   # run audit-pipeline liveness probe every 12 cycles (~1h) — NIST 3.3.4

# ── logging ──────────────────────────────────────────────────────────────────
_log_path = Path(__file__).resolve().parents[2] / "data" / "logs" / "health_watchdog.log"
_log_path.parent.mkdir(parents=True, exist_ok=True)
_handler = RotatingFileHandler(str(_log_path), maxBytes=5 * 1024 * 1024,
                               backupCount=2, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logging.root.handlers = [_handler]
logging.root.setLevel(logging.INFO)
log = logging.getLogger("watchdog")

# ── state ────────────────────────────────────────────────────────────────────
_down_counts: dict = {}   # name -> consecutive failed checks
_alerted: dict = {}       # name -> alert already sent for this outage
_cycle: int = 0           # check cycles elapsed (for hourly audit probe cadence)
_audit_probe_alerted: bool = False  # audit-failure alert already sent (avoid spam)


def _check(name, port, path) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.getcode() == 200
    except Exception:
        return False


def _send(text: str) -> None:
    try:
        payload = json.dumps({"chat_id": OWNER_CHAT_ID, "text": text}).encode()
        req = urllib.request.Request(TELEGRAM_URL, data=payload, method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        log.info("alert sent: %s", text.splitlines()[0])
    except Exception as e:
        log.error("alert send failed: %s", e)


def _ram_summary() -> str:
    try:
        vm = subprocess.check_output(["vm_stat"], timeout=3).decode()
        try:
            total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"]).decode()) // (1024 ** 2)
        except Exception:
            total = 16384
        page = 16384

        def _pg(label):
            m = re.search(rf"{label}[^:]*:\s+(\d+)", vm)
            return int(m.group(1)) * page // (1024 ** 2) if m else 0

        p = (_pg("Pages active") + _pg("Pages wired down")) / total if total else 0
        band = "GREEN" if p < 0.65 else "YELLOW" if p < 0.75 else "ORANGE" if p < 0.85 else "RED"
        return f"RAM: {p*100:.0f}% ({band})"
    except Exception:
        return "RAM: unknown"


def _probe_audit_pipeline() -> None:
    """NIST 3.3.4 — prove the live audit pipeline can still record events.

    Appends one real audit event via the SAME production write path
    (enterprise features.audit_log.log_event → data/audit.log), then confirms
    it actually persisted by re-reading the last event's hash. Any failure —
    import unavailable, write raises, or the event doesn't land — fires an
    operator alert. The probe event is itself a legitimate audit record
    proving the pipeline was alive at that hour.
    """
    global _audit_probe_alerted
    try:
        import sys
        ent = Path(__file__).resolve().parents[3] / "enterprise"
        if str(ent) not in sys.path:
            sys.path.insert(0, str(ent))
        from features import audit_log

        written = audit_log.log_event(
            event_type="audit.health_check",
            operator="watchdog",
            action="pipeline_liveness_probe",
        )
        # Verify the write actually persisted to disk (not just returned).
        recent = audit_log.get_recent_events(limit=1)
        if not recent or recent[0].get("hash") != written.get("hash"):
            raise RuntimeError("probe event did not persist to audit.log")

        if _audit_probe_alerted:
            _send("✅ Recovered: audit logging pipeline\n"
                  f"Node: {NODE_NAME}\nAudit writes confirmed landing again.")
            _audit_probe_alerted = False
        log.info("audit probe OK — event %s persisted", written.get("hash"))
    except Exception as e:
        log.error("AUDIT LOGGING FAILURE: %s", e)
        if not _audit_probe_alerted:
            _send(f"🔴 AUDIT LOGGING FAILURE — {NODE_NAME}\n\n"
                  f"The audit pipeline failed a liveness probe:\n{e}\n\n"
                  f"Audit records may not be persisting (NIST 3.3.4).\n"
                  f"Check data/audit.log writability + enterprise features.")
            _audit_probe_alerted = True


def _check_all() -> None:
    global _cycle
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    newly_down = []
    for name, port, path, critical in SERVICES:
        if _check(name, port, path):
            if _down_counts.get(name, 0) and _alerted.get(name):
                _send(f"✅ Recovered: {name}\nNode: {NODE_NAME}\nTime: {now}")
            _down_counts[name] = 0
            _alerted[name] = False
            continue
        count = _down_counts.get(name, 0) + 1
        _down_counts[name] = count
        log.warning("DOWN: %s (count=%d)", name, count)
        threshold = CRITICAL_THRESHOLD if critical else STANDARD_THRESHOLD
        if count == threshold and not _alerted.get(name):
            newly_down.append((name, count, critical))
            _alerted[name] = True
        elif count > threshold and _alerted.get(name) and count % REMINDER_EVERY == 0:
            _send(f"🔴 STILL DOWN: {name}\nDown ~{count*CHECK_INTERVAL//60}min\n"
                  f"Node: {NODE_NAME}\n{_ram_summary()}")

    if newly_down:
        body = "\n".join(
            f"  {'🔴' if c else '🟡'} {n} (down ~{cnt*CHECK_INTERVAL//60}min)"
            for n, cnt, c in newly_down)
        _send(f"⚠️ SERVICE DOWN — {NODE_NAME}\n\n{body}\n\n{_ram_summary()}\n"
              f"Time: {now}\nCheck: launchctl list | grep zyrcon")

    # NIST 3.3.4 — audit-pipeline liveness probe, ~hourly (every 12th cycle,
    # including the first check at startup).
    if _cycle % AUDIT_PROBE_EVERY == 0:
        _probe_audit_pipeline()
    _cycle += 1


def main() -> None:
    log.info("health watchdog started — node=%s role=%s — %d services every %ds",
             NODE_NAME, NODE_ROLE, len(SERVICES), CHECK_INTERVAL)
    if _SERVICES_FALLBACK:
        log.warning("node exclusions would have emptied SERVICES — kept full list as fallback")
    print(f"[WATCHDOG] {NODE_NAME} monitoring {len(SERVICES)} services every {CHECK_INTERVAL//60}min")
    _check_all()
    while True:
        time.sleep(CHECK_INTERVAL)
        _check_all()


if __name__ == "__main__":
    main()
