"""
tests/test_iot_registry.py
Tests for device_registry — IoT device registry (port 8301).
"""
import json
import tempfile
import threading
from http.server import HTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from cascadia.iot.device_registry import _Handler, DeviceStore


# ── Unit tests (DeviceStore) ──────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    return DeviceStore(db_path=str(tmp_path / "test_devices.db"))


def _device(**overrides):
    base = {
        "device_id": "sensor-001",
        "name": "Barn Temperature Sensor",
        "type": "temperature",
        "location": "Barn North Wall",
        "alert_threshold_min": 35.0,
        "alert_threshold_max": 95.0,
        "unit": "fahrenheit",
    }
    base.update(overrides)
    return base


def test_register_new_device(store):
    d = store.register(_device())
    assert d["device_id"] == "sensor-001"
    assert d["name"] == "Barn Temperature Sensor"
    assert d["registered_at"] is not None


def test_list_devices_returns_registered_device(store):
    store.register(_device())
    devices = store.list_all()
    assert len(devices) == 1
    assert devices[0]["device_id"] == "sensor-001"


def test_get_device_detail(store):
    store.register(_device())
    d = store.get("sensor-001")
    assert d is not None
    assert d["location"] == "Barn North Wall"
    assert d["alert_threshold_min"] == 35.0
    assert d["alert_threshold_max"] == 95.0


def test_get_nonexistent_device_returns_none(store):
    assert store.get("no-such-device") is None


def test_update_threshold_config(store):
    store.register(_device())
    updated = store.update("sensor-001", {
        "alert_threshold_min": 40.0,
        "alert_threshold_max": 100.0,
    })
    assert updated["alert_threshold_min"] == 40.0
    assert updated["alert_threshold_max"] == 100.0
    assert updated["name"] == "Barn Temperature Sensor"


def test_update_name(store):
    store.register(_device())
    updated = store.update("sensor-001", {"name": "Barn Temp Sensor v2"})
    assert updated["name"] == "Barn Temp Sensor v2"


def test_update_nonexistent_device_returns_none(store):
    result = store.update("no-such", {"name": "x"})
    assert result is None


def test_last_seen_updates(store):
    store.register(_device())
    d_before = store.get("sensor-001")
    assert d_before["last_seen"] is None

    store.touch_last_seen("sensor-001")
    d_after = store.get("sensor-001")
    assert d_after["last_seen"] is not None


def test_deregister_writes_to_suggestions_not_deletes(store, tmp_path):
    store.register(_device())
    store.deregister_note("sensor-001")
    # Device still exists in DB — deregister does not auto-delete
    d = store.get("sensor-001")
    assert d is not None


def test_register_multiple_devices(store):
    store.register(_device(device_id="s1", name="Sensor 1"))
    store.register(_device(device_id="s2", name="Sensor 2", type="humidity"))
    devices = store.list_all()
    ids = {d["device_id"] for d in devices}
    assert "s1" in ids
    assert "s2" in ids


def test_register_upsert_updates_existing(store):
    store.register(_device(name="Old Name"))
    store.register(_device(name="New Name"))
    d = store.get("sensor-001")
    assert d["name"] == "New Name"


# ── Integration tests (live HTTP server) ──────────────────────────────────────

@pytest.fixture(scope="module")
def registry_server(tmp_path_factory):
    db = str(tmp_path_factory.mktemp("db") / "devices.db")
    import cascadia.iot.device_registry as reg_mod
    original_store = reg_mod._store
    reg_mod._store = DeviceStore(db_path=db)

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    reg_mod._store = original_store


def _post(url, body):
    data = json.dumps(body).encode()
    req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


def _put(url, body):
    data = json.dumps(body).encode()
    req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="PUT")
    try:
        with urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


def _get(url):
    with urlopen(url, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


def test_http_register_device(registry_server):
    status, body = _post(f"{registry_server}/iot/devices/register", _device())
    assert status == 201
    assert body["device_id"] == "sensor-001"


def test_http_list_devices(registry_server):
    status, body = _get(f"{registry_server}/iot/devices")
    assert status == 200
    assert "devices" in body
    assert any(d["device_id"] == "sensor-001" for d in body["devices"])


def test_http_get_device_detail(registry_server):
    status, body = _get(f"{registry_server}/iot/devices/sensor-001")
    assert status == 200
    assert body["device_id"] == "sensor-001"


def test_http_get_missing_device_returns_404(registry_server):
    try:
        _get(f"{registry_server}/iot/devices/no-such")
        assert False, "should have raised"
    except HTTPError as e:
        assert e.code == 404


def test_http_update_threshold(registry_server):
    status, body = _put(
        f"{registry_server}/iot/devices/sensor-001",
        {"alert_threshold_max": 99.0}
    )
    assert status == 200
    assert body["alert_threshold_max"] == 99.0


def test_http_deregister_does_not_delete(registry_server):
    status, body = _post(f"{registry_server}/iot/devices/sensor-001/deregister", {})
    assert status == 200
    assert body["ok"] is True
    # Device still accessible
    status2, _ = _get(f"{registry_server}/iot/devices/sensor-001")
    assert status2 == 200


def test_http_health(registry_server):
    status, body = _get(f"{registry_server}/iot/health")
    assert status == 200
    assert body["status"] == "healthy"
    assert body["component"] == "device_registry"
