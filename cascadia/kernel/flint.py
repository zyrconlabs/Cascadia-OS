"""
flint.py — Cascadia OS v{VERSION}
FLINT: Server and OS control layer.

Owns: process lifecycle, readiness-gated startup, health supervision,
      restart/backoff, graceful shutdown, resource-governance entrypoint.
Does not own: workflow planning, scheduler logic, approval UI, store mechanics.
"""
# MATURITY: PRODUCTION
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List
from urllib import request

from cascadia import VERSION, VERSION_SHORT
from cascadia.shared.config import load_config
from cascadia.shared.logger import configure_logging


class ReusableHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def server_bind(self) -> None:
        import socket as _socket
        # SO_REUSEPORT prevents "Address already in use" on fast restarts (macOS/Linux)
        if hasattr(_socket, 'SO_REUSEPORT'):
            try:
                self.socket.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass
        super().server_bind()


@dataclass(slots=True)
class ProcessEntry:
    """Owns runtime metadata for one supervised process. Does not own process business logic."""
    name: str
    module: str
    port: int
    tier: int
    heartbeat_file: str
    depends_on: List[str] = field(default_factory=list)
    pid: int | None = None
    process_state: str = 'starting'
    healthy: bool = False
    last_health_ok_at: float | None = None
    restart_attempts: int = 0
    next_restart_at: float = 0.0
    last_error: str = ''


