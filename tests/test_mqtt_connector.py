"""
tests/test_mqtt_connector.py
Tests for MQTT Connector (port 8305).
"""
import json
import threading
from http.server import HTTPServer
from urllib.request import Request, urlopen

import pytest

from cascadia.connectors.mqtt.server import (
    MqttConnector,
    MqttStore,
    _is_command_topic,
    _Handler,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store():
    return MqttStore()


@pytest.fixture
def mc(store):
    return MqttConnector(store=store)


@pytest.fixture
def server():
    """Spin up the HTTP server on an ephemeral port for HTTP-layer tests."""
    import cascadia.connectors.mqtt.server as mod
    original = mod._mc
    mod._mc = MqttConnector(store=MqttStore())

    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    mod._mc = original


def _post(base_url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = Request(f"{base_url}/api/run", data=data,
                  headers={"Content-Type": "application/json"})
    with urlopen(req) as resp:
        return json.loads(resp.read())


# ── Unit tests ────────────────────────────────────────────────────────────────

def test_health_returns_ok(server):
    with urlopen(f"{server}/api/health") as resp:
        result = json.loads(resp.read())
    assert result["status"] == "healthy"
    assert result["component"] == "mqtt"
    assert result["port"] == 8305


def test_simulated_mode_no_broker(mc):
    """connect() succeeds without a real broker and returns simulated=True."""
    result = mc.connect(broker_host="broker.example.com", broker_port=1883)
    assert result["connected"] is True
    assert result["simulated"] is True
    assert result["broker"] == "broker.example.com"


def test_subscribe_stores_topic(mc):
    result = mc.subscribe("sensors/temp")
    assert result["subscribed"] is True
    assert "sensors/temp" in mc.list_subscriptions()


def test_publish_data_allowed_without_approval(mc):
    """Data topics do not require approval."""
    result = mc.publish("sensors/temperature", payload={"value": 72.4})
    assert result.get("approval_required") is not True
    assert result["published"] is True
    assert result["simulated"] is True


def test_publish_command_requires_approval(mc):
    """Topics containing command words require approval."""
    result = mc.publish("command/hvac/set", payload={"state": "on"})
    assert result["approval_required"] is True
    assert "approval_message" in result
    assert result["pending_action"] == "mqtt_publish"

    # Also test "control" segment
    result2 = mc.publish("devices/control", payload={"relay": 1})
    assert result2["approval_required"] is True


def test_get_latest_message_returns_data(mc):
    mc.store.store_message("sensors/humidity", {"value": 65}, qos=0)
    result = mc.get_latest_message("sensors/humidity")
    assert result["topic"] == "sensors/humidity"
    assert result["message"] is not None
    assert result["message"]["payload"]["value"] == 65


def test_list_subscriptions_returns_list(mc):
    subscriptions = mc.list_subscriptions()
    assert isinstance(subscriptions, list)

    mc.subscribe("sensors/power")
    mc.subscribe("sensors/water")
    subscriptions = mc.list_subscriptions()
    assert "sensors/power" in subscriptions
    assert "sensors/water" in subscriptions


def test_normalized_response_shape(server):
    """Health check response has the required shape."""
    with urlopen(f"{server}/api/health") as resp:
        result = json.loads(resp.read())
    assert "status" in result
    assert "component" in result
    assert "port" in result


def test_is_command_topic_pure_function():
    """_is_command_topic correctly classifies topics."""
    assert _is_command_topic("command/hvac") is True
    assert _is_command_topic("devices/control/relay") is True
    assert _is_command_topic("sensors/temperature") is False
    assert _is_command_topic("data/readings/humidity") is False
    assert _is_command_topic("actuate/pump/1") is True
    assert _is_command_topic("COMMAND/unit") is True   # case-insensitive
