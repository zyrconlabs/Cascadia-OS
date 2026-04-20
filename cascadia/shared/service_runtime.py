# MATURITY: PRODUCTION — Minimal HTTP wrapper for supervised services.
from __future__ import annotations

import json
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict

from .logger import configure_logging


class ReusableHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    allow_reuse_port = True


class ServiceRuntime:
    """Owns the minimal local HTTP wrapper for supervised services. Does not own business logic."""

    def __init__(self, *, name: str, port: int, heartbeat_file: str, log_dir: str, ready_delay_seconds: float = 0.5) -> None:
        self.name = name
        self.port = port
        self.heartbeat_file = Path(heartbeat_file)
        self.log_dir = log_dir
        self.ready_delay_seconds = ready_delay_seconds
        self.logger = configure_logging(log_dir, name)
        self.state = 'starting'
        self._shutdown = threading.Event()
        self._httpd: ReusableHTTPServer | None = None
        self._routes: Dict[tuple[str, str], Callable[[Dict[str, Any]], tuple[int, Dict[str, Any]]]] = {}

    def register_route(self, method: str, path: str, handler: Callable[[Dict[str, Any]], tuple[int, Dict[str, Any]]]) -> None:
        """Owns route registration. Does not own request semantics beyond method/path dispatch."""
        self._routes[(method.upper(), path)] = handler

    def _heartbeat_loop(self) -> None:
        while not self._shutdown.is_set():
            self.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
            self.heartbeat_file.write_text(str(time.time()), encoding='utf-8')
            time.sleep(5)

    def _ready_after_delay(self) -> None:
        time.sleep(self.ready_delay_seconds)
        if not self._shutdown.is_set():
            self.state = 'ready'
            self.logger.info('Service ready on port %s', self.port)

    def on_sigterm(self, *_: Any) -> None:
        """Owns graceful stop for the service wrapper. Does not own persistence beyond process state."""
        self.state = 'draining'
        self._shutdown.set()
        if self._httpd is not None:
            self._httpd.shutdown()

    def route_request(self, method: str, path: str, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Owns HTTP route dispatch. Does not own unknown-route recovery beyond 404."""
        if (method, path) in self._routes:
            return self._routes[(method, path)](payload)
        if method == 'GET' and path == '/health':
            return 200, {'component': self.name, 'state': self.state, 'ok': self.state in {'ready', 'degraded', 'draining'}}
        if method == 'POST' and path == '/drain':
            self.state = 'draining'
            self._shutdown.set()
            return 202, {'component': self.name, 'state': self.state}
        return 404, {'error': f'Unknown route {method} {path}'}

    def start(self) -> None:
        """Owns the service HTTP loop. Does not own kernel supervision."""
        signal.signal(signal.SIGTERM, self.on_sigterm)
        signal.signal(signal.SIGINT, self.on_sigterm)
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        threading.Thread(target=self._ready_after_delay, daemon=True).start()
        runtime = self

        class Handler(BaseHTTPRequestHandler):
            def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
                body = json.dumps(payload).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_payload(self) -> Dict[str, Any]:
                n = int(self.headers.get('Content-Length', '0'))
                return json.loads(self.rfile.read(n).decode('utf-8')) if n else {}

            def _send_html(self, code: int, body: bytes) -> None:
                self.send_response(code)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                code, payload = runtime.route_request('GET', self.path, {})
                if isinstance(payload, dict) and '__html__' in payload:
                    self._send_html(code, payload['__html__'])
                else:
                    self._send_json(code, payload)

            def do_POST(self) -> None:  # noqa: N802
                code, payload = runtime.route_request('POST', self.path, self._read_payload())
                self._send_json(code, payload)

            def log_message(self, format: str, *args: Any) -> None:
                runtime.logger.info(format, *args)

        self._httpd = ReusableHTTPServer(('127.0.0.1', self.port), Handler)
        self.logger.info('Starting service HTTP server on 127.0.0.1:%s', self.port)
        try:
            self._httpd.serve_forever(poll_interval=0.5)
        finally:
            self.state = 'offline'
            self.logger.info('Service stopped')
