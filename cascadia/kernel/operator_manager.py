"""
operator_manager.py - Cascadia OS
Discovers operators via manifest.json, starts and supervises them as subprocesses.
Operators are self-describing apps — drop a folder with manifest.json, it runs.
Remove the folder, it's gone. The manager has zero hardcoded operator knowledge.

Design contract:
  - Scans OPERATORS_DIR for subdirectories containing manifest.json
  - Respects manifest fields: autostart, port, health_path, start_cmd
  - Supervises with restart-on-crash and health polling
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
from pathlib import Path
from typing import Optional


DEFAULT_OPERATORS_DIR = Path(__file__).parent.parent / "operators"
HEALTH_INTERVAL = 10       # seconds between health checks
RESTART_DELAY   = 5        # seconds before restarting a crashed operator
STARTUP_GRACE   = 8        # seconds to wait after start before first health check
HTTP_TIMEOUT    = 3        # seconds for health check HTTP request


class OperatorProcess:
    """Owns the lifecycle of a single operator subprocess."""

    def __init__(self, manifest: dict, operator_dir: Path, logger) -> None:
        self.id           = manifest["id"]
        self.name         = manifest.get("name", self.id.upper())
        self.port         = manifest["port"]
        self.health_path  = manifest.get("health_path", "/api/health")
        self.start_cmd    = manifest.get("start_cmd", "dashboard.py")
        self.operator_dir = operator_dir
        self.logger       = logger
        self.proc: Optional[subprocess.Popen] = None
        self._stopped     = False

    def _build_cmd(self) -> list:
        # start_cmd is a script filename relative to operator_dir
        script = self.operator_dir / self.start_cmd.replace("python3 ", "").strip()
        if script.exists():
            return [sys.executable, str(script)]
        raise ValueError(f"Operator {self.id}: script not found at {script}")

    def _log_file(self):
        log_dir = self.operator_dir.parent.parent.parent / "data" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return open(str(log_dir / f"{self.id}.log"), "a")

    def start(self, preexec_fn=None) -> None:
        cmd = self._build_cmd()
        self.logger.info("OperatorManager starting %s (port %s)", self.name, self.port)
        log = self._log_file()
        popen_kwargs = dict(
            cwd=str(self.operator_dir),
            env=self._env(),
            stdout=log,
            stderr=log,
        )
        if preexec_fn is not None and sys.platform != 'win32':
            popen_kwargs['preexec_fn'] = preexec_fn
        self.proc = subprocess.Popen(cmd, **popen_kwargs)

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


class OperatorManager:
    """
    Discovers operators from operators_dir, starts autostart ones,
    and supervises all running operators in a background thread.
    """

    def __init__(self, logger, operators_dir: Path = None, config: dict = None) -> None:
        self.logger       = logger
        self.operators_dir = operators_dir or DEFAULT_OPERATORS_DIR
        self.operators: dict = {}
        self._thread: Optional[threading.Thread] = None
        self._running  = False
        self._config   = config or {}

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
                    continue
                self.operators[op_id] = OperatorProcess(manifest, op_dir, self.logger)
                self.logger.info("OperatorManager discovered: %s (port %s)", manifest.get("name", op_id), manifest.get("port"))
            except Exception as e:
                self.logger.error("OperatorManager: bad manifest at %s — %s", op_dir, e)

    def start_all(self) -> None:
        """Start all discovered operators."""
        sandbox_cfg = self._config.get('sandbox', {})
        sandbox_enabled = sandbox_cfg.get('enabled', False)
        preexec = self._get_preexec_fn(sandbox_cfg) if sandbox_enabled else None
        for op in self.operators.values():
            try:
                op.start(preexec_fn=preexec)
            except Exception as e:
                self.logger.error("OperatorManager: failed to start %s — %s", op.name, e)

    def stop_all(self) -> None:
        self._running = False
        for op in self.operators.values():
            op.stop()

    def _supervise(self) -> None:
        """Background supervision loop — restarts crashed operators."""
        time.sleep(STARTUP_GRACE)
        while self._running:
            for op in list(self.operators.values()):
                if op._stopped:
                    continue
                if not op.is_alive():
                    if op.is_healthy():
                        continue
                    self.logger.warning("OperatorManager: %s exited — restarting in %ss", op.name, RESTART_DELAY)
                    time.sleep(RESTART_DELAY)
                    try:
                        op.start()
                    except Exception as e:
                        self.logger.error("OperatorManager: restart failed for %s — %s", op.name, e)
            time.sleep(HEALTH_INTERVAL)

    def run(self) -> None:
        """Discover, start, and begin supervising. Non-blocking."""
        self.discover()
        self.start_all()
        self._running = True
        self._thread = threading.Thread(target=self._supervise, daemon=True, name="operator-supervisor")
        self._thread.start()
        self.logger.info("OperatorManager supervising %d operator(s)", len(self.operators))
