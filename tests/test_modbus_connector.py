"""
tests/test_modbus_connector.py
Tests for Modbus Connector (port 8306).
"""
import json
import threading
from http.server import HTTPServer
from urllib.request import Request, urlopen

import pytest

from cascadia.connectors.modbus.server import (
    ModbusConnector,
    ModbusSimulator,
    _Handler,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def simulator():
    return ModbusSimulator()


@pytest.fixture
def mb(simulator):
    return ModbusConnector(simulator=simulator)


@pytest.fixture
def server():
    """Spin up the HTTP server on an ephemeral port for HTTP-layer tests."""
    import cascadia.connectors.modbus.server as mod
    original = mod._mb
    mod._mb = ModbusConnector(simulator=ModbusSimulator())

    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    mod._mb = original


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
    assert result["component"] == "modbus"
    assert result["port"] == 8306


def test_simulated_mode_no_device(mb):
    """connect() returns connected=True and simulated=True without a real device."""
    result = mb.connect(host="192.168.1.100", port=502, unit_id=1)
    assert result["connected"] is True
    assert result["simulated"] is True
    assert result["host"] == "192.168.1.100"
    assert result["unit_id"] == 1


def test_read_coils_returns_list(mb):
    result = mb.read_coils(address=0, count=4)
    assert "values" in result
    assert isinstance(result["values"], list)
    assert len(result["values"]) == 4
    # Check seeded defaults: coils[0]=False, coils[1]=True
    assert result["values"][0] is False
    assert result["values"][1] is True


def test_read_holding_registers_returns_values(mb):
    result = mb.read_holding_registers(address=0, count=4)
    assert "values" in result
    assert isinstance(result["values"], list)
    assert all(isinstance(v, int) for v in result["values"])
    # Seeded: {0: 750, 1: 600, 2: 100, 3: 0}
    assert result["values"][0] == 750


def test_read_input_registers_returns_values(mb):
    result = mb.read_input_registers(address=0, count=4)
    assert "values" in result
    assert isinstance(result["values"], list)
    assert all(isinstance(v, int) for v in result["values"])
    # Seeded: {0: 723, 1: 245, 2: 870, 3: 32}
    assert result["values"][0] == 723


def test_write_coil_requires_approval(mb):
    result = mb.write_coil(address=0, value=True)
    assert result["approval_required"] is True
    assert "approval_message" in result
    assert result["pending_action"] == "modbus_write_coil"


def test_write_register_requires_approval(mb):
    result = mb.write_register(address=1, value=750)
    assert result["approval_required"] is True
    assert "approval_message" in result
    assert result["pending_action"] == "modbus_write_register"


def test_write_multiple_registers_requires_approval(mb):
    result = mb.write_multiple_registers(address=0, values=[750, 600, 100])
    assert result["approval_required"] is True
    assert "3 registers" in result["approval_message"]
    assert result["pending_action"] == "modbus_write_multiple_registers"


def test_write_never_auto_approved(mb):
    """Writes always require approval — no way to bypass."""
    # Even calling write directly with any combination always returns approval_required
    for addr in range(4):
        for val in [True, False]:
            result = mb.write_coil(address=addr, value=val)
            assert result["approval_required"] is True, (
                f"write_coil({addr}, {val}) should always require approval"
            )
        for val in [0, 100, 999]:
            result = mb.write_register(address=addr, value=val)
            assert result["approval_required"] is True, (
                f"write_register({addr}, {val}) should always require approval"
            )


def test_normalized_response_shape(server):
    """Health response contains required status/component/port keys."""
    with urlopen(f"{server}/api/health") as resp:
        result = json.loads(resp.read())
    assert "status" in result
    assert "component" in result
    assert "port" in result
