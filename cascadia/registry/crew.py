"""
crew.py - Cascadia OS v0.44
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
import json
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Dict

from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime
from cascadia.core.watchdog import OperatorWatchdog

_REQUIRED_MANIFEST_FIELDS = {'operator_id', 'name', 'version', 'capabilities'}
_OPERATORS_DIR = Path(__file__).parent.parent.parent / 'operators'


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
        self._config = config
        self.registry: Dict[str, Dict[str, Any]] = {}
        self._watchdog = OperatorWatchdog(config, self.runtime.logger)
        self.runtime.register_route('POST', '/register',              self.register)
        self.runtime.register_route('POST', '/validate',              self.validate)
        self.runtime.register_route('GET',  '/crew',                  self.list_crew)
        self.runtime.register_route('POST', '/deregister',            self.deregister)
        self.runtime.register_route('POST', '/install_operator',      self.install_operator)
        self.runtime.register_route('GET',  '/api/watchdog/status',   self.watchdog_status)

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

    def install_operator(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Install an operator from a base64-encoded zip bundle.
        Validates the manifest, extracts to operators/, and registers in the Crew.
        Does not own execution scheduling or capability policy.
        """
        import base64
        zip_b64 = payload.get('zip_b64', '')
        if not zip_b64:
            return 400, {'error': 'zip_b64 required'}

        try:
            raw = base64.b64decode(zip_b64)
        except Exception:
            return 400, {'error': 'invalid base64 encoding'}

        manifest, error = self._extract_and_validate_manifest(raw)
        if error:
            return 400, {'error': error}

        op_id = manifest['operator_id']
        dest = _OPERATORS_DIR / op_id
        try:
            with zipfile.ZipFile(BytesIO(raw)) as zf:
                zf.extractall(dest)
        except Exception as exc:
            return 500, {'error': f'extraction failed: {exc}'}

        self.registry[op_id] = {
            'operator_id': op_id,
            'type': manifest.get('type', 'community'),
            'autonomy_level': manifest.get('autonomy_level', 'assistive'),
            'capabilities': manifest.get('capabilities', []),
            'health_hook': manifest.get('health_hook', '/health'),
            'version': manifest.get('version'),
            'source': 'installed',
        }
        self.runtime.logger.info('CREW installed operator: %s v%s', op_id, manifest.get('version'))
        return 201, {'installed': op_id, 'manifest': manifest}

    @staticmethod
    def _extract_and_validate_manifest(raw: bytes) -> tuple[Dict[str, Any], str]:
        """
        Returns (manifest_dict, error_string).
        error_string is '' on success.
        """
        try:
            with zipfile.ZipFile(BytesIO(raw)) as zf:
                names = zf.namelist()
                manifest_name = next((n for n in names if n.endswith('manifest.json')), None)
                if manifest_name is None:
                    return {}, 'manifest.json not found in zip'
                manifest = json.loads(zf.read(manifest_name))
        except zipfile.BadZipFile:
            return {}, 'not a valid zip file'
        except json.JSONDecodeError:
            return {}, 'manifest.json is not valid JSON'
        except Exception as exc:
            return {}, str(exc)

        missing = _REQUIRED_MANIFEST_FIELDS - set(manifest.keys())
        if missing:
            return {}, f'manifest missing required fields: {sorted(missing)}'
        if not isinstance(manifest['capabilities'], list):
            return {}, 'capabilities must be a list'
        return manifest, ''

    def watchdog_status(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """GET /api/watchdog/status — operator health and restart counts."""
        return 200, self._watchdog.get_status()

    def start(self) -> None:
        self._watchdog.start()
        self.runtime.start()


def main() -> None:
    p = argparse.ArgumentParser(description='CREW - Cascadia OS operator registry')
    p.add_argument('--config', required=True)
    p.add_argument('--name', required=True)
    a = p.parse_args()
    CrewService(a.config, a.name).start()


if __name__ == '__main__':
    main()
