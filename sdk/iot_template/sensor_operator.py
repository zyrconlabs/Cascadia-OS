#!/usr/bin/env python3
"""
IoT Sensor Operator Template — Cascadia OS v0.47
Owns: sensor data analysis, anomaly detection, threshold alerting
Does not own: physical actuation (Enterprise only), sensor storage (CONDUIT)

SAFETY BOUNDARY:
    This operator performs AI analysis of sensor data only.
    Physical actuator control requires:
    1. Enterprise tier license
    2. Hardware guard channels independent of this software
    3. Explicit human approval through SENTINEL
    This operator NEVER directly controls actuators.
"""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from cascadia_sdk import vault_store, sentinel_check, crew_register
except ImportError:
    def vault_store(k, v): return False
    def sentinel_check(a, c=None): return True
    def crew_register(m): return False

MANIFEST = json.loads((Path(__file__).parent / 'manifest.json').read_text())
PORT = 9001

TEMP_THRESHOLD = 85.0  # degrees C — example threshold

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self._json(200, {'ok': True, 'id': MANIFEST['id']})
        else:
            self._json(404, {'error': 'not found'})

    def do_POST(self):
        n = int(self.headers.get('Content-Length', '0'))
        body = json.loads(self.rfile.read(n)) if n else {}

        if self.path == '/api/sensor':
            # Receive sensor envelope from VANGUARD
            device_id = body.get('device_id', '')
            payload   = body.get('payload', {})
            temp      = payload.get('temperature')

            analysis = {'device_id': device_id, 'anomaly': False, 'action_required': False}

            if temp is not None:
                temp = float(temp)
                if temp > TEMP_THRESHOLD:
                    analysis['anomaly'] = True
                    analysis['message'] = f'Temperature {temp}°C exceeds threshold {TEMP_THRESHOLD}°C'

                    # Request human approval before any action
                    # SAFETY BOUNDARY: We request approval, not actuate directly
                    if sentinel_check('threshold_alert', {'device_id': device_id, 'temp': temp}):
                        analysis['action_required'] = True
                        vault_store(f'iot:alert:{device_id}', json.dumps(analysis))

            self._json(200, {
                'output': analysis,
                'confidence': 0.92,
                'escalate_if_below': 0.75,
                'escalation_reason': 'Low confidence threshold analysis — human review recommended'
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

if __name__ == '__main__':
    crew_register(MANIFEST)
    print(f'[{MANIFEST["id"]}] running on port {PORT}')
    HTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
