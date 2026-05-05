"""
cascadia/operators/generic_alert/server.py

Open-core reference implementation — Apache 2.0
See: https://github.com/zyrconlabs/cascadia-os

Generic Alert Operator — port 8910 (env: GENERIC_ALERT_PORT)
Converts sensor threshold violations into Approval Center
requests. Demonstrates: approval gate pattern, SQLite-backed
state, IoT alert lifecycle (create → pending → resolve).

Copy and adapt this file to build your own alert operators.
Commercial operators built on this pattern are available
via the Zyrcon DEPOT: https://zyrcon.ai
"""
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("GENERIC_ALERT_PORT", 8910))
_DEFAULT_DB = os.environ.get(
    "GENERIC_ALERT_DB",
    os.path.join(os.path.dirname(__file__), "alerts.db"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Cascadia platform stubs ───────────────────────────────
# These are no-op stubs matching the Cascadia operator
# wiring contract. Replace with real implementations when
# running inside a full Cascadia OS deployment.

def vault_get(key: str, default=None):
    """Retrieve a credential from the Cascadia Vault."""
    return os.environ.get(key, default)

def sentinel_check(action: str, payload: dict) -> bool:
    """
    Approval gate check. Returns True if action is
    pre-approved, False if it requires human approval.
    In production this calls the Cascadia Sentinel service.
    """
    return False  # default: all actions require approval

def crew_register(operator_id: str, port: int) -> None:
    """Register this operator with the Cascadia CREW."""
    pass  # no-op in standalone mode
# ─────────────────────────────────────────────────────────


class AlertStore:
    def __init__(self, db_path: str = _DEFAULT_DB):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._lock, self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    alert_id    TEXT PRIMARY KEY,
                    device_id   TEXT,
                    device_name TEXT,
                    metric      TEXT,
                    current_value REAL,
                    threshold   REAL,
                    operator    TEXT,
                    severity    TEXT,
                    action      TEXT,
                    status      TEXT DEFAULT 'pending',
                    message     TEXT,
                    created_at  TEXT,
                    resolved_at TEXT,
                    resolution_notes TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rules (
                    rule_id    TEXT PRIMARY KEY,
                    device_id  TEXT,
                    metric     TEXT,
                    operator   TEXT,
                    threshold  REAL,
                    severity   TEXT,
                    action     TEXT,
                    created_at TEXT
                )
            """)

    def save_alert(self, alert: dict) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO alerts
                (alert_id, device_id, device_name, metric, current_value, threshold,
                 operator, severity, action, status, message, created_at, resolved_at,
                 resolution_notes)
                VALUES (:alert_id, :device_id, :device_name, :metric, :current_value,
                        :threshold, :operator, :severity, :action, :status, :message,
                        :created_at, :resolved_at, :resolution_notes)
            """, alert)

    def fetch_alert(self, alert_id: str) -> dict | None:
        with self._lock, self._conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM alerts WHERE alert_id = ?", (alert_id,)
            ).fetchone()
            return dict(row) if row else None

    def fetch_alerts(self, status: str | None = None, limit: int = 50) -> list:
        with self._lock, self._conn() as conn:
            conn.row_factory = sqlite3.Row
            if status:
                rows = conn.execute(
                    "SELECT * FROM alerts WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    def update_alert_status(self, alert_id: str, status: str,
                             resolved_at: str = None, resolution_notes: str = "") -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE alerts SET status=?, resolved_at=?, resolution_notes=? WHERE alert_id=?",
                (status, resolved_at, resolution_notes, alert_id)
            )

    def save_rule(self, rule: dict) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO rules
                (rule_id, device_id, metric, operator, threshold, severity, action, created_at)
                VALUES (:rule_id, :device_id, :metric, :operator, :threshold,
                        :severity, :action, :created_at)
            """, rule)

    def fetch_rules(self, device_id: str | None = None) -> list:
        with self._lock, self._conn() as conn:
            conn.row_factory = sqlite3.Row
            if device_id:
                rows = conn.execute(
                    "SELECT * FROM rules WHERE device_id = ? ORDER BY created_at DESC",
                    (device_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM rules ORDER BY created_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def delete_rule(self, rule_id: str) -> bool:
        with self._lock, self._conn() as conn:
            cur = conn.execute("DELETE FROM rules WHERE rule_id = ?", (rule_id,))
            return cur.rowcount > 0


class SimulatedAlertStore(AlertStore):
    """Drop-in AlertStore replacement for simulate mode.
    All writes are no-ops. Reads return empty results."""

    def _init_db(self): pass

    def save_alert(self, alert: dict) -> None: pass

    def fetch_alert(self, alert_id: str): return None

    def fetch_alerts(self, status=None, limit=50): return []

    def update_alert_status(self, *args, **kwargs): pass

    def save_rule(self, rule: dict) -> None: pass

    def fetch_rules(self, device_id=None): return []

    def delete_rule(self, rule_id: str) -> bool: return True


class GenericAlert:
    def __init__(self, store: AlertStore = None):
        self.store = store or AlertStore()

    def create_alert(self, device_id: str, metric: str, current_value,
                     threshold, severity: str, device_name: str = "",
                     operator_str: str = "gt",
                     action: str = "approval_required") -> dict:
        alert_id = str(uuid.uuid4())
        msg = (
            f"Sensor alert: {device_name} — {metric} is {current_value} "
            f"({operator_str} threshold of {threshold}). "
            f"Severity: {severity}. Action: {action}"
        )

        # Determine approval and status
        if severity == "critical":
            approval_required = True
            status = "pending"
        elif severity == "info" and action == "auto_resolve":
            approval_required = False
            status = "resolved"
        else:
            approval_required = True
            status = "pending"

        alert = {
            "alert_id": alert_id,
            "device_id": device_id,
            "device_name": device_name,
            "metric": metric,
            "current_value": current_value,
            "threshold": threshold,
            "operator": operator_str,
            "severity": severity,
            "action": action,
            "status": status,
            "message": msg,
            "created_at": _now(),
            "resolved_at": _now() if status == "resolved" else None,
            "resolution_notes": "",
        }
        self.store.save_alert(alert)

        result = dict(alert)
        result["approval_required"] = approval_required
        if approval_required:
            result["approval_message"] = msg
            result["pending_action"] = "create_alert"
        return result

    def list_alerts(self, status: str = None, limit: int = 50) -> list:
        return self.store.fetch_alerts(status=status, limit=limit)

    def get_alert(self, alert_id: str) -> dict | None:
        return self.store.fetch_alert(alert_id)

    def resolve_alert(self, alert_id: str, resolution_notes: str = "") -> dict:
        return {
            "approval_required": True,
            "approval_message": f"Resolve alert {alert_id}?",
            "pending_action": "resolve_alert",
            "alert_id": alert_id,
            "resolution_notes": resolution_notes,
        }

    def configure_rule(self, device_id: str, metric: str, operator_str: str,
                       threshold, severity: str, action: str) -> dict:
        return {
            "approval_required": True,
            "approval_message": f"Configure alert rule for {device_id}:{metric}",
            "pending_action": "configure_rule",
            "device_id": device_id,
            "metric": metric,
        }

    def list_rules(self, device_id: str = None) -> list:
        return self.store.fetch_rules(device_id=device_id)

    def delete_rule(self, rule_id: str) -> dict:
        return {
            "approval_required": True,
            "approval_message": f"Delete rule {rule_id}?",
            "pending_action": "delete_rule",
            "rule_id": rule_id,
        }


# ── HTTP layer ────────────────────────────────────────────────────────────────

_ga = GenericAlert()
_ga_sim = GenericAlert(store=SimulatedAlertStore())

HEALTH = {"status": "healthy", "component": "generic_alert", "port": PORT}


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
        if self.path == "/api/simulate":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            action = body.get("action", "")
            try:
                result = _dispatch_sim(action, body)
                result["simulated"] = True
                self._send(200, result)
            except Exception as exc:
                self._send(400, {"error": str(exc)})
            return
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
    if action == "create_alert":
        return _ga.create_alert(
            device_id=body["device_id"],
            metric=body["metric"],
            current_value=body["current_value"],
            threshold=body["threshold"],
            severity=body["severity"],
            device_name=body.get("device_name", ""),
            operator_str=body.get("operator_str", "gt"),
            action=body.get("action", "approval_required"),
        )
    if action == "list_alerts":
        return {"alerts": _ga.list_alerts(
            status=body.get("status"), limit=body.get("limit", 50)
        )}
    if action == "get_alert":
        return _ga.get_alert(body["alert_id"]) or {"error": "not found"}
    if action == "resolve_alert":
        return _ga.resolve_alert(
            alert_id=body["alert_id"],
            resolution_notes=body.get("resolution_notes", ""),
        )
    if action == "configure_rule":
        return _ga.configure_rule(
            device_id=body["device_id"],
            metric=body["metric"],
            operator_str=body.get("operator_str", "gt"),
            threshold=body["threshold"],
            severity=body.get("severity", "warning"),
            action=body.get("action", "approval_required"),
        )
    if action == "list_rules":
        return {"rules": _ga.list_rules(device_id=body.get("device_id"))}
    if action == "delete_rule":
        return _ga.delete_rule(body["rule_id"])
    raise ValueError(f"Unknown action: {action}")


def _dispatch_sim(action: str, body: dict) -> dict:
    if action == "create_alert":
        return _ga_sim.create_alert(
            device_id=body["device_id"],
            metric=body["metric"],
            current_value=body["current_value"],
            threshold=body["threshold"],
            severity=body["severity"],
            device_name=body.get("device_name", ""),
            operator_str=body.get("operator_str", "gt"),
            action=body.get("action", "approval_required"),
        )
    if action == "list_alerts":
        return {"alerts": _ga_sim.list_alerts(
            status=body.get("status"), limit=body.get("limit", 50)
        )}
    if action == "get_alert":
        return _ga_sim.get_alert(body["alert_id"]) or {"error": "not found"}
    if action == "resolve_alert":
        return _ga_sim.resolve_alert(
            alert_id=body["alert_id"],
            resolution_notes=body.get("resolution_notes", ""),
        )
    if action == "configure_rule":
        return _ga_sim.configure_rule(
            device_id=body["device_id"],
            metric=body["metric"],
            operator_str=body.get("operator_str", "gt"),
            threshold=body["threshold"],
            severity=body.get("severity", "warning"),
            action=body.get("action", "approval_required"),
        )
    if action == "list_rules":
        return {"rules": _ga_sim.list_rules(device_id=body.get("device_id"))}
    if action == "delete_rule":
        return _ga_sim.delete_rule(body["rule_id"])
    raise ValueError(f"Unknown action: {action}")


def main():
    server = HTTPServer(("0.0.0.0", PORT), _Handler)
    print(f"Generic Alert operator listening on port {PORT}")
    crew_register("generic_alert", PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
