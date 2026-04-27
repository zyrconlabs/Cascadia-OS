# MATURITY: PRODUCTION — Minimal HTTP wrapper for supervised services.
from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
import os
import signal
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .logger import configure_logging

_WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'


def _match_path(pattern: str, path: str) -> Optional[Dict[str, str]]:
    """Match URL path against a pattern with {param} segments. Returns params dict or None."""
    pp = pattern.split('/')
    rp = path.split('/')
    if len(pp) != len(rp):
        return None
    params: Dict[str, str] = {}
    for a, b in zip(pp, rp):
        if a.startswith('{') and a.endswith('}'):
            params[a[1:-1]] = b
        elif a != b:
            return None
    return params


def _ws_accept_key(key: str) -> str:
    combined = (key.strip() + _WS_GUID).encode('utf-8')
    return base64.b64encode(hashlib.sha1(combined).digest()).decode('ascii')


def _ws_frame(data: bytes) -> bytes:
    length = len(data)
    if length < 126:
        return bytes([0x81, length]) + data
    elif length < 65536:
        return bytes([0x81, 126]) + struct.pack('>H', length) + data
    else:
        return bytes([0x81, 127]) + struct.pack('>Q', length) + data


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
        self._ws_paths: set = set()
        self._ws_clients: List[Any] = []
        self._ws_lock = threading.Lock()

    def register_route(self, method: str, path: str, handler: Callable[[Dict[str, Any]], tuple[int, Dict[str, Any]]]) -> None:
        """Owns route registration. Does not own request semantics beyond method/path dispatch."""
        self._routes[(method.upper(), path)] = handler

    def register_ws_route(self, path: str) -> None:
        """Register a path as a WebSocket upgrade endpoint."""
        self._ws_paths.add(path)

    def broadcast_event(self, event: Dict[str, Any]) -> None:
        """Send a JSON event to all connected WebSocket clients. Dead connections are pruned."""
        frame = _ws_frame(json.dumps(event).encode('utf-8'))
        with self._ws_lock:
            dead = []
            for sock in self._ws_clients:
                try:
                    sock.sendall(frame)
                except Exception:
                    dead.append(sock)
            for s in dead:
                self._ws_clients.remove(s)

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
        clean = path.split('?')[0]
        # Exact match (highest priority)
        if (method, clean) in self._routes:
            return self._routes[(method, clean)](payload)
        # Path-param pattern match (e.g. /api/workflows/{id}/runs)
        for (m, pattern), handler in self._routes.items():
            if m != method or '{' not in pattern:
                continue
            params = _match_path(pattern, clean)
            if params is not None:
                return handler({**payload, **params})
        if method == 'GET' and clean == '/health':
            return 200, {'component': self.name, 'state': self.state, 'ok': self.state in {'ready', 'degraded', 'draining'}}
        if method == 'POST' and clean == '/drain':
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
            def _cors_headers(self) -> None:
                origin = self.headers.get('Origin', '*')
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, Stripe-Signature')
                self.send_header('Access-Control-Max-Age', '86400')

            def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
                body = json.dumps(payload).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self._cors_headers()
                self.end_headers()
                self.wfile.write(body)

            def _read_payload(self) -> Dict[str, Any]:
                n = int(self.headers.get('Content-Length', '0'))
                data = json.loads(self.rfile.read(n).decode('utf-8')) if n else {}
                # Inject request metadata so handlers can use it
                data['__remote_addr__'] = self.client_address[0]
                data['__headers__'] = dict(self.headers)
                return data

            def _check_internal_key(self) -> bool:
                expected = os.environ.get('CASCADIA_INTERNAL_KEY', '')
                if not expected:
                    return True  # auth not enabled
                # Exempt health paths
                clean = self.path.split('?')[0]
                if clean in ('/health', '/api/health', '/api/enterprise/health'):
                    return True
                provided = self.headers.get('X-Cascadia-Key', '')
                if not _hmac.compare_digest(expected, provided):
                    self._send_json(401, {'error': 'unauthorized', 'message': 'Valid X-Cascadia-Key required'})
                    return False
                return True

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(204)
                self._cors_headers()
                self.end_headers()

            def _send_html(self, code: int, body: bytes) -> None:
                self.send_response(code)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if (self.headers.get('Upgrade', '').lower() == 'websocket'
                        and self.path in runtime._ws_paths):
                    self._handle_ws_upgrade()
                    return
                if not self._check_internal_key():
                    return
                code, payload = runtime.route_request('GET', self.path, {})
                if isinstance(payload, dict) and '__html__' in payload:
                    self._send_html(code, payload['__html__'])
                else:
                    self._send_json(code, payload)

            def _handle_ws_upgrade(self) -> None:
                key = self.headers.get('Sec-WebSocket-Key', '')
                accept = _ws_accept_key(key)
                self.send_response(101, 'Switching Protocols')
                self.send_header('Upgrade', 'websocket')
                self.send_header('Connection', 'Upgrade')
                self.send_header('Sec-WebSocket-Accept', accept)
                self.end_headers()
                self.wfile.flush()
                sock = self.request
                with runtime._ws_lock:
                    runtime._ws_clients.append(sock)
                runtime.logger.info('WS client connected: %s', self.client_address)
                try:
                    while not runtime._shutdown.is_set():
                        header = sock.recv(2)
                        if len(header) < 2:
                            break
                        opcode = header[0] & 0x0F
                        if opcode == 8:  # close frame
                            break
                        masked = (header[1] & 0x80) != 0
                        plen = header[1] & 0x7F
                        if plen == 126:
                            plen = int.from_bytes(sock.recv(2), 'big')
                        elif plen == 127:
                            plen = int.from_bytes(sock.recv(8), 'big')
                        if masked:
                            sock.recv(4)  # consume masking key
                        if plen > 0:
                            sock.recv(plen)  # consume payload
                except Exception:
                    pass
                finally:
                    with runtime._ws_lock:
                        try:
                            runtime._ws_clients.remove(sock)
                        except ValueError:
                            pass
                    runtime.logger.info('WS client disconnected')

            def do_POST(self) -> None:  # noqa: N802
                if not self._check_internal_key():
                    return
                try:
                    code, payload = runtime.route_request('POST', self.path, self._read_payload())
                    self._send_json(code, payload)
                except Exception as exc:
                    runtime.logger.error('POST %s error: %s', self.path, exc, exc_info=True)
                    self._send_json(500, {'error': str(exc)})

            def do_DELETE(self) -> None:  # noqa: N802
                if not self._check_internal_key():
                    return
                try:
                    code, payload = runtime.route_request('DELETE', self.path, {})
                    self._send_json(code, payload)
                except Exception as exc:
                    runtime.logger.error('DELETE %s error: %s', self.path, exc, exc_info=True)
                    self._send_json(500, {'error': str(exc)})

            def log_message(self, format: str, *args: Any) -> None:
                runtime.logger.info(format, *args)

        self._httpd = ReusableHTTPServer(('0.0.0.0', self.port), Handler)
        self.logger.info('Starting service HTTP server on 0.0.0.0:%s', self.port)
        try:
            self._httpd.serve_forever(poll_interval=0.5)
        finally:
            self.state = 'offline'
            self.logger.info('Service stopped')
