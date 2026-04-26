"""
tests/test_sensor_store.py
Tests for SensorStore — Task 8.
"""
import time
import pytest
from pathlib import Path


@pytest.fixture
def store(tmp_path):
    from cascadia.iot.sensor_store import SensorStore
    db = str(tmp_path / 'sensors.db')
    return SensorStore(db)


def test_record_and_query(store):
    """record() stores a reading and query() returns it."""
    store.record('device_a', 'device_a/temp', {'temperature': 25.0}, value=25.0)
    results = store.query('device_a', hours=24)
    assert len(results) == 1
    assert results[0]['device_id'] == 'device_a'
    assert results[0]['value'] == 25.0


def test_query_filters_by_time(store, tmp_path):
    """query() returns empty when all readings are outside the time window."""
    from cascadia.iot.sensor_store import SensorStore
    import sqlite3
    from datetime import datetime, timezone, timedelta

    db_path = str(tmp_path / 'sensors_old.db')
    s = SensorStore(db_path)

    # Manually insert an old reading
    old_time = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            'INSERT INTO sensor_readings (device_id, topic, value, payload, recorded_at) VALUES (?, ?, ?, ?, ?)',
            ('old_device', 'old/topic', 10.0, '{}', old_time),
        )

    # Query last 24 hours — should be empty
    results = s.query('old_device', hours=24)
    assert len(results) == 0


def test_query_empty_when_no_readings(store):
    """query() returns empty list when no readings exist."""
    results = store.query('nonexistent', hours=24)
    assert results == []


def test_latest_returns_most_recent(store):
    """latest() returns the most recent reading for a device."""
    store.record('dev_b', 'dev_b/temp', {'temperature': 20.0}, value=20.0)
    time.sleep(0.01)
    store.record('dev_b', 'dev_b/temp', {'temperature': 25.0}, value=25.0)
    latest = store.latest('dev_b')
    assert latest is not None
    assert latest['value'] == 25.0


def test_list_devices(store):
    """list_devices() returns all devices with readings."""
    store.record('alpha', 'alpha/temp', {}, value=1.0)
    store.record('beta', 'beta/temp', {}, value=2.0)
    store.record('alpha', 'alpha/temp', {}, value=3.0)
    devices = store.list_devices()
    assert 'alpha' in devices
    assert 'beta' in devices
    assert len(devices) == 2


def test_purge_old(tmp_path):
    """purge_old() deletes readings older than retention_days."""
    from cascadia.iot.sensor_store import SensorStore
    import sqlite3
    from datetime import datetime, timezone, timedelta

    db_path = str(tmp_path / 'purge_test.db')
    s = SensorStore(db_path)

    # Insert old reading directly
    old_time = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            'INSERT INTO sensor_readings (device_id, topic, value, payload, recorded_at) VALUES (?, ?, ?, ?, ?)',
            ('purge_dev', 'purge/topic', 1.0, '{}', old_time),
        )

    # Insert a recent reading
    s.record('purge_dev', 'purge/topic', {}, value=2.0)

    count = s.purge_old(retention_days=90)
    assert count == 1

    # Only recent reading should remain
    results = s.query('purge_dev', hours=24)
    assert len(results) == 1
