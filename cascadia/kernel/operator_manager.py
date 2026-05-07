"""
operator_manager.py - Cascadia OS
Discovers operators via manifest.json, starts and supervises them as subprocesses.
Operators are self-describing apps — drop a folder with manifest.json, it runs.
Remove the folder, it's gone. The manager has zero hardcoded operator knowledge.

Design contract:
  - Scans OPERATORS_DIR for subdirectories containing manifest.json
  - Respects manifest fields: autostart, port, health_path, start_cmd, worker_cmd
  - Supervises with restart-on-crash, exponential backoff, and health polling
  - Exposes HTTP control API on localhost:6210
  - Shuts down cleanly when stop() is called

If this file grows complex, that is a design error.
"""
# MATURITY: PRODUCTION — Operator lifecycle manager. Simple by design.
from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, request


_LOG_DIR    = Path(__file__).parent.parent.parent / "data" / "logs"
INTENT_FILE = Path(__file__).parent.parent.parent / "data" / "runtime" / "operator_intent.json"

DEFAULT_OPERATORS_DIR = Path(__file__).parent.parent / "operators"
HEALTH_INTERVAL = 30       # seconds between health checks
STARTUP_GRACE   = 8        # seconds to wait after start before first health check
HTTP_TIMEOUT    = 3        # seconds for health check HTTP request

RESTART_BACKOFFS = [5, 15, 30, 60, 120, 300]
MAX_RESTARTS     = 6

CREW_URL = "http://127.0.0.1:5100"
OM_API_PORT = 6210         # localhost-only kernel/admin surface


class OperatorProcess:
    """Owns the lifecycle of a single operator subprocess."""

    def __init__(self, manifest: dict, operator_dir: Path, logger) -> None:
        self.id           = manifest["id"]
        self.name         = manifest.get("name", self.id.upper())
        self.port         = manifest["port"]
        self.health_path  = manifest.get("health_path", "/api/health")
        self.start_cmd    = manifest.get("start_cmd", "dashboard.py")
        self.worker_cmd   = manifest.get("worker_cmd")
        self.manifest     = manifest
        self.operator_dir = operator_dir
        self.logger       = logger
        self.proc: Optional[subprocess.Popen] = None
        self.worker_proc: Optional[subprocess.Popen] = None
        self.worker_started = False
        self.status       = "pending"
        self._stopped     = False

    def _resolve_script(self, cmd: str) -> Path:
        filename = cmd.replace("python3 ", "").strip()
        if Path(filename).is_absolute():
            p = Path(filename)
            if p.exists():
                return p
            raise FileNotFoundError(
                f"Operator {self.id}: script not found. Tried:\n   {p}\n   Skipping."
            )
        candidates = [
            self.operator_dir / filename,
            self.operator_dir / self.id / filename,
        ]
        # start_cmd sometimes carries a redundant op-id prefix (e.g. "telegram/server.py")
        # Strip it so the path resolves to operator_dir/server.py correctly.
        prefix = f"{self.id}/"
        if filename.startswith(prefix):
            candidates.append(self.operator_dir / filename[len(prefix):])
        for p in candidates:
            if p.exists():
                return p
        tried = "\n   ".join(str(p) for p in candidates)
        raise FileNotFoundError(
            f"Operator {self.id}: script not found. Tried:\n   {tried}\n   Skipping."
        )

    def _build_cmd(self) -> list:
        script = self._resolve_script(self.start_cmd)
        return [sys.executable, str(script)]

    def _log_file(self, name: str):
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        return open(str(_LOG_DIR / f"{name}.log"), "a")

    def start(self, preexec_fn=None) -> None:
        cmd = self._build_cmd()
        self.logger.info("OperatorManager starting %s (port %s)", self.name, self.port)
        log = self._log_file(self.id)
        popen_kwargs = dict(
            cwd=str(self.operator_dir),
            env=self._env(),
            stdout=log,
            stderr=log,
        )
        if preexec_fn is not None and sys.platform != 'win32':
            popen_kwargs['preexec_fn'] = preexec_fn
        self.proc = subprocess.Popen(cmd, **popen_kwargs)
        self.status = "starting"

    def start_worker(self) -> None:
        if not self.worker_cmd:
            return
        try:
            worker_script = self._resolve_script(self.worker_cmd)
        except FileNotFoundError as e:
            self.logger.warning(str(e))
            return
        log = self._log_file(f"{self.id}_worker")
        self.worker_proc = subprocess.Popen(
            [sys.executable, str(worker_script)],
            cwd=str(self.operator_dir),
            env=self._env(),
            stdout=log,
            stderr=log,
        )
        self.worker_started = True
        self.logger.info(
            "Operator %s worker started (PID %d)", self.id, self.worker_proc.pid
        )

    def _env(self) -> dict:
        env = os.environ.copy()
        env["CASCADIA_PORT"] = str(self.port)
        env["CASCADIA_OPERATOR_ID"] = self.id
        return env

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def is_healthy(self) -> bool:
        try:
            import urllib.request
            url = f"http://127.0.0.1:{self.port}{self.health_path}"
            with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as r:
                return r.status == 200
        except Exception:
            return False

    def stop(self) -> None:
        self._stopped = True
        if self.proc and self.proc.poll() is None:
            self.logger.info("OperatorManager stopping %s", self.name)
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.worker_proc and self.worker_proc.poll() is None:
            self.worker_proc.terminate()
            try:
                self.worker_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.worker_proc.kill()


