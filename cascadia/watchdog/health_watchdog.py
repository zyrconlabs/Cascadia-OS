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
NODE_NAME = "zyrcon-node-a"

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
CRITICAL_THRESHOLD = 1   # alert after 1 failed check (~5 min)
STANDARD_THRESHOLD = 3   # alert after 3 failed checks (~15 min)
REMINDER_EVERY = 6       # re-alert every 6 checks (~30 min) while still down

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


def _check_all() -> None:
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


def main() -> None:
    log.info("health watchdog started — %d services every %ds", len(SERVICES), CHECK_INTERVAL)
    print(f"[WATCHDOG] monitoring {len(SERVICES)} services every {CHECK_INTERVAL//60}min")
    _check_all()
    while True:
        time.sleep(CHECK_INTERVAL)
        _check_all()


if __name__ == "__main__":
    main()