class Flint:
    """
    FLINT - Cascadia OS control plane.
    Owns process supervision, health, restart/backoff, and graceful shutdown.
    Does not own workflows, approvals, scheduling, or store mechanics.
    """

    def __init__(self, config_path: str) -> None:
        self.config_path = config_path
        self.config = load_config(config_path)
        self.logger = configure_logging(self.config['log_dir'], 'flint')
        self.shutdown_event = threading.Event()
        self.components = {
            item['name']: ProcessEntry(
                name=item['name'], module=item['module'], port=item['port'],
                tier=item['tier'], heartbeat_file=item['heartbeat_file'],
                depends_on=item.get('depends_on', []),
            )
            for item in self.config['components']
        }
        self.processes: Dict[str, subprocess.Popen[str]] = {}
        self.process_state = 'starting'
        self._status_server: ReusableHTTPServer | None = None
        # LLM health tracking
        self._llm_health: Dict[str, Any] = {'ok': False, 'latency_ms': None, 'error': None, 'checked_at': None}
        self._llm_down_count: int = 0

    def _start_component(self, component: ProcessEntry) -> None:
        cmd = [sys.executable, '-m', component.module, '--config', self.config_path, '--name', component.name]
        self.logger.info('FLINT starting %s (%s)', component.name, component.module)
        proc = subprocess.Popen(cmd, text=True)
        self.processes[component.name] = proc
        component.pid = proc.pid
        component.process_state = 'starting'
        component.last_error = ''

    def _http_get(self, port: int, path: str) -> Dict[str, Any]:
        with request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=2) as r:
            return json.loads(r.read().decode())

    def _http_post(self, port: int, path: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        data = json.dumps(payload or {}).encode()
        req = request.Request(f'http://127.0.0.1:{port}{path}', data=data, method='POST',
                              headers={'Content-Type': 'application/json'})
        with request.urlopen(req, timeout=2) as r:
            return json.loads(r.read().decode())

    def _check_health(self, component: ProcessEntry) -> bool:
        proc = self.processes.get(component.name)
        if proc is None or proc.poll() is not None:
            component.process_state = 'offline'
            component.healthy = False
            component.last_error = f'exited (code {getattr(proc, "returncode", None)})'
            return False
        try:
            p = self._http_get(component.port, '/health')
            ok = bool(p.get('ok'))
            component.healthy = ok
            component.process_state = p.get('state', 'ready')
            if ok:
                component.last_health_ok_at = time.time()
            return ok
        except Exception as exc:
            component.healthy = False
            component.last_error = str(exc)
            return False

    def _wait_ready(self, group: List[ProcessEntry], timeout: int = 45) -> None:
        start = time.time()
        while time.time() - start < timeout and not self.shutdown_event.is_set():
            if all(self._check_health(c) for c in group):
                return
            time.sleep(1)
        raise RuntimeError('FLINT readiness timeout: ' + ', '.join(c.name for c in group))

    def start_tiers(self) -> None:
        """Dependency-group parallel startup. Same-tier components start together."""
        for tier in sorted({c.tier for c in self.components.values()}):
            group = [c for c in self.components.values() if c.tier == tier]
            for c in group:
                self._start_component(c)
            self._wait_ready(group)
            self.logger.info('FLINT tier %s ready: %s', tier, [c.name for c in group])

    def _heartbeat_loop(self) -> None:
        hb = Path(self.config['flint']['heartbeat_file'])
        while not self.shutdown_event.is_set():
            hb.parent.mkdir(parents=True, exist_ok=True)
            hb.write_text(str(time.time()))
            time.sleep(self.config['flint']['heartbeat_interval_seconds'])

    def _check_llm_health(self) -> Dict[str, Any]:
        """
        Check LLM server liveness via /health endpoint.
        Returns a health dict with ok, latency_ms, error, and checked_at.
        Does not own model selection or inference — only liveness probing.
        """
        llm_cfg = self.config.get('llm', {})
        llm_url = llm_cfg.get('base_url', 'http://127.0.0.1:8080')
        health_url = llm_url.rstrip('/') + '/health'
        t0 = time.time()
        try:
            with request.urlopen(health_url, timeout=5) as r:
                latency_ms = round((time.time() - t0) * 1000, 1)
                ok = r.status == 200
                return {'ok': ok, 'latency_ms': latency_ms, 'error': None, 'checked_at': t0}
        except Exception as exc:
            latency_ms = round((time.time() - t0) * 1000, 1)
            return {'ok': False, 'latency_ms': latency_ms, 'error': str(exc), 'checked_at': t0}

    def _llm_health_loop(self) -> None:
        """Poll LLM health every 30 seconds. Log ERROR after 3 consecutive failures."""
        poll_interval = 30
        while not self.shutdown_event.is_set():
            result = self._check_llm_health()
            self._llm_health = result
            if result['ok']:
                if self._llm_down_count > 0:
                    self.logger.info('FLINT LLM health restored after %s failures', self._llm_down_count)
                self._llm_down_count = 0
            else:
                self._llm_down_count += 1
                if self._llm_down_count >= 3:
                    self.logger.error(
                        'FLINT LLM unreachable (%s consecutive failures): %s',
                        self._llm_down_count, result.get('error'),
                    )
                else:
                    self.logger.warning(
                        'FLINT LLM health check failed (%s): %s',
                        self._llm_down_count, result.get('error'),
                    )
            self.shutdown_event.wait(timeout=poll_interval)

    def _maybe_restart(self, component: ProcessEntry) -> None:
        now = time.time()
        if (component.restart_attempts >= self.config['flint']['max_restart_attempts']
                or now < component.next_restart_at):
            return
        delays = self.config['flint']['restart_backoff_seconds']
        delay = delays[min(component.restart_attempts, len(delays) - 1)]
        component.restart_attempts += 1
        component.next_restart_at = now + delay
        self.logger.warning('FLINT restarting %s (attempt %s, backoff %ss): %s',
                            component.name, component.restart_attempts, delay, component.last_error)
        proc = self.processes.get(component.name)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._start_component(component)

    def monitor_loop(self) -> None:
        stale = self.config['flint']['heartbeat_stale_after_seconds']
        # Grace period: allow components to write first heartbeat before monitoring
        grace = stale  # same as stale threshold — typically 15s
        self.logger.info('FLINT monitor waiting %ss for components to stabilise', grace)
        self.shutdown_event.wait(timeout=grace)
        while not self.shutdown_event.is_set():
            for component in self.components.values():
                ok = self._check_health(component)
                hb = Path(component.heartbeat_file)
                if hb.exists():
                    age = time.time() - hb.stat().st_mtime
                    if age > stale:
                        ok = False
                        component.last_error = f'heartbeat stale ({age:.1f}s)'
                else:
                    # No heartbeat file yet — only flag as error if process is also dead
                    proc = self.processes.get(component.name)
                    if proc is not None and proc.poll() is None:
                        # Process alive, heartbeat not yet written — give it time
                        ok = True
                    else:
                        ok = False
                        component.last_error = 'missing heartbeat'
                if not ok and not self.shutdown_event.is_set():
                    self._maybe_restart(component)
                elif ok:
                    component.restart_attempts = 0
            time.sleep(self.config['flint']['health_interval_seconds'])

    def _serve_status(self) -> None:
        """PRISM reads from /api/flint/status. All operators use /v1/chat/completions."""
        llm_cfg = self.config.get('llm', {})
        llm_url   = llm_cfg.get('base_url', 'http://127.0.0.1:8080')
        llm_model = llm_cfg.get('model', 'qwen2.5-3b-instruct-q4_k_m.gguf')
        llm_completions_url = llm_url.rstrip('/') + '/v1/chat/completions'
        flint = self
        port = self.config['flint']['status_port']

        class Handler(BaseHTTPRequestHandler):
            def _cors(self) -> None:
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')

            def _send(self, code: int, body: Dict[str, Any]) -> None:
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self._cors()
                self.end_headers()
                self.wfile.write(raw)

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(200)
                self._cors()
                self.end_headers()

            def _read_body(self) -> bytes:
                length = int(self.headers.get('Content-Length', 0))
                return self.rfile.read(length) if length else b'{}'

            def do_GET(self) -> None:  # noqa: N802
                if self.path == '/health':
                    self._send(200, {'component': 'flint', 'state': flint.process_state,
                                     'version': VERSION_SHORT,
                                     'ok': flint.process_state in {'ready', 'draining'}})
                elif self.path == '/api/flint/status':
                    comps = list(flint.components.values())
                    self._send(200, {'component': 'flint', 'version': VERSION_SHORT,
                                     'state': flint.process_state,
                                     'components_healthy': sum(1 for c in comps if c.healthy),
                                     'components_total': len(comps),
                                     'components': [asdict(c) for c in comps],
                                     'llm': flint._llm_health})
                elif self.path == '/api/flint/llm_health':
                    self._send(200, flint._llm_health)
                else:
                    self._send(404, {'error': 'not found'})


            def do_POST(self) -> None:  # noqa: N802
                if self.path == '/v1/chat/completions':
                    self._proxy_llm()
                else:
                    self._send(404, {'error': 'not found'})

            def _proxy_llm(self) -> None:
                """Pass OpenAI-compatible request through to local llama.cpp server."""
                try:
                    body = json.loads(self._read_body())
                    # Ensure model name matches what llama.cpp is serving
                    body['model'] = llm_model
                    # llama.cpp handles system messages inside the messages array natively
                    req_data = json.dumps(body).encode()
                    req = request.Request(
                        llm_completions_url,
                        data=req_data,
                        method='POST',
                        headers={'Content-Type': 'application/json'}
                    )
                    with request.urlopen(req, timeout=120) as r:
                        raw = r.read()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw)))
                    self._cors()
                    self.end_headers()
                    self.wfile.write(raw)
                except Exception as exc:
                    flint.logger.error('LLM proxy error: %s', exc)
                    self._send(502, {'error': 'LLM proxy error', 'detail': str(exc)})

            def log_message(self, fmt: str, *args: Any) -> None:
                flint.logger.debug(fmt, *args)

        self._status_server = ReusableHTTPServer(('0.0.0.0', port), Handler)
        self.logger.info('FLINT status server on port %s', port)
        self._status_server.serve_forever(poll_interval=0.5)

    def handle_signal(self, signum: int, _frame: Any) -> None:
        self.logger.info('FLINT received signal %s — graceful shutdown', signum)
        self.process_state = 'draining'
        self.shutdown_event.set()
        self.graceful_shutdown()

    def graceful_shutdown(self) -> None:
        timeout = self.config['flint']['drain_timeout_seconds']
        for tier in sorted({c.tier for c in self.components.values()}, reverse=True):
            group = [c for c in self.components.values() if c.tier == tier]
            for c in group:
                try:
                    self._http_post(c.port, '/drain', {})
                except Exception:
                    pass
            deadline = time.time() + timeout
            for c in group:
                proc = self.processes.get(c.name)
                if proc is None:
                    continue
                try:
                    proc.wait(timeout=max(0.5, deadline - time.time()))
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                c.process_state = 'offline'
        if self._status_server:
            self._status_server.shutdown()
        self.process_state = 'offline'
        self.logger.info('FLINT offline')

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.handle_signal)
        signal.signal(signal.SIGINT, self.handle_signal)
        threading.Thread(target=self._heartbeat_loop, daemon=True, name='flint-hb').start()
        threading.Thread(target=self._serve_status, daemon=True, name='flint-status').start()
        threading.Thread(target=self._llm_health_loop, daemon=True, name='flint-llm-health').start()
        self.start_tiers()
        self.process_state = 'ready'
        self.logger.info('FLINT ready — Cascadia OS v' + VERSION)
        self.monitor_loop()


def main() -> None:
    p = argparse.ArgumentParser(description='FLINT — Cascadia OS control plane')
    p.add_argument('--config', required=True)
    Flint(p.parse_args().config).run()


if __name__ == '__main__':
    main()