class OperatorManager:
    """
    Discovers operators from operators_dir, starts autostart ones,
    and supervises all running operators in a background thread.
    Exposes a localhost HTTP control API on OM_API_PORT.
    """

    def __init__(self, logger, operators_dir: Path = None, config: dict = None) -> None:
        self.logger        = logger
        self.operators_dir = operators_dir or DEFAULT_OPERATORS_DIR
        self.operators: dict = {}
        self._disabled: list = []
        self._thread: Optional[threading.Thread] = None
        self._running  = False
        self._config   = config or {}
        self._restart_counts: dict = {}
        self._worker_restart_counts: dict = {}
        self._shutting_down = False

    # ── Intent storage ────────────────────────────────────────────────────────

    def _load_intent(self) -> dict:
        """Load durable operator intent from disk."""
        try:
            if INTENT_FILE.exists():
                return json.loads(INTENT_FILE.read_text())
        except Exception:
            pass
        return {}

    def _save_intent(self, op_id: str, worker_intent: str,
                     updated_by: str = "system") -> None:
        """Save operator worker intent to disk. worker_intent: 'running' or 'stopped'."""
        try:
            INTENT_FILE.parent.mkdir(parents=True, exist_ok=True)
            intents = self._load_intent()
            intents[op_id] = {
                "worker_intent": worker_intent,
                "updated_at": datetime.now().isoformat(),
                "updated_by": updated_by,
            }
            INTENT_FILE.write_text(json.dumps(intents, indent=2))
        except Exception as e:
            self.logger.warning("Could not save intent for %s: %s", op_id, e)

    def _get_worker_intent(self, op_id: str) -> str:
        """Return the durable worker intent: 'running' (default) or 'stopped'."""
        return self._load_intent().get(op_id, {}).get("worker_intent", "running")

    def _get_operator(self, op_id: str) -> Optional[OperatorProcess]:
        """Find operator by id. Returns None if not found."""
        return self.operators.get(op_id)

    # ── Sandbox ───────────────────────────────────────────────────────────────

    def _get_preexec_fn(self, sandbox_config: dict):
        if sys.platform == 'win32':
            return None
        max_memory_mb  = sandbox_config.get('max_memory_mb', 512)
        max_open_files = sandbox_config.get('max_open_files', 256)

        def apply_limits():
            mem_bytes = max_memory_mb * 1024 * 1024
            try:
                resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
            except ValueError:
                pass
            try:
                resource.setrlimit(resource.RLIMIT_NOFILE, (max_open_files, max_open_files))
            except ValueError:
                pass
        return apply_limits

    # ── Discovery & startup ───────────────────────────────────────────────────

    def discover(self) -> None:
        """Scan operators directory for valid manifests."""
        if not self.operators_dir.exists():
            self.logger.warning("OperatorManager: operators dir not found at %s", self.operators_dir)
            return

        for op_dir in sorted(self.operators_dir.iterdir()):
            manifest_path = op_dir / "manifest.json"
            if not op_dir.is_dir() or not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text())
                op_id = manifest["id"]
                if not manifest.get("autostart", False):
                    self.logger.info("OperatorManager skipping %s (autostart: false)", op_id)
                    self._disabled.append(op_id)
                    continue
                self.operators[op_id] = OperatorProcess(manifest, op_dir, self.logger)
                self.logger.info(
                    "OperatorManager discovered: %s (port %s)",
                    manifest.get("name", op_id), manifest.get("port")
                )
            except Exception as e:
                self.logger.error("OperatorManager: bad manifest at %s — %s", op_dir, e)

    def start_all(self) -> None:
        """Start all discovered operators and log a startup summary."""
        sandbox_cfg = self._config.get('sandbox', {})
        sandbox_enabled = sandbox_cfg.get('enabled', False)
        preexec = self._get_preexec_fn(sandbox_cfg) if sandbox_enabled else None

        started, skipped, failed = [], [], []
        for op in self.operators.values():
            try:
                op.start(preexec_fn=preexec)
                started.append(op.id)
            except FileNotFoundError as e:
                self.logger.error(str(e))
                op.status = "skipped_bad_path"
                skipped.append(op.id)
            except Exception as e:
                self.logger.error("OperatorManager: failed to start %s — %s", op.name, e)
                op.status = "failed"
                failed.append(op.id)

        self.logger.info("OperatorManager startup complete:")
        self.logger.info("  Started:                    %s", started or "none")
        self.logger.info("  Skipped (bad path):         %s", skipped or "none")
        self.logger.info("  Disabled (autostart=false): %s", self._disabled or "none")
        self.logger.info("  Failed:                     %s", failed or "none")

    def stop_all(self) -> None:
        self._shutting_down = True
        self._running = False
        self.logger.info("OperatorManager shutting down — stopping all operators")
        # Stop workers first so they don't become orphans
        for op in self.operators.values():
            if op.worker_proc and op.worker_proc.poll() is None:
                self.logger.info("Stopping worker for %s (PID %d)", op.id, op.worker_proc.pid)
                op.worker_proc.terminate()
                try:
                    op.worker_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    op.worker_proc.kill()
                op.worker_proc = None
        # Then stop main operator processes
        for op in self.operators.values():
            if op.proc and op.proc.poll() is None:
                self.logger.info("Stopping %s (PID %d)", op.name, op.proc.pid)
                op.proc.terminate()
                try:
                    op.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    op.proc.kill()

    # ── Restart helpers ───────────────────────────────────────────────────────

    def _try_restart(self, op: OperatorProcess) -> None:
        if self._shutting_down:
            return
        op_id = op.id
        count = self._restart_counts.get(op_id, 0)

        if count >= MAX_RESTARTS:
            self.logger.error(
                "Operator %s exceeded %d restart attempts — "
                "marking as failed. Fix the operator and "
                "restart OperatorManager to retry.",
                op_id, MAX_RESTARTS
            )
            op.status = "failed"
            return

        backoff = RESTART_BACKOFFS[min(count, len(RESTART_BACKOFFS) - 1)]
        self._restart_counts[op_id] = count + 1
        self.logger.warning(
            "Operator %s died — restarting in %ds (attempt %d/%d)",
            op_id, backoff, count + 1, MAX_RESTARTS
        )
        time.sleep(backoff)
        try:
            op.start()
        except Exception as e:
            self.logger.error("OperatorManager: restart failed for %s — %s", op.name, e)

    def _try_restart_worker(self, op: OperatorProcess) -> None:
        if self._shutting_down:
            return
        op_id = op.id

        # Durable intent check — never restart a worker the user explicitly stopped.
        if self._get_worker_intent(op_id) == "stopped":
            self.logger.info(
                "Operator %s worker exited — intent is stopped, not restarting",
                op_id
            )
            op.worker_proc = None
            return

        count = self._worker_restart_counts.get(op_id, 0)

        if count >= MAX_RESTARTS:
            self.logger.error(
                "Operator %s worker exceeded %d restart attempts — giving up.",
                op_id, MAX_RESTARTS
            )
            return

        backoff = RESTART_BACKOFFS[min(count, len(RESTART_BACKOFFS) - 1)]
        self._worker_restart_counts[op_id] = count + 1
        self.logger.warning(
            "Operator %s worker died unexpectedly — restarting in %ds (attempt %d/%d)",
            op_id, backoff, count + 1, MAX_RESTARTS
        )
        time.sleep(backoff)
        op.start_worker()

    # ── CREW registration ─────────────────────────────────────────────────────

    def _register_with_crew(self, op_id: str, manifest: dict) -> None:
        import urllib.request

        for _ in range(30):
            try:
                urllib.request.urlopen(f"{CREW_URL}/health", timeout=2)
                break
            except Exception:
                time.sleep(2)

        for attempt in range(10):
            try:
                payload = json.dumps(manifest).encode()
                req = urllib.request.Request(
                    f"{CREW_URL}/register",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                urllib.request.urlopen(req, timeout=5)
                self.logger.info("Operator %s registered with CREW", op_id)
                return
            except Exception as e:
                self.logger.debug(
                    "CREW registration attempt %d failed: %s", attempt + 1, e
                )
                time.sleep(15)

    # ── HTTP control API ──────────────────────────────────────────────────────

    def _start_api(self, port: int = OM_API_PORT) -> None:
        """Start the OM control API on a background daemon thread."""
        api = Flask("operator_manager_api")
        # Suppress Flask startup banner and request logs
        import logging as _logging
        _logging.getLogger("werkzeug").setLevel(_logging.ERROR)

        @api.route("/operators/<op_id>/worker/stop", methods=["POST"])
        def worker_stop(op_id):
            op = self._get_operator(op_id)
            if not op:
                return jsonify({"ok": False, "error": f"Unknown operator: {op_id}"}), 404

            self._save_intent(op_id, "stopped", "api")

            if op.worker_proc and op.worker_proc.poll() is None:
                try:
                    op.worker_proc.terminate()
                    try:
                        op.worker_proc.wait(timeout=5)
                    except Exception:
                        op.worker_proc.kill()
                    op.worker_proc = None
                    self.logger.info("Operator %s worker stopped via API", op_id)
                    return jsonify({"ok": True, "status": "stopping", "op_id": op_id})
                except Exception as e:
                    return jsonify({"ok": False, "error": str(e)}), 500
            else:
                op.worker_proc = None
                return jsonify({"ok": True, "status": "stopped", "op_id": op_id})

        @api.route("/operators/<op_id>/worker/start", methods=["POST"])
        def worker_start(op_id):
            op = self._get_operator(op_id)
            if not op:
                return jsonify({"ok": False, "error": f"Unknown operator: {op_id}"}), 404

            self._save_intent(op_id, "running", "api")

            if op.worker_proc and op.worker_proc.poll() is None:
                return jsonify({"ok": True, "status": "already_running", "op_id": op_id})

            try:
                self._worker_restart_counts[op_id] = 0
                op.start_worker()
                return jsonify({"ok": True, "status": "starting", "op_id": op_id})
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)}), 500

        @api.route("/operators/<op_id>/status", methods=["GET"])
        def operator_status(op_id):
            op = self._get_operator(op_id)
            if not op:
                return jsonify({"ok": False, "error": f"Unknown operator: {op_id}"}), 404

            worker_running = (
                op.worker_proc is not None and op.worker_proc.poll() is None
            )
            return jsonify({
                "ok": True,
                "op_id": op_id,
                "status": op.status,
                "worker_running": worker_running,
                "worker_intent": self._get_worker_intent(op_id),
                "restart_count": self._restart_counts.get(op_id, 0),
            })

        @api.route("/health", methods=["GET"])
        def health():
            return jsonify({"ok": True, "service": "operator_manager"})

        threading.Thread(
            target=lambda: api.run(
                host="127.0.0.1",
                port=port,
                debug=False,
                use_reloader=False,
            ),
            daemon=True,
            name="om-api",
        ).start()
        self.logger.info("OperatorManager API running on port %d", port)

    # ── Supervision loop ──────────────────────────────────────────────────────

    def _supervise(self) -> None:
        """Background supervision loop — restarts crashed operators."""
        time.sleep(STARTUP_GRACE)
        while self._running:
            for op in list(self.operators.values()):
                if op._stopped or op.status in ("failed", "skipped_bad_path"):
                    continue

                if not op.is_alive():
                    if op.is_healthy():
                        # Port still up (e.g., process forked) — treat as running
                        if op.status != "running":
                            op.status = "running"
                            self._restart_counts[op.id] = 0
                        continue
                    self._try_restart(op)
                    continue

                # Process is alive — check health
                if op.is_healthy():
                    first_healthy = op.status != "running"
                    op.status = "running"
                    if first_healthy:
                        self._restart_counts[op.id] = 0
                        self.logger.info(
                            "Operator %s healthy — restart count reset", op.id
                        )
                        if op.worker_cmd and not op.worker_started:
                            op.start_worker()
                            self._save_intent(op.id, "running", "operator_manager")
                        threading.Thread(
                            target=self._register_with_crew,
                            args=(op.id, op.manifest),
                            daemon=True,
                            name=f"crew-register-{op.id}",
                        ).start()

                # Monitor worker
                if op.worker_started and op.worker_proc is not None:
                    if op.worker_proc.poll() is not None:
                        self._try_restart_worker(op)

            time.sleep(HEALTH_INTERVAL)

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self) -> None:
        """Discover, start, supervise, and expose control API. Non-blocking."""
        import signal as _signal
        _signal.signal(_signal.SIGTERM, lambda sig, frame: self.stop_all())
        self.discover()
        self.start_all()
        self._start_api(port=OM_API_PORT)
        self._running = True
        self._thread = threading.Thread(
            target=self._supervise, daemon=True, name="operator-supervisor"
        )
        self._thread.start()
        self.logger.info("OperatorManager supervising %d operator(s)", len(self.operators))
