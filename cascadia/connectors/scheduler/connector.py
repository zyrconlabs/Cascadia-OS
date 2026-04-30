"""
cascadia/connectors/scheduler/connector.py — CON-115
Scheduler Connector · Zyrcon Labs · v1.0.0

Owns: one-shot delayed jobs, recurring cron-style jobs,
      NATS event dispatch when jobs fire, job registry (in-memory).
Does not own: workflow routing, persistent job storage (Vault does that).
"""
from __future__ import annotations

import asyncio
import http.server
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import nats
    _NATS_AVAILABLE = True
except ImportError:
    _NATS_AVAILABLE = False

NAME = "scheduler-connector"
VERSION = "1.0.0"
PORT = 9987

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [scheduler] %(message)s',
)
log = logging.getLogger(NAME)

_start_time = time.time()

# ── Job model ─────────────────────────────────────────────────────────────────

@dataclass
class Job:
    job_id: str
    name: str
    target_subject: str          # NATS subject to publish to when job fires
    payload: Dict[str, Any]      # arbitrary data forwarded in the event
    schedule: str                # 'once' | cron expression e.g. '*/5 * * * *'
    run_at: Optional[float]      # UTC epoch for one-shot jobs
    interval_seconds: Optional[float]  # for simple recurring (non-cron)
    enabled: bool = True
    last_run: Optional[float] = None
    next_run: Optional[float] = None
    run_count: int = 0
    created_at: float = field(default_factory=time.time)


# ── Job registry ──────────────────────────────────────────────────────────────

_jobs: Dict[str, Job] = {}
_jobs_lock = threading.Lock()

# Shared NATS connection
_nc: Any = None
_loop: Optional[asyncio.AbstractEventLoop] = None


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None:
        _loop = asyncio.new_event_loop()
        threading.Thread(target=_loop.run_forever, daemon=True).start()
    return _loop


# ── Cron parser (minimal — field-by-field) ────────────────────────────────────

