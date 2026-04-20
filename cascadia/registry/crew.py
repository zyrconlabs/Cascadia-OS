"""
crew.py - Cascadia OS v0.43
CREW: Operator group registry and message hub.
Tracks registered operators and routes messages between them.
Validates capability manifests on every inbound route.
It routes. It does not execute.

Business owner view: A Crew is the group of operators working together
on your tasks. PRISM shows you who is in your Crew and what they are doing.
"""
# MATURITY: FUNCTIONAL — Wildcard capability validation works. Heartbeat tracking is v0.3.
from __future__ import annotations

import argparse
from typing import Any, Dict

from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime


class CrewService:
    """
    CREW - Owns operator registration, capability tracking, and group membership.
    Does not own workflow planning or durable run execution.
    """

    def __init__(self, config_path: str, name: str) -> None:
        config = load_config(config_path)
        component = next(c for c in config['components'] if c['name'] == name)
        self.runtime = ServiceRuntime(
            name=name, port=component['port'],
            heartbeat_file=component['heartbeat_file'],
            log_dir=config['log_dir'],
        )
        self.registry: Dict[str, Dict[str, Any]] = {}
        self.runtime.register_route('POST', '/register', self.register)
        self.runtime.register_route('POST', '/validate', self.validate)
        self.runtime.register_route('GET', '/crew', self.list_crew)
        self.runtime.register_route('POST', '/deregister', self.deregister)

    def register(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Register an operator into the Crew."""
        op_id = payload.get('operator_id')
        if not op_id:
            return 400, {'error': 'operator_id required'}
        self.registry[op_id] = payload
        self.runtime.logger.info('CREW registered operator: %s', op_id)
        return 201, {'registered': op_id, 'crew_size': len(self.registry)}

    def deregister(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Remove an operator from the Crew."""
        op_id = payload.get('operator_id')
        self.registry.pop(op_id, None)
        return 200, {'removed': op_id}

    def validate(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Validate that a sender holds a required capability."""
        sender = payload.get('sender', '')
        capability = payload.get('capability', '')
        manifest = self.registry.get(sender, {}).get('capabilities', [])
        # Support wildcard: crm.* covers crm.read and crm.write
        allowed = capability in manifest or any(
            capability.startswith(c[:-1]) for c in manifest if c.endswith('*')
        )
        return 200, {'ok': allowed, 'sender': sender, 'capability': capability}

    def list_crew(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Return all registered operators — PRISM displays this."""
        return 200, {
            'crew_size': len(self.registry),
            'operators': {
                op_id: {
                    'operator_id': op_id,
                    'type': rec.get('type', 'unknown'),
                    'autonomy_level': rec.get('autonomy_level', 'assistive'),
                    'capabilities': rec.get('capabilities', []),
                    'health_hook': rec.get('health_hook', '/health'),
                }
                for op_id, rec in self.registry.items()
            },
        }

    def start(self) -> None:
        self.runtime.start()


def main() -> None:
    p = argparse.ArgumentParser(description='CREW - Cascadia OS operator registry')
    p.add_argument('--config', required=True)
    p.add_argument('--name', required=True)
    a = p.parse_args()
    CrewService(a.config, a.name).start()


if __name__ == '__main__':
    main()
