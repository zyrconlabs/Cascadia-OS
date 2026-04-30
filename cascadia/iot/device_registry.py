"""
device_registry.py — Cascadia OS IoT
Pure library: device CRUD, last-seen updates, threshold config.
Owns: DeviceStore, _Handler (importable HTTP handler).
Does not own: port binding, process startup, sensor ingest, alerting.
Port binding is owned by cascadia/operators/iot_registry/operator.py.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

NAME = "device_registry"
VERSION = "1.0.0"
DB_PATH = os.environ.get("IOT_DB_PATH", "data/iot/devices.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [device_registry] %(message)s",
)
log = logging.getLogger(NAME)

_db_lock = threading.Lock()


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DeviceStore:
    def __init__(self, db_path: str = DB_PATH) -> None:
        self._db = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS devices (
                    device_id           TEXT PRIMARY KEY,
                    name                TEXT NOT NULL,
                    type                TEXT NOT NULL,
                    location            TEXT,
                    alert_threshold_min REAL,
                    alert_threshold_max REAL,
                    unit                TEXT,
                    registered_at       TEXT NOT NULL,
                    last_seen           TEXT
                )
            """)

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "device_id": row["device_id"],
            "name": row["name"],
            "type": row["type"],
            "location": row["location"],
            "alert_threshold_min": row["alert_threshold_min"],
            "alert_threshold_max": row["alert_threshold_max"],
            "unit": row["unit"],
            "registered_at": row["registered_at"],
            "last_seen": row["last_seen"],
        }

    def register(self, data: Dict[str, Any]) -> Dict[str, Any]:
        now = _now_utc()
        with _db_lock, sqlite3.connect(self._db) as conn:
            conn.execute("""
                INSERT INTO devices
                  (device_id, name, type, location, alert_threshold_min,
                   alert_threshold_max, unit, registered_at, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(device_id) DO UPDATE SET
                  name=excluded.name, type=excluded.type,
                  location=excluded.location,
                  alert_threshold_min=excluded.alert_threshold_min,
                  alert_threshold_max=excluded.alert_threshold_max,
                  unit=excluded.unit
            """, (
                data["device_id"], data["name"], data["type"],
                data.get("location"), data.get("alert_threshold_min"),
                data.get("alert_threshold_max"), data.get("unit"), now,
            ))
        return self.get(data["device_id"])

    def list_all(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM devices ORDER BY registered_at DESC"
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get(self, device_id: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM devices WHERE device_id=?", (device_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def update(self, device_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        allowed = {"name", "location", "alert_threshold_min", "alert_threshold_max", "unit"}
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return self.get(device_id)
        fields = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [device_id]
        with _db_lock, sqlite3.connect(self._db) as conn:
            conn.execute(
                f"UPDATE devices SET {fields} WHERE device_id=?", values
            )
        return self.get(device_id)

    def touch_last_seen(self, device_id: str) -> None:
        with _db_lock, sqlite3.connect(self._db) as conn:
            conn.execute(
                "UPDATE devices SET last_seen=? WHERE device_id=?",
                (_now_utc(), device_id),
            )

    def deregister_note(self, device_id: str) -> str:
        path = os.getenv(
            "ZYRCON_SUGGESTIONS_PATH",
            "data/suggestions/iot_suggestions.md"
        )
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        note = (
            f"\n### [{_now_utc()}] — Deregister request: {device_id}\n"
            f"Affects: data/iot/devices.db\n"
            f"Risk: LOW\n"
            f"Description: User requested deregistration of device {device_id}. "
            f"Deletion is intentionally not automatic — requires manual review.\n"
            f"Proposed change: DELETE FROM devices WHERE device_id='{device_id}';\n"
            f"Decision: [leave blank]\n"
        )
        try:
            with open(path, "a") as f:
                f.write(note)
        except OSError as exc:
            log.warning("could not write to suggestions file: %s", exc)
        return note


_store: Optional[DeviceStore] = None
_store_lock = threading.Lock()


def _get_store() -> DeviceStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = DeviceStore()
    return _store


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        log.info(fmt, *args)

    def _send(self, status: int, body: Any) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> Optional[Dict[str, Any]]:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            return None

    def _device_id_from_path(self, path: str) -> Optional[str]:
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "iot" and parts[1] == "devices":
            return parts[2]
        return None

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/iot/health":
            self._send(200, {"status": "healthy", "component": "device_registry"})
            return
        if path == "/iot/devices":
            self._send(200, {"devices": _get_store().list_all()})
            return
        device_id = self._device_id_from_path(path)
        if device_id and not path.endswith("/deregister"):
            device = _get_store().get(device_id)
            if device:
                self._send(200, device)
            else:
                self._send(404, {"error": f"device '{device_id}' not found"})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path

        if path == "/iot/devices/register":
            body = self._read_json()
            if not body:
                self._send(400, {"error": "missing JSON body"})
                return
            for field in ("device_id", "name", "type"):
                if not body.get(field):
                    self._send(400, {"error": f"missing required field: {field}"})
                    return
            device = _get_store().register(body)
            self._send(201, device)
            return

        if path.endswith("/deregister"):
            device_id = path.split("/")[-2]
            device = _get_store().get(device_id)
            if not device:
                self._send(404, {"error": f"device '{device_id}' not found"})
                return
            _get_store().deregister_note(device_id)
            self._send(200, {
                "ok": True,
                "message": "Deregister request logged to SUGGESTIONS file — not auto-deleted."
            })
            return

        self._send(404, {"error": "not found"})

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        device_id = self._device_id_from_path(path)
        if not device_id:
            self._send(404, {"error": "not found"})
            return
        body = self._read_json() or {}
        updated = _get_store().update(device_id, body)
        if updated:
            self._send(200, updated)
        else:
            self._send(404, {"error": f"device '{device_id}' not found"})


def touch_last_seen(device_id: str) -> None:
    _get_store().touch_last_seen(device_id)


# Public module-level API
def register_device(data: Dict[str, Any]) -> Dict[str, Any]:
    return _get_store().register(data)

def list_devices() -> List[Dict[str, Any]]:
    return _get_store().list_all()

def get_device(device_id: str) -> Optional[Dict[str, Any]]:
    return _get_store().get(device_id)

def update_device(device_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return _get_store().update(device_id, data)

def propose_deregister(device_id: str) -> str:
    return _get_store().deregister_note(device_id)
