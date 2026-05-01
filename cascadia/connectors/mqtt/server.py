"""
cascadia/connectors/mqtt/server.py
MQTT Connector — port 8305 (env: MQTT_CONNECTOR_PORT)
Connects Cascadia to any MQTT broker. Command topics require approval.
"""
import json
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("MQTT_CONNECTOR_PORT", 8305))

_COMMAND_WORDS = {"command", "control", "set", "actuate", "override"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_command_topic(topic: str) -> bool:
    """Return True if any path segment is a known command word."""
    return bool(_COMMAND_WORDS.intersection(topic.lower().split("/")))


class MqttStore:
    def __init__(self, max_history_per_topic: int = 100):
        self.max_history_per_topic = max_history_per_topic
        self._lock = threading.Lock()
        self.subscriptions: set = set()
        self.messages: dict = {}

    def subscribe(self, topic: str) -> None:
        with self._lock:
            self.subscriptions.add(topic)

    def unsubscribe(self, topic: str) -> None:
        with self._lock:
            self.subscriptions.discard(topic)

    def store_message(self, topic: str, payload, qos: int = 0) -> None:
        entry = {"topic": topic, "payload": payload, "qos": qos, "received_at": _now()}
        with self._lock:
            history = self.messages.setdefault(topic, [])
            history.append(entry)
            if len(history) > self.max_history_per_topic:
                self.messages[topic] = history[-self.max_history_per_topic:]

    def get_latest(self, topic: str) -> dict | None:
        with self._lock:
            history = self.messages.get(topic, [])
            return history[-1] if history else None

    def get_history(self, topic: str, limit: int = 20) -> list:
        with self._lock:
            history = self.messages.get(topic, [])
            return list(history[-limit:])

    def list_subscriptions(self) -> list:
        with self._lock:
            return sorted(self.subscriptions)


class MqttConnector:
    def __init__(self, store: MqttStore = None):
        self.store = store or MqttStore()

    def connect(self, broker_host: str, broker_port: int = 1883,
                username: str = None, password: str = None) -> dict:
        # Simulated — no real broker connection
        return {"connected": True, "simulated": True, "broker": broker_host}

    def disconnect(self) -> dict:
        return {"connected": False}

    def subscribe(self, topic: str) -> dict:
        self.store.subscribe(topic)
        return {"subscribed": True, "topic": topic}

    def unsubscribe(self, topic: str) -> dict:
        self.store.unsubscribe(topic)
        return {"unsubscribed": True, "topic": topic}

    def publish(self, topic: str, payload, qos: int = 0) -> dict:
        if _is_command_topic(topic):
            return {
                "approval_required": True,
                "approval_message": (
                    f"Publish to command topic '{topic}' — "
                    "control commands require approval before sending"
                ),
                "pending_action": "mqtt_publish",
                "topic": topic,
            }
        self.store.store_message(topic, payload, qos=qos)
        return {"published": True, "topic": topic, "simulated": True}

    def list_subscriptions(self) -> list:
        return self.store.list_subscriptions()

    def get_latest_message(self, topic: str) -> dict:
        msg = self.store.get_latest(topic)
        if msg is None:
            return {"topic": topic, "message": None}
        return {"topic": topic, "message": msg}

    def get_message_history(self, topic: str, limit: int = 20) -> list:
        return self.store.get_history(topic, limit=limit)


# ── HTTP layer ────────────────────────────────────────────────────────────────

_mc = MqttConnector()

HEALTH = {"status": "healthy", "component": "mqtt", "port": PORT}


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
        return _mc.connect(
            broker_host=body["broker_host"],
            broker_port=body.get("broker_port", 1883),
            username=body.get("username"),
            password=body.get("password"),
        )
    if action == "disconnect":
        return _mc.disconnect()
    if action == "subscribe":
        return _mc.subscribe(body["topic"])
    if action == "unsubscribe":
        return _mc.unsubscribe(body["topic"])
    if action == "publish":
        return _mc.publish(
            topic=body["topic"],
            payload=body.get("payload"),
            qos=body.get("qos", 0),
        )
    if action == "list_subscriptions":
        return {"subscriptions": _mc.list_subscriptions()}
    if action == "get_latest_message":
        return _mc.get_latest_message(body["topic"])
    if action == "get_message_history":
        return {"history": _mc.get_message_history(
            body["topic"], limit=body.get("limit", 20)
        )}
    raise ValueError(f"Unknown action: {action}")


def main():
    server = HTTPServer(("0.0.0.0", PORT), _Handler)
    print(f"MQTT Connector listening on port {PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
