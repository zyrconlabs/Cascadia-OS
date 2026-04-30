"""Tests for CON-115 Scheduler Connector."""
from __future__ import annotations

import asyncio
import json
import time
import threading
import urllib.request
import urllib.error
from http.server import HTTPServer
from unittest.mock import AsyncMock, MagicMock

import pytest

from cascadia.connectors.scheduler.connector import (
    NAME,
    VERSION,
    PORT,
    _jobs,
    _cron_next,
    _compute_next_run,
    create_job,
    cancel_job,
    list_jobs,
    get_job,
    _fire_job,
    _scheduler_tick,
    handle_event,
    _SchedulerHandler,
    Job,
)


def _clear():
    _jobs.clear()


# ── Cron parser ───────────────────────────────────────────────────────────────

def test_cron_every_minute():
    now = time.time()
    nxt = _cron_next('* * * * *', now)
    assert nxt is not None
    assert nxt > now
    assert nxt - now <= 120  # within 2 minutes


def test_cron_invalid_expression():
    assert _cron_next('not a cron', time.time()) is None


def test_cron_too_few_fields():
    assert _cron_next('* * *', time.time()) is None


def test_cron_step_expression():
    now = time.time()
    nxt = _cron_next('*/5 * * * *', now)
    assert nxt is not None
    assert nxt > now


def test_cron_specific_minute():
    # Find next occurrence of minute 0
    import datetime as dt
    ref = dt.datetime(2025, 1, 1, 12, 30, tzinfo=dt.timezone.utc).timestamp()
    nxt = _cron_next('0 * * * *', ref)
    assert nxt is not None
    nxt_dt = dt.datetime.fromtimestamp(nxt, tz=dt.timezone.utc)
    assert nxt_dt.minute == 0
    assert nxt_dt.hour == 13  # next top of hour


# ── create_job ────────────────────────────────────────────────────────────────

def test_create_job_once():
    _clear()
    run_at = time.time() + 300
    result = create_job({
        'name': 'test-once',
        'target_subject': 'cascadia.test',
        'schedule': 'once',
        'run_at': run_at,
        'payload': {'key': 'val'},
    })
    assert result['ok'] is True
    assert 'job_id' in result
    assert result['job_id'] in _jobs
    _clear()


def test_create_job_cron():
    _clear()
    result = create_job({
        'name': 'test-cron',
        'target_subject': 'cascadia.test',
        'schedule': '* * * * *',
    })
    assert result['ok'] is True
    _clear()


def test_create_job_interval():
    _clear()
    result = create_job({
        'name': 'heartbeat',
        'target_subject': 'cascadia.heartbeat',
        'schedule': 'interval',
        'interval_seconds': 60,
    })
    assert result['ok'] is True
    _clear()


def test_create_job_missing_name():
    result = create_job({'target_subject': 'x', 'schedule': '* * * * *'})
    assert result['ok'] is False
    assert 'name' in result['error']


def test_create_job_missing_subject():
    result = create_job({'name': 'x', 'schedule': '* * * * *'})
    assert result['ok'] is False
    assert 'target_subject' in result['error']


def test_create_job_missing_schedule():
    result = create_job({'name': 'x', 'target_subject': 'y'})
    assert result['ok'] is False
    assert 'schedule' in result['error']


def test_create_job_once_without_run_at():
    result = create_job({'name': 'x', 'target_subject': 'y', 'schedule': 'once'})
    assert result['ok'] is False
    assert 'run_at' in result['error']


def test_create_job_invalid_cron():
    result = create_job({'name': 'x', 'target_subject': 'y', 'schedule': 'bad cron'})
    assert result['ok'] is False
    assert 'cron' in result['error']


def test_create_job_custom_id():
    _clear()
    result = create_job({
        'name': 'custom',
        'target_subject': 'cascadia.test',
        'schedule': '* * * * *',
        'job_id': 'my-custom-id',
    })
    assert result['ok'] is True
    assert result['job_id'] == 'my-custom-id'
    _clear()


# ── cancel_job ────────────────────────────────────────────────────────────────

def test_cancel_existing_job():
    _clear()
    r = create_job({'name': 'tmp', 'target_subject': 'x', 'schedule': '* * * * *'})
    job_id = r['job_id']
    result = cancel_job(job_id)
    assert result['ok'] is True
    assert job_id not in _jobs


def test_cancel_nonexistent_job():
    result = cancel_job('no-such-id')
    assert result['ok'] is False
    assert 'not found' in result['error']


# ── list/get ──────────────────────────────────────────────────────────────────

def test_list_jobs():
    _clear()
    create_job({'name': 'j1', 'target_subject': 'x', 'schedule': '* * * * *'})
    create_job({'name': 'j2', 'target_subject': 'y', 'schedule': '* * * * *'})
    jobs = list_jobs()
    assert len(jobs) == 2
    names = {j['name'] for j in jobs}
    assert names == {'j1', 'j2'}
    _clear()


def test_get_job_found():
    _clear()
    r = create_job({'name': 'findme', 'target_subject': 'x', 'schedule': '* * * * *'})
    j = get_job(r['job_id'])
    assert j is not None
    assert j['name'] == 'findme'
    _clear()


def test_get_job_not_found():
    assert get_job('missing') is None


# ── _fire_job ─────────────────────────────────────────────────────────────────

def test_fire_job_increments_run_count():
    _clear()
    job = Job(
        job_id='fire-test', name='test', target_subject='x',
        payload={}, schedule='* * * * *', run_at=None, interval_seconds=None,
    )
    _jobs['fire-test'] = job
    _fire_job(job)
    assert job.run_count == 1
    assert job.last_run is not None
    _clear()


