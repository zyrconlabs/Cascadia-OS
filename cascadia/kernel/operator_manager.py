"""
operator_manager.py - Cascadia OS
Discovers operators via manifest.json, starts and supervises them as subprocesses.
Operators are self-describing apps — drop a folder with manifest.json, it runs.
Remove the folder, it's gone. The manager has zero hardcoded operator knowledge.

Design contract:
  - Scans OPERATORS_DIR for subdirectories containing manifest.json
  - Respects manifest fields: autostart, lifecycle, port, health_path, start_cmd, worker_cmd
  - Lifecycle states: always_on, activity_driven, on_demand
  - Intent states: running (restart on crash), sleeping (wake on event), stopped (user stopped)
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
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, request


_LOG_DIR    = Path(__file__).parent.parent.parent / "data" / "logs"
INTENT_FILE = Path(__file__).parent.parent.parent / "data" / "runtime" / "operator_intent.json"

DEFAULT_OPERATORS_DIR = Path(__file__).parent.parent / "operators"
STARTUP_GRACE        = 8    # seconds to wait after start before first health check
HTTP_TIMEOUT         = 3    # seconds for health check HTTP request
LIVENESS_INTERVAL    = 30   # seconds between liveness checks
SOFT_PULSE_INTERVAL  = 900  # 15 minutes between has_work checks

RESTART_BACKOFFS = [5, 15, 30, 60, 120, 300]
MAX_RESTARTS     = 6

CREW_URL    = "http://127.0.0.1:5100"
OM_API_PORT = 6210         # localhost-only kernel/admin surface

# Valid intent values
_VALID_INTENTS = {"running", "sleeping", "stopped"}


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

    Lifecycle model:
      always_on      — always running, restart on crash
      activity_driven — starts at boot, sleeps when idle, wakes on work
      on_demand      — sleeps by default, woken by Mission Manager or API

    Intent states:
      running  — active, restart on crash
      sleeping — idle, wake on event, no crash restart
      stopped  — user stopped, never auto-restart
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

    def _save_intent(self, op_id: str, intent: str,
                     updated_by: str = "system") -> None:
        """Save operator intent to disk. intent: 'running', 'sleeping', or 'stopped'."""
        if intent not in _VALID_INTENTS:
            self.logger.warning("_save_intent: invalid intent %r for %s", intent, op_id)
            return
        try:
            INTENT_FILE.parent.mkdir(parents=True, exist_ok=True)
            intents = self._load_intent()
            intents[op_id] = {
                "worker_intent": intent,
                "updated_at": datetime.now().isoformat(),
                "updated_by": updated_by,
            }
            INTENT_FILE.write_text(json.dumps(intents, indent=2))
        except Exception as e:
            self.logger.warning("Could not save intent for %s: %s", op_id, e)

    def _get_worker_intent(self, op_id: str) -> str:
        """Return the durable intent: 'running' (default), 'sleeping', or 'stopped'."""
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
        """Scan operators directory for valid manifests.

        Loads ALL operators that have a lifecycle field, regardless of autostart.
        Operators without a lifecycle field and autostart=false are ignored (connectors,
        marketplace stubs, etc.) — same as before.
        """
        if not self.operators_dir.exists():
            self.logger.warning("OperatorManager: operators dir not found at %s", self.operators_dir)
            return

        for op_dir in sorted(self.operators_dir.iterdir()):
            manifest_path = op_dir / "manifest.json"
            if not op_dir.is_dir() or not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text())
                op_id     = manifest["id"]
                lifecycle = manifest.get("lifecycle")
                autostart = manifest.get("autostart", False)

                if not autostart and not lifecycle:
                    # Legacy: no lifecycle field and not autostart → skip (connector/stub)
                    self._disabled.append(op_id)
                    continue

                op = OperatorProcess(manifest, op_dir, self.logger)
                self.operators[op_id] = op
                self.logger.info(
                    "OperatorManager discovered: %s (port %s, lifecycle=%s, autostart=%s)",
                    manifest.get("name", op_id), manifest.get("port"),
                    lifecycle or "unset", autostart,
                )
            except Exception as e:
                self.logger.error("OperatorManager: bad manifest at %s — %s", op_dir, e)

    def start_all(self) -> None:
        """Start autostart operators. Non-autostart operators start sleeping."""
        sandbox_cfg = self._config.get('sandbox', {})
        sandbox_enabled = sandbox_cfg.get('enabled', False)
        preexec = self._get_preexec_fn(sandbox_cfg) if sandbox_enabled else None

        existing_intents = self._load_intent()
        started, sleeping, skipped, failed = [], [], [], []

        for op in self.operators.values():
            if not op.manifest.get("autostart", False):
                # Initialize sleeping intent only if this operator has no prior intent record
                if op.id not in existing_intents:
                    self._save_intent(op.id, "sleeping", "operator_manager")
                op.status = "sleeping"
                sleeping.append(op.id)
                continue
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
        self.logger.info("  Sleeping (autostart=false): %s", sleeping or "none")
        self.logger.info("  Skipped (bad path):         %s", skipped or "none")
        self.logger.info("  Disabled (no lifecycle):    %s", len(self._disabled))
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

    # ── Sleep / Wake ──────────────────────────────────────────────────────────

    def sleep_operator(self, op_id: str, reason: str = "no_work") -> bool:
        """Stop an operator process and mark intent as sleeping (not stopped).
        OM will wake it automatically when work is detected or /wake is called."""
        op = self._get_operator(op_id)
        if not op:
            self.logger.warning("sleep_operator: unknown operator %s", op_id)
            return False

        # Stop worker first if running
        if op.worker_proc is not None and op.worker_proc.poll() is None:
            try:
                op.worker_proc.terminate()
                op.worker_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                op.worker_proc.kill()
            except Exception:
                pass
            op.worker_proc = None
            op.worker_started = False

        # Stop main process
        if op.proc is not None and op.proc.poll() is None:
            try:
                op.proc.terminate()
                op.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                op.proc.kill()
            except Exception:
                pass
            op.proc = None

        self._save_intent(op_id, "sleeping", "operator_manager")
        op.status = "sleeping"
        self.logger.info("Operator %s sleeping (reason: %s)", op_id, reason)
        return True

    def wake_operator(self, op_id: str, reason: str = "work_detected") -> bool:
        """Wake a sleeping operator. Sets intent=running and starts the process.
        Called by boot check, /wake API, or soft pulse."""
        op = self._get_operator(op_id)
        if not op:
            self.logger.warning("wake_operator: unknown operator %s", op_id)
            return False

        # Already running — nothing to do
        if op.proc is not None and op.proc.poll() is None:
            self.logger.debug("Operator %s already running", op_id)
            return True

        self._save_intent(op_id, "running", "operator_manager")
        op.status = "starting"
        self.logger.info("Waking operator %s (reason: %s)", op_id, reason)

        try:
            op.start()
            if op.worker_cmd:
                op.start_worker()
            return True
        except Exception as e:
            self.logger.error("Failed to wake operator %s: %s", op_id, e)
            self._save_intent(op_id, "sleeping", "operator_manager")
            op.status = "sleeping"
            return False

    # ── Work detection ────────────────────────────────────────────────────────

    def _check_operator_has_work(self, op: OperatorProcess) -> bool:
        """Check if an operator has pending work.
        Tries health endpoint first (when running), then state file fallback."""
        # Try health endpoint if operator is running
        if op.proc is not None and op.proc.poll() is None:
            try:
                url = f"http://127.0.0.1:{op.port}{op.health_path}"
                with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as r:
                    data = json.loads(r.read().decode())
                return bool(data.get("has_work", False))
            except Exception:
                pass

        # Fallback: check state.json for activity_driven operators (e.g. RECON)
        for state_path in (
            Path(op.operator_dir) / "data" / "state.json",
            Path(op.operator_dir) / "state.json",
        ):
            if state_path.exists():
                try:
                    state = json.loads(state_path.read_text())
                    return state.get("status") == "running"
                except Exception:
                    pass

        return False

    # ── Boot check (Trigger 1) ────────────────────────────────────────────────

    def _boot_check(self) -> None:
        """Check activity_driven operators for pending work after startup grace period.
        Keeps running if work found, puts to sleep if idle."""
        self.logger.info("OperatorManager boot check starting...")

        for op in list(self.operators.values()):
            lifecycle = op.manifest.get("lifecycle", "on_demand")
            if lifecycle != "activity_driven":
                continue

            has_work = self._check_operator_has_work(op)

            if has_work:
                self.logger.info(
                    "Boot check: %s has pending work — keeping running", op.id
                )
                if op.proc is None or op.proc.poll() is not None:
                    self.wake_operator(op.id, "boot_work_detected")
            else:
                self.logger.info(
                    "Boot check: %s has no pending work — sleeping", op.id
                )
                if op.proc is not None and op.proc.poll() is None:
                    self.sleep_operator(op.id, "boot_no_work")
                else:
                    self._save_intent(op.id, "sleeping", "boot_check")
                    op.status = "sleeping"

        self.logger.info("OperatorManager boot check complete")

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

        # Never restart worker for sleeping or explicitly stopped operators
        intent = self._get_worker_intent(op_id)
        if intent in ("stopped", "sleeping"):
            self.logger.info(
                "Operator %s worker exited — intent is %s, not restarting",
                op_id, intent
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

    # ── Monitoring loops ──────────────────────────────────────────────────────

    def _check_process_liveness(self) -> None:
        """Loop 1: liveness-only check every 30s. No HTTP. No logging when healthy."""
        for op in list(self.operators.values()):
            if op._stopped or op.status in ("failed", "skipped_bad_path"):
                continue

            intent = self._get_worker_intent(op.id)

            # Main process liveness
            if op.proc is not None and op.proc.poll() is not None:
                # Process exited
                if intent == "running":
                    # Unexpected exit — check if port is still up (forked process)
                    if op.is_healthy():
                        op.status = "running"
                        self._restart_counts[op.id] = 0
                    else:
                        self._try_restart(op)
                # sleeping or stopped → expected, no action
                continue

            # Process is alive or not yet started
            if op.proc is not None and op.proc.poll() is None:
                if op.status == "starting" and op.is_healthy():
                    # First healthy transition: start worker, register with CREW
                    first_healthy = True
                    op.status = "running"
                    self._restart_counts[op.id] = 0
                    self.logger.info("Operator %s healthy — restart count reset", op.id)
                    if op.worker_cmd and not op.worker_started:
                        op.start_worker()
                        self._save_intent(op.id, "running", "operator_manager")
                    threading.Thread(
                        target=self._register_with_crew,
                        args=(op.id, op.manifest),
                        daemon=True,
                        name=f"crew-register-{op.id}",
                    ).start()

            # Worker liveness
            if op.worker_started and op.worker_proc is not None:
                if op.worker_proc.poll() is not None:
                    self._try_restart_worker(op)

    def _soft_pulse_check(self) -> None:
        """Loop 2 (Trigger 3): Check has_work for running activity_driven operators.
        Sleeps idle ones. Runs every SOFT_PULSE_INTERVAL seconds. Logs on state change only."""
        self.logger.debug("OM soft pulse check running")

        for op in list(self.operators.values()):
            lifecycle = op.manifest.get("lifecycle", "on_demand")
            if lifecycle != "activity_driven":
                continue

            intent = self._get_worker_intent(op.id)
            if intent != "running":
                continue
            if op.proc is None or op.proc.poll() is not None:
                continue

            has_work = self._check_operator_has_work(op)

            if not has_work:
                self.logger.info(
                    "Soft pulse: %s has no work — sleeping", op.id
                )
                self.sleep_operator(op.id, "soft_pulse_no_work")
            # has_work=True → stay running, no log

    def _monitoring_loop(self) -> None:
        """Background loop: liveness check every 30s, soft pulse every 15min."""
        time.sleep(STARTUP_GRACE)
        self._boot_check()
        last_pulse = time.time()

        while self._running:
            now = time.time()
            self._check_process_liveness()
            if now - last_pulse >= SOFT_PULSE_INTERVAL:
                self._soft_pulse_check()
                last_pulse = now
            time.sleep(LIVENESS_INTERVAL)

    # ── HTTP control API ──────────────────────────────────────────────────────

    def _start_api(self, port: int = OM_API_PORT) -> None:
        """Start the OM control API on a background daemon thread."""
        api = Flask("operator_manager_api")
        import logging as _logging
        _logging.getLogger("werkzeug").setLevel(_logging.ERROR)

        om = self  # capture for closures

        @api.route("/operators/<op_id>/worker/stop", methods=["POST"])
        def worker_stop(op_id):
            op = om._get_operator(op_id)
            if not op:
                return jsonify({"ok": False, "error": f"Unknown operator: {op_id}"}), 404

            om._save_intent(op_id, "stopped", "api")

            if op.worker_proc and op.worker_proc.poll() is None:
                try:
                    op.worker_proc.terminate()
                    try:
                        op.worker_proc.wait(timeout=5)
                    except Exception:
                        op.worker_proc.kill()
                    op.worker_proc = None
                    om.logger.info("Operator %s worker stopped via API", op_id)
                    return jsonify({"ok": True, "status": "stopping", "op_id": op_id})
                except Exception as e:
                    return jsonify({"ok": False, "error": str(e)}), 500
            else:
                op.worker_proc = None
                return jsonify({"ok": True, "status": "stopped", "op_id": op_id})

        @api.route("/operators/<op_id>/worker/start", methods=["POST"])
        def worker_start(op_id):
            op = om._get_operator(op_id)
            if not op:
                return jsonify({"ok": False, "error": f"Unknown operator: {op_id}"}), 404

            om._save_intent(op_id, "running", "api")

            if op.worker_proc and op.worker_proc.poll() is None:
                return jsonify({"ok": True, "status": "already_running", "op_id": op_id})

            try:
                om._worker_restart_counts[op_id] = 0
                op.start_worker()
                return jsonify({"ok": True, "status": "starting", "op_id": op_id})
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)}), 500

        @api.route("/operators/<op_id>/wake", methods=["POST"])
        def operator_wake(op_id):
            """Wake a sleeping operator. Called by Mission Manager, PRISM, or another operator."""
            op = om._get_operator(op_id)
            if not op:
                return jsonify({"ok": False, "error": f"Unknown operator: {op_id}"}), 404

            intent = om._get_worker_intent(op_id)

            if intent == "stopped":
                return jsonify({
                    "ok": False,
                    "error": "Operator is explicitly stopped. Use /start to override.",
                }), 409

            body = request.get_json(force=True) or {}
            reason = body.get("reason", "api_request")

            previous_intent = intent
            success = om.wake_operator(op_id, reason)

            return jsonify({
                "ok": success,
                "op_id": op_id,
                "status": "waking" if success else "failed",
                "previous_intent": previous_intent,
            })

        @api.route("/operators/<op_id>/sleep", methods=["POST"])
        def operator_sleep(op_id):
            """Put an operator to sleep. Called by Mission Manager after a step or soft pulse."""
            op = om._get_operator(op_id)
            if not op:
                return jsonify({"ok": False, "error": f"Unknown operator: {op_id}"}), 404

            body = request.get_json(force=True) or {}
            reason = body.get("reason", "api_request")

            success = om.sleep_operator(op_id, reason)

            return jsonify({
                "ok": success,
                "op_id": op_id,
                "status": "sleeping" if success else "failed",
            })

        @api.route("/operators/<op_id>/status", methods=["GET"])
        def operator_status(op_id):
            op = om._get_operator(op_id)
            if not op:
                return jsonify({"ok": False, "error": f"Unknown operator: {op_id}"}), 404

            worker_running = (
                op.worker_proc is not None and op.worker_proc.poll() is None
            )
            return jsonify({
                "ok": True,
                "op_id": op_id,
                "status": op.status,
                "port": op.port,
                "lifecycle": op.manifest.get("lifecycle", "on_demand"),
                "worker_running": worker_running,
                "worker_intent": om._get_worker_intent(op_id),
                "has_work": om._check_operator_has_work(op),
                "restart_count": om._restart_counts.get(op_id, 0),
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
            target=self._monitoring_loop, daemon=True, name="operator-supervisor"
        )
        self._thread.start()
        self.logger.info("OperatorManager supervising %d operator(s)", len(self.operators))
