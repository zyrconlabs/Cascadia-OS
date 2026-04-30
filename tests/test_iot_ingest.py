"""
tests/test_iot_ingest.py
Tests for sensor_ingest — IoT HTTP ingest endpoint (port 8300).
"""
import json
import threading
from http.server import HTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from cascadia.iot.sensor_ingest import (
    _Handler,
    _normalize_timestamp,
    _nats_subject,
    _validate_reading,
    VALID_SENSOR_TYPES,
)


# ── Unit tests ────────────────────────────────────────────────────────────────

def test_valid_temperature_reading_passes_validation():
    body = {
        "device_id": "sensor-001",
        "sensor_type": "temperature",
        "value": 72.4,
        "unit": "fahrenheit",
        "timestamp": "2026-04-30T10:00:00Z",
    }
    assert _validate_reading(body) is None


def test_missing_device_id_rejected():
    body = {"sensor_type": "temperature", "value": 72.4, "unit": "fahrenheit"}
    err = _validate_reading(body)
    assert err is not None
    assert "device_id" in err


def test_missing_sensor_type_rejected():
    body = {"device_id": "s1", "value": 72.4, "unit": "fahrenheit"}
    err = _validate_reading(body)
    assert err is not None
    assert "sensor_type" in err


def test_invalid_sensor_type_rejected():
    body = {"device_id": "s1", "sensor_type": "unknown_type", "value": 1.0, "unit": "x"}
    err = _validate_reading(body)
    assert err is not None
    assert "sensor_type" in err or "invalid" in err.lower()


def test_missing_value_rejected():
    body = {"device_id": "s1", "sensor_type": "temperature", "unit": "fahrenheit"}
    assert _validate_reading(body) is not None


def test_missing_unit_rejected():
    body = {"device_id": "s1", "sensor_type": "temperature", "value": 22.0}
    assert _validate_reading(body) is not None


def test_all_valid_sensor_types_accepted():
    for st in VALID_SENSOR_TYPES:
        body = {"device_id": "s1", "sensor_type": st, "value": 1.0, "unit": "u"}
        assert _validate_reading(body) is None, f"sensor_type '{st}' should be valid"


def test_nats_subject_correctly_formed():
    subj = _nats_subject("temperature", "sensor-abc")
    assert subj == "cascadia.iot.sensor.temperature.sensor-abc"


def test_nats_subject_all_types():
    for st in VALID_SENSOR_TYPES:
        subj = _nats_subject(st, "dev-1")
        assert subj == f"cascadia.iot.sensor.{st}.dev-1"


def test_timestamp_normalization_utc():
    ts = _normalize_timestamp("2026-04-30T10:00:00Z")
    assert "2026-04-30" in ts
    assert ts.endswith("Z")


def test_timestamp_normalization_offset():
    ts = _normalize_timestamp("2026-04-30T06:00:00-04:00")
    assert "2026-04-30" in ts
    assert "10:00:00" in ts


def test_timestamp_none_returns_now():
    ts = _normalize_timestamp(None)
    assert ts.endswith("Z")
    assert len(ts) > 0


# ── Integration tests (live HTTP server) ──────────────────────────────────────

@pytest.fixture(scope="module")
def ingest_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def _post(url, body):
    data = json.dumps(body).encode()
    req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


def _get(url):
    with urlopen(url, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


def test_health_endpoint_returns_200(ingest_server):
    status, body = _get(f"{ingest_server}/iot/health")
    assert status == 200
    assert body["status"] == "healthy"
    assert body["component"] == "sensor_ingest"


def test_valid_reading_accepted(ingest_server):
    reading = {
        "device_id": "barn-temp-01",
        "sensor_type": "temperature",
        "value": 68.5,
        "unit": "fahrenheit",
        "timestamp": "2026-04-30T10:00:00Z",
    }
    status, body = _post(f"{ingest_server}/iot/sensor/reading", reading)
    assert status == 200
    assert body["accepted"] is True
    assert "cascadia.iot.sensor.temperature.barn-temp-01" in body["subject"]


def test_missing_device_id_returns_400(ingest_server):
    reading = {"sensor_type": "humidity", "value": 55.0, "unit": "percent"}
    status, body = _post(f"{ingest_server}/iot/sensor/reading", reading)
    assert status == 400
    assert "error" in body


def test_invalid_sensor_type_returns_400(ingest_server):
    reading = {
        "device_id": "s1",
        "sensor_type": "xray",
        "value": 1.0,
        "unit": "mSv",
    }
    status, body = _post(f"{ingest_server}/iot/sensor/reading", reading)
    assert status == 400
    assert "error" in body


def test_sensor_types_endpoint(ingest_server):
    status, body = _get(f"{ingest_server}/iot/sensor/types")
    assert status == 200
    assert "sensor_types" in body
    assert "temperature" in body["sensor_types"]
