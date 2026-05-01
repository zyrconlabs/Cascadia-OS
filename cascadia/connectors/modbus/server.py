"""
cascadia/connectors/modbus/server.py
Modbus Connector — port 8306 (env: MODBUS_CONNECTOR_PORT)
Connects Cascadia to PLCs and industrial equipment via Modbus TCP.
ALL equipment writes require approval — no exceptions.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("MODBUS_CONNECTOR_PORT", 8306))


class ModbusSimulator:
    """Simulated Modbus register bank with realistic defaults."""

    def __init__(self):
        # Digital outputs — unit/pump states
        self.coils: dict[int, bool] = {0: False, 1: True, 2: False, 3: False}
        # Digital inputs — limit switches (read-only)
        self.discrete_inputs: dict[int, bool] = {0: True, 1: False, 2: True}
        # Analog setpoints
        self.holding_registers: dict[int, int] = {0: 750, 1: 600, 2: 100, 3: 0}
        # Sensor readings (scaled x10)
        self.input_registers: dict[int, int] = {0: 723, 1: 245, 2: 870, 3: 32}

    def read_coils(self, address: int, count: int) -> list[bool]:
        return [self.coils.get(address + i, False) for i in range(count)]

    def read_discrete_inputs(self, address: int, count: int) -> list[bool]:
        return [self.discrete_inputs.get(address + i, False) for i in range(count)]

    def read_holding_registers(self, address: int, count: int) -> list[int]:
        return [self.holding_registers.get(address + i, 0) for i in range(count)]

    def read_input_registers(self, address: int, count: int) -> list[int]:
        return [self.input_registers.get(address + i, 0) for i in range(count)]


class ModbusConnector:
    def __init__(self, simulator: ModbusSimulator = None):
        self.simulator = simulator or ModbusSimulator()

    def connect(self, host: str, port: int = 502, unit_id: int = 1) -> dict:
        return {"connected": True, "simulated": True, "host": host, "unit_id": unit_id}

    def disconnect(self) -> dict:
        return {"connected": False}

    def read_coils(self, address: int, count: int = 1) -> dict:
        return {
            "values": self.simulator.read_coils(address, count),
            "address": address,
            "count": count,
        }

    def read_discrete_inputs(self, address: int, count: int = 1) -> dict:
        return {
            "values": self.simulator.read_discrete_inputs(address, count),
            "address": address,
            "count": count,
        }

    def read_holding_registers(self, address: int, count: int = 1) -> dict:
        return {
            "values": self.simulator.read_holding_registers(address, count),
            "address": address,
            "count": count,
        }

    def read_input_registers(self, address: int, count: int = 1) -> dict:
        return {
            "values": self.simulator.read_input_registers(address, count),
            "address": address,
            "count": count,
        }

    def write_coil(self, address: int, value: bool) -> dict:
        return {
            "approval_required": True,
            "approval_message": (
                f"Write coil {address}={value} on Modbus device — "
                "industrial equipment write requires approval"
            ),
            "pending_action": "modbus_write_coil",
            "address": address,
            "value": value,
        }

    def write_register(self, address: int, value: int) -> dict:
        return {
            "approval_required": True,
            "approval_message": (
                f"Write register {address}={value} on Modbus device — "
                "industrial equipment write requires approval"
            ),
            "pending_action": "modbus_write_register",
            "address": address,
            "value": value,
        }

    def write_multiple_registers(self, address: int, values: list) -> dict:
        return {
            "approval_required": True,
            "approval_message": (
                f"Write {len(values)} registers starting at {address} — "
                "industrial equipment write requires approval"
            ),
            "pending_action": "modbus_write_multiple_registers",
            "address": address,
            "values": values,
        }

    def get_device_info(self, unit_id: int = 1) -> dict:
        return {
            "unit_id": unit_id,
            "simulated": True,
            "protocol": "Modbus TCP",
            "registers": {
                "coils": 4,
                "discrete_inputs": 3,
                "holding_registers": 4,
                "input_registers": 4,
            },
        }


# ── HTTP layer ────────────────────────────────────────────────────────────────

_mb = ModbusConnector()

HEALTH = {"status": "healthy", "component": "modbus", "port": PORT}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, code: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/health":
            self._send(200, HEALTH)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/run":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        action = body.get("action", "")
        try:
            result = _dispatch(action, body)
            self._send(200, result)
        except Exception as exc:
            self._send(400, {"error": str(exc)})


def _dispatch(action: str, body: dict) -> dict:
    if action == "connect":
        return _mb.connect(
            host=body["host"],
            port=body.get("port", 502),
            unit_id=body.get("unit_id", 1),
        )
    if action == "disconnect":
        return _mb.disconnect()
    if action == "read_coils":
        return _mb.read_coils(body["address"], body.get("count", 1))
    if action == "read_discrete_inputs":
        return _mb.read_discrete_inputs(body["address"], body.get("count", 1))
    if action == "read_holding_registers":
        return _mb.read_holding_registers(body["address"], body.get("count", 1))
    if action == "read_input_registers":
        return _mb.read_input_registers(body["address"], body.get("count", 1))
    if action == "write_coil":
        return _mb.write_coil(body["address"], body["value"])
    if action == "write_register":
        return _mb.write_register(body["address"], body["value"])
    if action == "write_multiple_registers":
        return _mb.write_multiple_registers(body["address"], body["values"])
    if action == "get_device_info":
        return _mb.get_device_info(unit_id=body.get("unit_id", 1))
    raise ValueError(f"Unknown action: {action}")


def main():
    server = HTTPServer(("0.0.0.0", PORT), _Handler)
    print(f"Modbus Connector listening on port {PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