def _cron_next(expr: str, after: float) -> Optional[float]:
    """
    Compute next fire time for a 5-field cron expression.
    Supports: * and */n and comma-separated values for each field.
    Returns None if the expression is invalid.
    """
    parts = expr.strip().split()
    if len(parts) != 5:
        return None

    def _field_matches(spec: str, value: int, min_v: int, max_v: int) -> bool:
        for part in spec.split(','):
            if part == '*':
                return True
            if part.startswith('*/'):
                try:
                    step = int(part[2:])
                    if step > 0 and (value - min_v) % step == 0:
                        return True
                except ValueError:
                    pass
            else:
                try:
                    if int(part) == value:
                        return True
                except ValueError:
                    pass
        return False

    # Walk forward minute-by-minute (capped at 1 year to prevent infinite loop)
    t = after + 60  # start one minute after
    t = (t // 60) * 60  # floor to minute
    limit = after + 366 * 24 * 3600

    while t < limit:
        dt = datetime.fromtimestamp(t, tz=timezone.utc)
        minute, hour, dom, month, dow = dt.minute, dt.hour, dt.day, dt.month, dt.weekday()
        # cron dow: 0=Sun…6=Sat; Python weekday: 0=Mon…6=Sun
        cron_dow = (dow + 1) % 7  # convert to cron convention

        if (_field_matches(parts[0], minute, 0, 59) and
                _field_matches(parts[1], hour, 0, 23) and
                _field_matches(parts[2], dom, 1, 31) and
                _field_matches(parts[3], month, 1, 12) and
                _field_matches(parts[4], cron_dow, 0, 6)):
            return t
        t += 60

    return None


def _compute_next_run(job: Job) -> Optional[float]:
    now = time.time()
    if job.schedule == 'once':
        return job.run_at if job.run_at and job.run_at > now else None
    if job.interval_seconds:
        base = job.last_run or now
        return base + job.interval_seconds
    return _cron_next(job.schedule, job.last_run or now)


# ── Job CRUD ──────────────────────────────────────────────────────────────────

def create_job(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create and register a new job.
    Required: name, target_subject, schedule
    Optional: payload, run_at (epoch), interval_seconds
    """
    name = data.get('name', '')
    target_subject = data.get('target_subject', '')
    schedule = data.get('schedule', '')

    if not name:
        return {'ok': False, 'error': 'name is required'}
    if not target_subject:
        return {'ok': False, 'error': 'target_subject is required'}
    if not schedule:
        return {'ok': False, 'error': 'schedule is required'}

    job_id = data.get('job_id') or str(uuid.uuid4())
    interval_s = data.get('interval_seconds')
    run_at = data.get('run_at')

    # Validate once-schedule requires run_at
    if schedule == 'once' and not run_at:
        return {'ok': False, 'error': 'run_at is required for schedule=once'}

    # Validate cron expression (non-special schedules that aren't 'once' or interval-based)
    if schedule not in ('once',) and interval_s is None:
        if _cron_next(schedule, time.time()) is None:
            return {'ok': False, 'error': f'invalid cron expression: {schedule!r}'}

    job = Job(
        job_id=job_id,
        name=name,
        target_subject=target_subject,
        payload=data.get('payload', {}),
        schedule=schedule,
        run_at=float(run_at) if run_at else None,
        interval_seconds=float(interval_s) if interval_s else None,
    )
    job.next_run = _compute_next_run(job)

    with _jobs_lock:
        _jobs[job_id] = job

    log.info('Created job %s (%s) next_run=%s', job_id, name,
             job.next_run and datetime.fromtimestamp(job.next_run, tz=timezone.utc).isoformat())
    return {'ok': True, 'job_id': job_id, 'next_run': job.next_run}


def cancel_job(job_id: str) -> Dict[str, Any]:
    with _jobs_lock:
        job = _jobs.pop(job_id, None)
    if job is None:
        return {'ok': False, 'error': f'job {job_id!r} not found'}
    log.info('Cancelled job %s', job_id)
    return {'ok': True, 'job_id': job_id}


def list_jobs() -> List[Dict[str, Any]]:
    with _jobs_lock:
        return [asdict(j) for j in _jobs.values()]


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _jobs_lock:
        j = _jobs.get(job_id)
    return asdict(j) if j else None


# ── Scheduler tick ────────────────────────────────────────────────────────────

def _fire_job(job: Job) -> None:
    """Fire a job: publish to NATS and update state."""
    job.last_run = time.time()
    job.run_count += 1

    envelope = {
        'connector': NAME,
        'job_id': job.job_id,
        'job_name': job.name,
        'payload': job.payload,
        'fired_at': datetime.now(timezone.utc).isoformat(),
        'run_count': job.run_count,
    }

    if _nc is not None:
        asyncio.run_coroutine_threadsafe(
            _nc.publish(job.target_subject, json.dumps(envelope).encode()),
            _get_loop(),
        )

    log.info('Fired job %s → %s (run #%s)', job.job_id, job.target_subject, job.run_count)

    # Compute next run or remove if one-shot
    if job.schedule == 'once':
        with _jobs_lock:
            _jobs.pop(job.job_id, None)
    else:
        job.next_run = _compute_next_run(job)


def _scheduler_tick() -> None:
    """Called every second to check for due jobs."""
    now = time.time()
    with _jobs_lock:
        due = [j for j in _jobs.values() if j.enabled and j.next_run and j.next_run <= now]

    for job in due:
        try:
            _fire_job(job)
        except Exception as exc:
            log.error('Error firing job %s: %s', job.job_id, exc)


def _scheduler_loop() -> None:
    """Background thread: tick every second."""
    while True:
        try:
            _scheduler_tick()
        except Exception as exc:
            log.error('Scheduler tick error: %s', exc)
        time.sleep(1)


# ── HTTP server ───────────────────────────────────────────────────────────────

class _SchedulerHandler(http.server.BaseHTTPRequestHandler):

    def _json(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> bytes:
        n = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(n) if n else b''

    def do_GET(self) -> None:
        path = self.path.split('?')[0].rstrip('/')
        if path == '/health':
            self._json(200, {
                'status': 'healthy', 'connector': NAME, 'version': VERSION,
                'port': PORT, 'jobs': len(_jobs),
                'uptime_seconds': round(time.time() - _start_time),
            })
        elif path == '/jobs':
            self._json(200, {'ok': True, 'jobs': list_jobs()})
        elif path.startswith('/jobs/'):
            job_id = path[len('/jobs/'):]
            j = get_job(job_id)
            if j:
                self._json(200, {'ok': True, 'job': j})
            else:
                self._json(404, {'error': f'job {job_id!r} not found'})
        else:
            self._json(404, {'error': 'not found'})

    def do_POST(self) -> None:
        path = self.path.split('?')[0].rstrip('/')
        if path == '/jobs':
            try:
                data = json.loads(self._body())
            except Exception:
                self._json(400, {'error': 'invalid JSON'})
                return
            result = create_job(data)
            self._json(201 if result['ok'] else 400, result)
        else:
            self._json(404, {'error': 'not found'})

    def do_DELETE(self) -> None:
        path = self.path.split('?')[0].rstrip('/')
        if path.startswith('/jobs/'):
            job_id = path[len('/jobs/'):]
            result = cancel_job(job_id)
            self._json(200 if result['ok'] else 404, result)
        else:
            self._json(404, {'error': 'not found'})

    def log_message(self, *_args: Any) -> None:
        pass


# ── NATS handler ──────────────────────────────────────────────────────────────

async def handle_event(nc, subject: str, raw: bytes) -> None:
    try:
        data = json.loads(raw)
    except Exception:
        log.error('Invalid JSON on %s', subject)
        return

    if subject.endswith('.create'):
        result = create_job(data)
        await nc.publish(
            f'cascadia.connectors.{NAME}.created',
            json.dumps({**result, 'connector': NAME,
                        'timestamp': datetime.now(timezone.utc).isoformat()}).encode(),
        )
    elif subject.endswith('.cancel'):
        job_id = data.get('job_id', '')
        result = cancel_job(job_id)
        await nc.publish(
            f'cascadia.connectors.{NAME}.cancelled',
            json.dumps({**result, 'connector': NAME,
                        'timestamp': datetime.now(timezone.utc).isoformat()}).encode(),
        )
    elif subject.endswith('.list'):
        await nc.publish(
            f'cascadia.connectors.{NAME}.jobs',
            json.dumps({'ok': True, 'jobs': list_jobs(), 'connector': NAME,
                        'timestamp': datetime.now(timezone.utc).isoformat()}).encode(),
        )


# ── Entry point ───────────────────────────────────────────────────────────────

def _start_http_server() -> None:
    server = http.server.HTTPServer(('', PORT), _SchedulerHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info('%s v%s health + jobs endpoint on port %s', NAME, VERSION, PORT)


async def main() -> None:
    global _nc
    _start_http_server()
    threading.Thread(target=_scheduler_loop, daemon=True).start()

    if not _NATS_AVAILABLE:
        log.warning('nats-py not installed — running in HTTP-only mode')
        await asyncio.sleep(float('inf'))
        return

    _nc = await nats.connect('nats://localhost:4222')
    await _nc.subscribe(
        f'cascadia.connectors.{NAME}.>',
        cb=lambda m: asyncio.create_task(handle_event(_nc, m.subject, m.data)),
    )
    log.info('%s connected to NATS', NAME)
    await asyncio.sleep(float('inf'))


if __name__ == '__main__':
    asyncio.run(main())