def test_fire_once_job_removes_itself():
    _clear()
    run_at = time.time() - 1  # past
    job = Job(
        job_id='once-fire', name='once', target_subject='x',
        payload={}, schedule='once', run_at=run_at, interval_seconds=None,
    )
    _jobs['once-fire'] = job
    _fire_job(job)
    assert 'once-fire' not in _jobs
    _clear()


# ── _scheduler_tick ───────────────────────────────────────────────────────────

def test_scheduler_tick_fires_due_job():
    _clear()
    job = Job(
        job_id='tick-test', name='tick', target_subject='x',
        payload={}, schedule='* * * * *', run_at=None, interval_seconds=None,
        next_run=time.time() - 1,  # overdue
    )
    _jobs['tick-test'] = job
    _scheduler_tick()
    assert job.run_count == 1
    _clear()


def test_scheduler_tick_skips_future_job():
    _clear()
    job = Job(
        job_id='future', name='future', target_subject='x',
        payload={}, schedule='* * * * *', run_at=None, interval_seconds=None,
        next_run=time.time() + 9999,
    )
    _jobs['future'] = job
    _scheduler_tick()
    assert job.run_count == 0
    _clear()


def test_scheduler_tick_skips_disabled_job():
    _clear()
    job = Job(
        job_id='disabled', name='disabled', target_subject='x',
        payload={}, schedule='* * * * *', run_at=None, interval_seconds=None,
        next_run=time.time() - 1,
        enabled=False,
    )
    _jobs['disabled'] = job
    _scheduler_tick()
    assert job.run_count == 0
    _clear()


# ── HTTP server ───────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def sched_server():
    server = HTTPServer(('127.0.0.1', 0), _SchedulerHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{port}'
    server.shutdown()


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 method='POST',
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _get(url):
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _delete(url):
    req = urllib.request.Request(url, method='DELETE')
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http_health(sched_server):
    status, body = _get(f'{sched_server}/health')
    assert status == 200
    assert body['connector'] == NAME


def test_http_create_job(sched_server):
    _clear()
    status, body = _post(f'{sched_server}/jobs', {
        'name': 'http-job',
        'target_subject': 'cascadia.test',
        'schedule': '* * * * *',
    })
    assert status == 201
    assert body['ok'] is True
    _clear()


def test_http_create_job_bad_request(sched_server):
    status, body = _post(f'{sched_server}/jobs', {'schedule': '* * * * *'})
    assert status == 400
    assert body['ok'] is False


def test_http_list_jobs(sched_server):
    _clear()
    create_job({'name': 'list1', 'target_subject': 'x', 'schedule': '* * * * *'})
    status, body = _get(f'{sched_server}/jobs')
    assert status == 200
    assert len(body['jobs']) >= 1
    _clear()


def test_http_get_job(sched_server):
    _clear()
    r = create_job({'name': 'get-me', 'target_subject': 'x', 'schedule': '* * * * *'})
    status, body = _get(f'{sched_server}/jobs/{r["job_id"]}')
    assert status == 200
    assert body['job']['name'] == 'get-me'
    _clear()


def test_http_get_job_not_found(sched_server):
    status, body = _get(f'{sched_server}/jobs/no-such-id')
    assert status == 404


def test_http_delete_job(sched_server):
    _clear()
    r = create_job({'name': 'del-me', 'target_subject': 'x', 'schedule': '* * * * *'})
    status, body = _delete(f'{sched_server}/jobs/{r["job_id"]}')
    assert status == 200
    assert body['ok'] is True
    _clear()


def test_http_delete_job_not_found(sched_server):
    status, body = _delete(f'{sched_server}/jobs/nonexistent')
    assert status == 404


# ── NATS handler ──────────────────────────────────────────────────────────────

def test_nats_create_job():
    _clear()
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append((subject, json.loads(payload)))

    nc.publish = mock_publish

    payload = json.dumps({
        'name': 'nats-job',
        'target_subject': 'cascadia.nats.test',
        'schedule': '* * * * *',
    }).encode()
    asyncio.run(handle_event(nc, f'cascadia.connectors.{NAME}.create', payload))

    assert any('created' in s for s, _ in published)
    _clear()


def test_nats_cancel_job():
    _clear()
    r = create_job({'name': 'cancel-me', 'target_subject': 'x', 'schedule': '* * * * *'})
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append((subject, json.loads(payload)))

    nc.publish = mock_publish

    payload = json.dumps({'job_id': r['job_id']}).encode()
    asyncio.run(handle_event(nc, f'cascadia.connectors.{NAME}.cancel', payload))

    assert any('cancelled' in s for s, _ in published)
    assert r['job_id'] not in _jobs
    _clear()


def test_nats_list_jobs():
    _clear()
    create_job({'name': 'listed', 'target_subject': 'x', 'schedule': '* * * * *'})
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append((subject, json.loads(payload)))

    nc.publish = mock_publish

    asyncio.run(handle_event(nc, f'cascadia.connectors.{NAME}.list', b'{}'))
    assert any('jobs' in s for s, _ in published)
    _clear()


def test_nats_invalid_json():
    nc = MagicMock()
    nc.publish = AsyncMock()
    asyncio.run(handle_event(nc, f'cascadia.connectors.{NAME}.create', b'not json'))
    nc.publish.assert_not_called()


# ── Metadata ──────────────────────────────────────────────────────────────────

def test_connector_metadata():
    assert NAME == 'scheduler-connector'
    assert VERSION == '1.0.0'
    assert PORT == 9987
