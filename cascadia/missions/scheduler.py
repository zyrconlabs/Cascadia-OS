"""Mission Scheduler — fires MissionRunner.start_mission on cron schedule.

Uses the same background daemon-thread + Event pattern as
cascadia.automation.scheduler.Scheduler, extended with standard 5-field
cron matching for mission schedule expressions.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from cascadia.missions.registry import MissionRegistry

log = logging.getLogger(__name__)

POLL_INTERVAL = 30  # seconds — same default as automation.scheduler.Scheduler


# ── Cron helpers ──────────────────────────────────────────────────────────────

def _cron_field_ok(spec: str, val: int) -> bool:
    for part in spec.split(','):
        if part == '*':
            return True
        if part.startswith('*/'):
            try:
                step = int(part[2:])
                if step > 0 and val % step == 0:
                    return True
            except ValueError:
                pass
        else:
            try:
                if int(part) == val:
                    return True
            except ValueError:
                pass
    return False


def _cron_matches(expr: str, now: datetime) -> bool:
    """True if a 5-field cron expression fires at the given UTC datetime (minute precision)."""
    parts = expr.strip().split()
    if len(parts) != 5:
        return False
    cron_dow = (now.weekday() + 1) % 7  # cron: 0=Sun…6=Sat
    return (
        _cron_field_ok(parts[0], now.minute)
        and _cron_field_ok(parts[1], now.hour)
        and _cron_field_ok(parts[2], now.day)
        and _cron_field_ok(parts[3], now.month)
        and _cron_field_ok(parts[4], cron_dow)
    )


def _cron_next_iso(expr: str) -> Optional[str]:
    """Return ISO 8601 UTC string of next fire time for a cron expression, or None."""
    parts = expr.strip().split()
    if len(parts) != 5:
        return None
    t = (int(time.time()) // 60 + 1) * 60
    limit = t + 366 * 24 * 3600
    while t < limit:
        dt = datetime.fromtimestamp(t, tz=timezone.utc)
        cron_dow = (dt.weekday() + 1) % 7
        if (
            _cron_field_ok(parts[0], dt.minute)
            and _cron_field_ok(parts[1], dt.hour)
            and _cron_field_ok(parts[2], dt.day)
            and _cron_field_ok(parts[3], dt.month)
            and _cron_field_ok(parts[4], cron_dow)
        ):
            return dt.isoformat()
        t += 60
    return None


def _resolve_db_path() -> str:
    try:
        p = Path(__file__).parent.parent.parent / "config.json"
        if p.exists():
            cfg = json.loads(p.read_text(encoding="utf-8"))
            return cfg.get("database_path", "./data/runtime/cascadia.db")
    except Exception:
        pass
    return "./data/runtime/cascadia.db"


# ── MissionScheduler ──────────────────────────────────────────────────────────

class MissionScheduler:
    """
    Reads cron schedules from installed mission manifests and fires
    MissionRunner.start_mission() when they are due.

    Background thread polls every POLL_INTERVAL seconds.
    Duplicate-run guard prevents stacking runs for the same
    mission/workflow while a run is already active.
    """

    def __init__(
        self,
        registry: Optional[MissionRegistry] = None,
        runner: Any = None,
        db_path: Optional[str] = None,
        poll_interval: int = POLL_INTERVAL,
    ) -> None:
        self._registry = registry or MissionRegistry()
        self._runner = runner
        self._db_path = db_path or _resolve_db_path()
        self._poll_interval = poll_interval
        self._schedules: Dict[str, dict] = {}
        self._fired_keys: Dict[str, str] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def load_schedules(self) -> List[dict]:
        """Return enabled schedule entries from all installed missions."""
        installed = self._registry.list_installed()
        installed_ids: set = set()
        for entry in installed:
            if isinstance(entry, dict):
                installed_ids.add(entry.get("id"))
            elif isinstance(entry, str):
                installed_ids.add(entry)

        results = []
        for mission_id in sorted(installed_ids):
            manifest = self._registry.get_mission(mission_id)
            if not manifest:
                continue
            for sched in manifest.get("schedules") or []:
                if not sched.get("enabled_by_default", False):
                    continue
                workflow_id = sched.get("workflow") or sched.get("workflow_id", "")
                cron = sched.get("cron", "")
                if not workflow_id or not cron:
                    continue
                results.append({
                    "mission_id": mission_id,
                    "schedule_id": sched.get("id", workflow_id),
                    "workflow_id": workflow_id,
                    "cron": cron,
                    "enabled": True,
                })
        return results

    def register_schedules(self) -> int:
        """Load and register all enabled schedules. Idempotent — skips duplicates.

        Returns count of newly registered schedules.
        """
        schedules = self.load_schedules()
        count = 0
        for s in schedules:
            key = f"{s['mission_id']}::{s['schedule_id']}"
            with self._lock:
                if key in self._schedules:
                    continue
                self._schedules[key] = s
            log.info("Registered mission schedule: %s (%s)", key, s["cron"])
            count += 1
        return count

    def unregister_schedules(self, mission_id: Optional[str] = None) -> int:
        """Remove schedules. Pass mission_id to remove only that mission's schedules.

        Returns count removed.
        """
        with self._lock:
            if mission_id is None:
                removed = len(self._schedules)
                self._schedules.clear()
                self._fired_keys.clear()
            else:
                prefix = f"{mission_id}::"
                keys = [k for k in self._schedules if k.startswith(prefix)]
                for k in keys:
                    del self._schedules[k]
                    self._fired_keys.pop(k, None)
                removed = len(keys)
        return removed

    def start(self) -> None:
        """Register schedules and start the background scheduler thread (daemon)."""
        if self._thread and self._thread.is_alive():
            return
        self.register_schedules()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="cascadia-mission-scheduler",
        )
        self._thread.start()
        log.info("Mission scheduler started — %d schedules registered", len(self._schedules))

    def stop(self) -> None:
        """Stop the scheduler loop and clear all registered schedules."""
        self._stop.set()
        self.unregister_schedules()
        log.info("Mission scheduler stopped")

    def status(self) -> dict:
        running = bool(self._thread and self._thread.is_alive())
        with self._lock:
            schedules = list(self._schedules.values())
        return {
            "running": running,
            "registered_schedules": len(schedules),
            "schedules": [
                {
                    "mission_id": s["mission_id"],
                    "schedule_id": s["schedule_id"],
                    "workflow_id": s["workflow_id"],
                    "cron": s["cron"],
                    "next_run": _cron_next_iso(s["cron"]),
                }
                for s in schedules
            ],
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = _now_utc()
            with self._lock:
                items = list(self._schedules.items())
            for key, s in items:
                if not _cron_matches(s["cron"], now):
                    continue
                fire_key = now.strftime("%Y-%m-%d-%H-%M")
                with self._lock:
                    if self._fired_keys.get(key) == fire_key:
                        continue
                    self._fired_keys[key] = fire_key
                threading.Thread(
                    target=self._fire_schedule,
                    args=(s["mission_id"], s["workflow_id"], s["schedule_id"]),
                    daemon=True,
                ).start()
            self._stop.wait(timeout=self._poll_interval)

    def _fire_schedule(self, mission_id: str, workflow_id: str, schedule_id: str) -> None:
        """Called when a cron schedule fires. Never crashes the scheduler loop."""
        if self._has_active_run(mission_id, workflow_id):
            log.warning(
                "Skipping schedule %s/%s — a run is already active",
                mission_id, workflow_id,
            )
            return
        if self._runner is None:
            log.warning("Scheduler has no runner — cannot fire %s/%s", mission_id, workflow_id)
            return
        try:
            result = self._runner.start_mission(
                mission_id=mission_id,
                workflow_id=workflow_id,
                trigger_type="schedule",
            )
            log.info(
                "Schedule fired %s/%s → run=%s status=%s",
                mission_id, workflow_id,
                result.get("mission_run_id", "?"),
                result.get("status", "?"),
            )
        except Exception as exc:
            log.error("Schedule fire failed %s/%s: %s", mission_id, workflow_id, exc)

    def _has_active_run(self, mission_id: str, workflow_id: str) -> bool:
        try:
            conn = sqlite3.connect(self._db_path)
            try:
                try:
                    row = conn.execute(
                        "SELECT id FROM mission_runs "
                        "WHERE mission_id=? AND workflow_id=? "
                        "AND status IN ('running','waiting_approval') LIMIT 1",
                        (mission_id, workflow_id),
                    ).fetchone()
                except sqlite3.OperationalError:
                    # workflow_id column absent on pre-migration DB — check without it
                    row = conn.execute(
                        "SELECT id FROM mission_runs "
                        "WHERE mission_id=? AND status IN ('running','waiting_approval') LIMIT 1",
                        (mission_id,),
                    ).fetchone()
                return row is not None
            finally:
                conn.close()
        except Exception:
            return False


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)
