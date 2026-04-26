"""
scheduler.py — Cascadia OS Task 5
Lightweight cron-style scheduler for recurring workflow triggers.
Runs as a background thread inside STITCH.
No external dependencies — stdlib only.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ScheduledJob:
    """One recurring job entry. Does not own execution — calls trigger_fn."""
    name: str
    schedule: str               # "HH:MM" daily, or "MON-FRI HH:MM", or "FRI HH:MM"
    trigger_fn: Callable[[], Any]
    enabled: bool = True
    last_fired: Optional[str] = None
    fire_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'schedule': self.schedule,
            'enabled': self.enabled,
            'last_fired': self.last_fired,
            'fire_count': self.fire_count,
        }

    def _day_matches(self, now: datetime) -> bool:
        """Check if the schedule's day filter matches today."""
        parts = self.schedule.strip().split()
        if len(parts) == 1:
            return True  # "HH:MM" fires every day
        day_spec = parts[0].upper()
        weekday = now.strftime('%a').upper()  # MON, TUE, WED, THU, FRI, SAT, SUN
        # Support "MON-FRI", "FRI", "MON"
        if '-' in day_spec:
            days = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']
            a, b = day_spec.split('-', 1)
            if a in days and b in days:
                return days.index(a) <= days.index(weekday) <= days.index(b)
        return weekday == day_spec or weekday[:3] == day_spec[:3]

    def should_fire(self, now: datetime) -> bool:
        """True if this job should fire right now (within the current minute)."""
        if not self.enabled:
            return False
        parts = self.schedule.strip().split()
        hhmm = parts[-1]  # last token is always HH:MM
        current_key = now.strftime('%Y-%m-%d') + ' ' + hhmm
        if self.last_fired == current_key:
            return False
        try:
            h, m = map(int, hhmm.split(':'))
        except ValueError:
            return False
        return now.hour == h and now.minute == m and self._day_matches(now)

    def fire(self) -> None:
        now = _now_utc()
        parts = self.schedule.strip().split()
        hhmm = parts[-1]
        self.last_fired = now.strftime('%Y-%m-%d') + ' ' + hhmm
        self.fire_count += 1
        try:
            self.trigger_fn()
        except Exception:
            pass  # Scheduler owns scheduling, not error recovery


class Scheduler:
    """
    Background thread that fires ScheduledJobs on their configured schedule.
    Checks jobs every 30 seconds. Safe to add/remove jobs while running.
    """

    def __init__(self, poll_interval: int = 30) -> None:
        self._jobs: Dict[str, ScheduledJob] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._poll_interval = poll_interval
        self._thread: Optional[threading.Thread] = None

    def add_job(self, name: str, schedule: str, trigger_fn: Callable[[], Any], enabled: bool = True) -> ScheduledJob:
        """Add or replace a scheduled job."""
        job = ScheduledJob(name=name, schedule=schedule, trigger_fn=trigger_fn, enabled=enabled)
        with self._lock:
            self._jobs[name] = job
        return job

    def remove_job(self, name: str) -> bool:
        with self._lock:
            return self._jobs.pop(name, None) is not None

    def list_jobs(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [j.to_dict() for j in self._jobs.values()]

    def start(self) -> None:
        """Start the background scheduler thread (daemon)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name='cascadia-scheduler')
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = _now_utc()
            with self._lock:
                jobs = list(self._jobs.values())
            for job in jobs:
                if job.should_fire(now):
                    threading.Thread(target=job.fire, daemon=True).start()
            self._stop.wait(timeout=self._poll_interval)
