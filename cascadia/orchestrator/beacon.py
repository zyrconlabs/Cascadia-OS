"""
beacon.py - Cascadia OS v0.44
BEACON: Orchestrator and capability-aware router.
Decides which operator handles a task, routes messages between operators,
checks capability manifests, and forwards requests to target operator ports.
A beacon guides things to the right place.
"""
# MATURITY: PRODUCTION — Capability-checked routing, CREW validation, and live
# HTTP forwarding to operator ports all implemented.
from __future__ import annotations

import argparse
import json
from typing import Any, Dict, Optional
from urllib import request as urllib_request
from urllib.error import URLError, HTTPError

from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime

# Actions that require a capability check before routing
_CAPABILITY_MAP: Dict[str, str] = {
    'run.execute':      'run.execute',
    'vault.read':       'vault.read',
    'vault.write':      'vault.write',
    'email.send':       'email.send',
    'email.read':       'email.read',
    'crm.write':        'crm.write',
    'calendar.read':    'calendar.read',
    'calendar.write':   'calendar.write',
    'browser.submit':   'browser.use',
    'invoice.create':   'payments.create',
    'file.delete':      'files.write',
    'shell.exec':       'shell.exec',
}


class BeaconService:
    """
    BEACON - Owns capability-checked task routing and operator handoffs.
    Routes validated requests to target operator HTTP ports.
    Does not own workflow planning, scheduling, or approval decisions.
    """

    def __init__(self, config_path: str, name: str) -> None:
        self.config = load_config(config_path)
        self._config = self.config  # alias for specs-aware routing methods
        component = next(c for c in self.config['components'] if c['name'] == name)
        self.runtime = ServiceRuntime(
            name=name, port=component['port'],
            heartbeat_file=component['heartbeat_file'],
            log_dir=self.config['log_dir'],
        )
        # Build port map from config — all registered components
        self._port_map: Dict[str, int] = {
            c['name']: c['port']
            for c in self.config.get('components', [])
        }
        crew_comp = next((c for c in self.config['components'] if c['name'] == 'crew'), None)
        self.crew_port: Optional[int] = crew_comp['port'] if crew_comp else None

        self.runtime.register_route('POST', '/route',    self.route)
        self.runtime.register_route('POST', '/handoff',  self.handoff)
        self.runtime.register_route('POST', '/forward',  self.forward)
        self.runtime.register_route('GET',  '/registry', self.registry)

    # ------------------------------------------------------------------
    # Capability validation
    # ------------------------------------------------------------------

    def _validate_capability(self, sender: str, capability: str) -> bool:
        """Check capability with CREW. Returns True if allowed."""
        if self.crew_port is None:
            return True  # No CREW — allow (open mode)
        try:
            data = json.dumps({'sender': sender, 'capability': capability}).encode()
            req = urllib_request.Request(
                f'http://127.0.0.1:{self.crew_port}/validate',
                data=data, method='POST',
                headers={'Content-Type': 'application/json'},
            )
            with urllib_request.urlopen(req, timeout=2) as r:
                result = json.loads(r.read().decode())
                return bool(result.get('ok', False))
        except Exception:
            return True  # CREW unreachable — fail open to avoid blocking runs

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def route(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Route a message to a target operator after capability check.
        If the target operator is a registered component with a known port,
        forwards the message payload via HTTP and returns the real response.
        """
        sender       = payload.get('sender', '')
        message_type = payload.get('message_type', '')
        target       = payload.get('target', '')
        message      = payload.get('message', {})
        forward_path = payload.get('path', '/message')
        timeout      = int(payload.get('timeout', 5))

        # Capability check
        required_cap = _CAPABILITY_MAP.get(message_type)
        if required_cap and sender:
            if not self._validate_capability(sender, required_cap):
                self.runtime.logger.warning(
                    'BEACON capability denied: %s needs %s for %s',
                    sender, required_cap, message_type,
                )
                return 403, {
                    'ok': False,
                    'reason': 'capability_denied',
                    'sender': sender,
                    'required': required_cap,
                }

        self.runtime.logger.info(
            'BEACON routing %s -> %s (%s)', sender, target, message_type
        )

        # Forward to target operator port if known
        target_port = self._port_map.get(target)
        if target_port and message:
            forwarded_status, forwarded_body = self._forward_http(
                target_port, forward_path, message, timeout
            )
            return 200, {
                'ok': True,
                'routed_to': target,
                'message_type': message_type,
                'forwarded': True,
                'forward_status': forwarded_status,
                'forward_response': forwarded_body,
            }

        # No port known or no message to forward — acknowledge only
        return 200, {
            'ok': True,
            'routed_to': target,
            'message_type': message_type,
            'forwarded': False,
            'note': 'Target port not registered — acknowledged only',
        }

    def handoff(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Delegate a task from one operator to another.
        Forwards the task payload to the target operator's /task endpoint.
        """
        from_op  = payload.get('from_operator', '')
        to_op    = payload.get('to_operator', '')
        run_id   = payload.get('run_id', '')
        task     = payload.get('task', {})
        timeout  = int(payload.get('timeout', 5))

        self.runtime.logger.info(
            'BEACON handoff: %s -> %s (run %s)', from_op, to_op, run_id
        )

        target_port = self._port_map.get(to_op)
        if target_port and task:
            status, body = self._forward_http(target_port, '/task', {
                'run_id': run_id,
                'from_operator': from_op,
                **task,
            }, timeout)
            return 200, {
                'ok': True,
                'from': from_op,
                'to': to_op,
                'run_id': run_id,
                'forwarded': True,
                'forward_status': status,
                'forward_response': body,
            }

        return 200, {
            'ok': True,
            'from': from_op,
            'to': to_op,
            'run_id': run_id,
            'forwarded': False,
        }

    def forward(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Direct HTTP forward to a named component's port.
        Skips capability check — caller is responsible for authorization.
        Used for internal component-to-component calls.
        """
        target  = payload.get('target', '')
        path    = payload.get('path', '/health')
        method  = payload.get('method', 'POST').upper()
        body    = payload.get('body', {})
        timeout = int(payload.get('timeout', 5))

        target_port = self._port_map.get(target)
        if not target_port:
            return 404, {'ok': False, 'error': f'target not registered: {target}'}

        status, response = self._forward_http(target_port, path, body, timeout, method)
        return 200, {
            'ok': status < 400,
            'target': target,
            'path': path,
            'forward_status': status,
            'response': response,
        }

    def registry(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Return the full port registry BEACON knows about."""
        return 200, {
            'registered': self._port_map,
            'count': len(self._port_map),
        }

    # ------------------------------------------------------------------
    # HTTP forwarding
    # ------------------------------------------------------------------

    def _forward_http(
        self,
        port: int,
        path: str,
        body: Dict[str, Any],
        timeout: int,
        method: str = 'POST',
    ) -> tuple[int, Any]:
        """
        Forward a JSON payload to a local component HTTP port.
        Returns (http_status, response_body).
        Falls back gracefully on connection errors.
        """
        url = f'http://127.0.0.1:{port}{path}'
        try:
            data = json.dumps(body).encode('utf-8') if body else None
            req = urllib_request.Request(
                url,
                data=data,
                method=method,
                headers={'Content-Type': 'application/json'},
            )
            with urllib_request.urlopen(req, timeout=timeout) as r:
                response_body = json.loads(r.read().decode())
                return r.status, response_body
        except HTTPError as e:
            body_text = e.read().decode('utf-8', errors='replace')[:200]
            self.runtime.logger.warning('BEACON forward %s -> HTTP %s', url, e.code)
            try:
                return e.code, json.loads(body_text)
            except Exception:
                return e.code, {'error': body_text}
        except URLError as e:
            self.runtime.logger.warning('BEACON forward %s -> unreachable: %s', url, e)
            return 503, {'error': f'target unreachable: {e.reason}'}
        except Exception as e:
            self.runtime.logger.warning('BEACON forward %s -> error: %s', url, e)
            return 500, {'error': str(e)}

    def _get_platform_specs(self) -> dict:
        import json
        from pathlib import Path
        platform_id = self._config.get('hardware_platform', 'zyrcon-mac')
        specs_path = Path(__file__).parent.parent.parent / 'hardware' / platform_id / 'specs.json'
        try:
            return json.loads(specs_path.read_text())
        except Exception:
            return {}

    def _can_handle_model(self, model_id: str) -> bool:
        specs = self._get_platform_specs()
        bandwidth = specs.get('memory_bandwidth_gbs', 0)
        requirements = {'3b': 10, '7b': 50, '14b': 100, '32b': 200, '70b': 500}
        for size, required in requirements.items():
            if size in model_id.lower():
                return bandwidth >= required
        return True  # unknown model — allow

    def _get_capable_fleet_nodes(self, model_id: str) -> list:
        """Query fleet for nodes capable of running the model."""
        try:
            import urllib.request, json
            prism_port = self._port_map.get('prism', 6300)
            resp = urllib.request.urlopen(f'http://127.0.0.1:{prism_port}/api/prism/fleet', timeout=2)
            nodes = json.loads(resp.read())
            requirements = {'3b': 10, '7b': 50, '14b': 100, '32b': 200, '70b': 500}
            capable = []
            for node in nodes:
                bw = node.get('specs', {}).get('memory_bandwidth_gbs', 0)
                node_ok = True
                for size, req in requirements.items():
                    if size in model_id.lower():
                        node_ok = bw >= req
                        break
                if node_ok and node.get('status') == 'online':
                    capable.append(node)
            return sorted(capable, key=lambda n: n.get('specs', {}).get('memory_bandwidth_gbs', 0), reverse=True)
        except Exception:
            return []

    def start(self) -> None:
        self.runtime.logger.info(
            'BEACON active — %d components registered', len(self._port_map)
        )
        self.runtime.start()


def main() -> None:
    p = argparse.ArgumentParser(description='BEACON - Cascadia OS orchestrator')
    p.add_argument('--config', required=True)
    p.add_argument('--name', required=True)
    a = p.parse_args()
    BeaconService(a.config, a.name).start()


if __name__ == '__main__':
    main()
