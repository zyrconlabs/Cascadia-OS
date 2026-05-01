"""
tests/test_generic_alert.py
Tests for Generic Alert Operator (port 8304).
"""
import json
import threading
from http.server import HTTPServer
from urllib.request import Request, urlopen

import pytest

from cascadia.operators.generic_alert.server import (
    AlertStore,
    GenericAlert,
    _Handler,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    return AlertStore(db_path=str(tmp_path / "alerts.db"))


@pytest.fixture
def ga(store):
    return GenericAlert(store=store)


@pytest.fixture
def server(tmp_path):
    """Spin up the HTTP server on an ephemeral port for HTTP-layer tests."""
    store = AlertStore(db_path=str(tmp_path / "http_alerts.db"))

    class _TestHandler(_Handler):
        pass

    # Patch the module-level _ga used by _Handler with a fresh store instance
    import cascadia.operators.generic_alert.server as mod
    original = mod._ga
    mod._ga = GenericAlert(store=store)

    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    mod._ga = original


def _post(base_url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = Request(f"{base_url}/api/run", data=data,
                  headers={"Content-Type": "application/json"})
    with urlopen(req) as resp:
        return json.loads(resp.read())


# ── Unit tests ────────────────────────────────────────────────────────────────

def test_health_returns_ok():
    from cascadia.operators.generic_alert.health import check
    result = check()
    assert result["status"] == "healthy"
    assert result["component"] == "generic_alert"
    assert result["port"] == 8304


def test_create_alert_creates_approval_request(ga):
    result = ga.create_alert(
        device_id="sensor-001",
        metric="temperature",
        current_value=95.0,
        threshold=90.0,
        severity="warning",
        device_name="Boiler Sensor",
        operator_str="gt",
        action="approval_required",
    )
    assert result["approval_required"] is True
    assert "approval_message" in result
    assert result["status"] == "pending"
    assert result["severity"] == "warning"


def test_critical_alert_always_requires_approval(ga):
    # Critical should ALWAYS be approval_required=True, regardless of action
    result = ga.create_alert(
        device_id="sensor-002",
        metric="pressure",
        current_value=200.0,
        threshold=150.0,
        severity="critical",
        action="auto_resolve",   # even if action says auto_resolve
    )
    assert result["approval_required"] is True
    assert result["status"] == "pending"


def test_info_alert_auto_resolves_if_configured(ga):
    result = ga.create_alert(
        device_id="sensor-003",
        metric="humidity",
        current_value=55.0,
        threshold=50.0,
        severity="info",
        action="auto_resolve",
    )
    assert result.get("approval_required") is False
    assert result["status"] == "resolved"


def test_list_alerts_returns_list(ga):
    # No alerts yet
    alerts = ga.list_alerts()
    assert isinstance(alerts, list)

    # Add an alert then check again
    ga.create_alert("d1", "temp", 100, 90, "warning")
    alerts = ga.list_alerts()
    assert len(alerts) >= 1
    assert isinstance(alerts[0], dict)


def test_configure_rule_requires_approval(ga):
    result = ga.configure_rule(
        device_id="device-001",
        metric="voltage",
        operator_str="gt",
        threshold=240.0,
        severity="warning",
        action="approval_required",
    )
    assert result["approval_required"] is True
    assert "device-001" in result["approval_message"]
    assert "voltage" in result["approval_message"]


def test_delete_rule_requires_approval(ga):
    result = ga.delete_rule("rule-abc-123")
    assert result["approval_required"] is True
    assert "rule-abc-123" in result["approval_message"]
    assert result["pending_action"] == "delete_rule"


def test_resolve_alert_requires_approval(ga):
    # First create an alert to get an ID
    created = ga.create_alert("d1", "temp", 100, 80, "warning")
    alert_id = created["alert_id"]

    result = ga.resolve_alert(alert_id, resolution_notes="Manual check done")
    assert result["approval_required"] is True
    assert alert_id in result["approval_message"]
    assert result["pending_action"] == "resolve_alert"
