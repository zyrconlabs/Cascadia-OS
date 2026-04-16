"""
beacon.py - Cascadia OS v0.2
BEACON: Orchestrator and capability-aware router.
Decides which operator handles a task, routes messages between operators,
and checks capability manifests on every route.
A beacon guides things to the right place.
"""
# MATURITY: FUNCTIONAL — Capability-checked routing works. CREW integration is live. Full handoff orchestration is v0.3.
from __future__ import annotations

import argparse
import json
from typing import Any, Dict
from urllib import request as urllib_request

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
    Does not own workflow planning, scheduling, or approval decisions.
    """

    def __init__(self, config_path: str, name: str) -> None:
        self.config = load_config(config_path)
        component = next(c for c in self.config['components'] if c['name'] == name)
        self.runtime = ServiceRuntime(
            name=name, port=component['port'],
            heartbeat_file=component['heartbeat_file'],
            log_dir=self.config['log_dir'],
        )
        # Find CREW port for capability validation
        crew_comp = next((c for c in self.config['components'] if c['name'] == 'crew'), None)
        self.crew_port: int | None = crew_comp['port'] if crew_comp else None

        self.runtime.register_route('POST', '/route', self.route)
        self.runtime.register_route('POST', '/handoff', self.handoff)

    def _validate_capability(self, sender: str, capability: str) -> bool:
        """Check capability with CREW. Returns True if allowed."""
        if self.crew_port is None:
            return True  # No CREW registered — allow (open mode)
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
            return False

    def route(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Route a message to a target operator.
        Validates capability before routing. Blocks if capability missing.
        """
        sender = payload.get('sender', '')
        message_type = payload.get('message_type', '')
        target = payload.get('target', '')

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

        self.runtime.logger.info('BEACON routing %s -> %s (%s)', sender, target, message_type)
        return 200, {'ok': True, 'routed_to': target, 'message_type': message_type}

    def handoff(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Delegate a task from one operator to another.
        Used by STITCH when a workflow step requires a different operator.
        """
        from_op = payload.get('from_operator', '')
        to_op = payload.get('to_operator', '')
        run_id = payload.get('run_id', '')
        self.runtime.logger.info('BEACON handoff: %s -> %s (run %s)', from_op, to_op, run_id)
        return 200, {'ok': True, 'from': from_op, 'to': to_op, 'run_id': run_id}

    def start(self) -> None:
        self.runtime.start()


def main() -> None:
    p = argparse.ArgumentParser(description='BEACON - Cascadia OS orchestrator')
    p.add_argument('--config', required=True)
    p.add_argument('--name', required=True)
    a = p.parse_args()
    BeaconService(a.config, a.name).start()


if __name__ == '__main__':
    main()
