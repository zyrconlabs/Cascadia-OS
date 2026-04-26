#!/usr/bin/env python3
"""
My Operator — Cascadia OS Operator Template
Owns: [your operator's responsibility]
Does not own: storage (VAULT), routing (BEACON), risk classification (SENTINEL)
"""
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Add sdk to path for cascadia_sdk
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from cascadia_sdk import vault_store, vault_get, sentinel_check, beacon_route, crew_register
except ImportError:
    # Graceful fallback if SDK not available
    def vault_store(k, v): return False
    def vault_get(k): return None
    def sentinel_check(a, c=None): return True
    def beacon_route(t, p): return {}
    def crew_register(m): return False

MANIFEST_PATH = Path(__file__).parent / 'manifest.json'
MANIFEST = json.loads(MANIFEST_PATH.read_text())
PORT = int(os.environ.get('OPERATOR_PORT', MANIFEST.get('port', 9000)))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self._json(200, {'ok': True, 'id': MANIFEST['id'], 'version': MANIFEST['version']})
        else:
            self._json(404, {'error': 'not found'})

    def do_POST(self):
        n = int(self.headers.get('Content-Length', '0'))
        body = json.loads(self.rfile.read(n)) if n else {}

        if self.path == '/api/run':
            # Check SENTINEL before acting
            if not sentinel_check('custom_capability', {'context': body}):
                self._json(403, {'error': 'action not permitted'})
                return

            # Store result in VAULT
            result = {'echo': body, 'status': 'ok'}
            vault_store(f'my_operator:last_result', json.dumps(result))

            # Return with confidence score for self-escalation
            self._json(200, {
                'output': result,
                'confidence': 0.95,
                'escalate_if_below': 0.80,
                'escalation_reason': ''
            })
        else:
            self._json(404, {'error': 'not found'})

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a): pass

def main():
    # Register with CREW on startup
    crew_register(MANIFEST)
    print(f'[{MANIFEST["id"]}] running on port {PORT}')
    HTTPServer(('127.0.0.1', PORT), Handler).serve_forever()

if __name__ == '__main__':
    main()
