"""vantage.py - Cascadia OS 2026.5 | VANTAGE: Capability enforcement gateway."""
# MATURITY: FUNCTIONAL — Validates declared capabilities, gates high-risk through SENTINEL, logs all calls.
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from cascadia.shared.config import load_config
from cascadia.shared.entitlements import get_risk_level
from cascadia.shared.service_runtime import ServiceRuntime
from cascadia.system.audit_log import AuditLog

VANTAGE_PORT = 6208
CREW_PORT = 5100
SENTINEL_PORT = 5102

_HIGH_RISK = {'high', 'critical'}
TIER_ORDER: Dict[str, int] = {"lite": 1, "pro": 2, "business": 3, "enterprise": 4}


def _http_post(url: str, data: Dict[str, Any], timeout: int = 5) -> Dict[str, Any]:
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=body, headers={'Content-Type': 'application/json'}, method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {'error': f'HTTP {e.code}', 'body': e.read().decode()}
    except urllib.error.URLError as e:
        return {'error': str(e.reason)}
    except Exception as e:
        return {'error': str(e)}


def _http_get(url: str, timeout: int = 5) -> Dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {'error': str(e)}


def check_capability(operator_id: str, capability: str) -> Dict[str, Any]:
    """Ask CREW to validate that operator has declared this capability."""
    return _http_post(
        f'http://localhost:{CREW_PORT}/validate',
        {'operator_id': operator_id, 'capability': capability},
    )


def call_sentinel(operator_id: str, capability: str, autonomy_level: str) -> Dict[str, Any]:
    """Map connector capability to nearest SENTINEL vocabulary entry and check."""
    risk = get_risk_level(capability)
    sentinel_action = {
        'critical': 'shell.exec',
        'high': 'file.delete',
        'medium': 'file.write',
        'low': 'file.read',
    }.get(risk, 'file.read')
    return _http_post(
        f'http://localhost:{SENTINEL_PORT}/check',
        {'action': sentinel_action, 'operator_id': operator_id, 'autonomy_level': autonomy_level},
    )


def fetch_crew_registry() -> Dict[str, Any]:
    """Fetch current operator registry from CREW."""
    return _http_get(f'http://localhost:{CREW_PORT}/crew')


def _active_tier_level(config: Dict[str, Any]) -> tuple[int, str]:
    """Return (numeric_level, tier_name) for the active license. Defaults to lite."""
    tier = config.get('license', {}).get('tier', 'lite')
    return TIER_ORDER.get(tier, 1), tier


def _fetch_operator_tier_required(operator_id: str) -> str:
    """Return tier_required from CREW registry for this operator. Defaults to lite."""
    registry = fetch_crew_registry()
    op_data = registry.get('operators', {}).get(operator_id, {})
    return op_data.get('tier_required', 'lite')


class VantageService:
    """VANTAGE — Capability enforcement gateway. Sits between operators and connectors."""

    def __init__(self, config_path: str, name: str) -> None:
        config = load_config(config_path)
        self._config = config
        comp = next(c for c in config['components'] if c['name'] == name)
        self.runtime = ServiceRuntime(
            name=name, port=comp['port'],
            pulse_file=comp['pulse_file'], log_dir=config['log_dir'],
        )
        self._audit = AuditLog()
        self._call_count = 0
        self._blocked_count = 0
        self.runtime.register_route('POST', '/call', self.handle_call)
        self.runtime.register_route('GET', '/api/health', self.handle_health)
        self.runtime.register_route('GET', '/api/registry', self.handle_registry)
        self.runtime.register_route('GET', '/api/capabilities/{operator_id}', self.handle_capabilities)
        self.runtime.register_route('POST', '/api/simulate', self.handle_simulate)

    def _log_vantage_call(self, operator_id: str, capability: str, risk_level: str,
                          decision: str, run_id: Optional[str] = None,
                          connector_id: str = '') -> None:
        self._audit.log_capability_call(
            operator_id=operator_id,
            connector_id=connector_id,
            capability=capability,
            risk_level=risk_level,
            verdict=decision,
            run_id=run_id or '',
            simulated=False,
        )

    def handle_call(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        operator_id = payload.get('operator_id', '')
        capability = payload.get('capability', '')
        connector_port = payload.get('connector_port')
        connector_path = payload.get('connector_path', '/api/call')
        connector_payload = payload.get('payload', {})
        autonomy_level = payload.get('autonomy_level', 'manual_only')
        run_id = payload.get('run_id')

        if not operator_id or not capability:
            return 400, {'error': 'operator_id and capability are required'}
        if not connector_port:
            return 400, {'error': 'connector_port is required'}

        # Step 1: validate declared capability in CREW
        crew_result = check_capability(operator_id, capability)
        if crew_result.get('error'):
            self.runtime.logger.warning(
                'CREW unreachable for %s/%s: %s', operator_id, capability, crew_result['error']
            )
            return 503, {'error': 'CREW registry unavailable', 'operator_id': operator_id, 'capability': capability}
        if not crew_result.get('ok', False):
            self._blocked_count += 1
            self._log_vantage_call(operator_id, capability, 'low', 'blocked_undeclared', run_id)
            return 403, {
                'verdict': 'blocked',
                'reason': 'capability not declared in operator manifest',
                'operator_id': operator_id,
                'capability': capability,
            }

        # Step 2: resolve risk level
        risk_level = get_risk_level(capability)

        # Step 2b: tier enforcement
        tier_required = _fetch_operator_tier_required(operator_id)
        active_level, active_tier = _active_tier_level(self._config)
        required_level = TIER_ORDER.get(tier_required, 1)
        if active_level < required_level:
            self._blocked_count += 1
            self._log_vantage_call(operator_id, capability, risk_level, 'blocked_tier_insufficient', run_id)
            return 403, {
                'error': 'tier_insufficient',
                'message': (
                    f'This operator requires the {tier_required} tier. '
                    f'Current license: {active_tier}. Upgrade at zyrcon.store.'
                ),
                'required_tier': tier_required,
                'current_tier': active_tier,
            }

        # Step 3: gate high/critical through SENTINEL
        if risk_level in _HIGH_RISK:
            sentinel_result = call_sentinel(operator_id, capability, autonomy_level)
            if sentinel_result.get('error'):
                self._blocked_count += 1
                self._log_vantage_call(operator_id, capability, risk_level, 'blocked_sentinel_unavailable', run_id)
                return 503, {
                    'error': 'SENTINEL unavailable for high-risk capability',
                    'capability': capability,
                    'risk_level': risk_level,
                }
            verdict = sentinel_result.get('verdict', 'blocked')
            if verdict != 'allowed':
                self._blocked_count += 1
                self._log_vantage_call(operator_id, capability, risk_level, f'blocked_{verdict}', run_id)
                return 403, {
                    'verdict': verdict,
                    'reason': sentinel_result.get('reason', 'blocked by SENTINEL'),
                    'operator_id': operator_id,
                    'capability': capability,
                    'risk_level': risk_level,
                }

        # Step 4: forward to connector
        connector_url = f'http://localhost:{connector_port}{connector_path}'
        start = time.time()
        connector_result = _http_post(connector_url, connector_payload)
        latency_ms = round((time.time() - start) * 1000, 1)

        self._call_count += 1
        decision = 'allowed' if 'error' not in connector_result else 'connector_error'
        self._log_vantage_call(operator_id, capability, risk_level, decision, run_id,
                               connector_id=str(connector_port))

        self.runtime.logger.info(
            'VANTAGE %s %s risk=%s -> %s (%sms)',
            operator_id, capability, risk_level, decision, latency_ms,
        )
        return 200, {
            'verdict': 'allowed',
            'operator_id': operator_id,
            'capability': capability,
            'risk_level': risk_level,
            'connector_port': connector_port,
            'latency_ms': latency_ms,
            'connector_response': connector_result,
        }

    def handle_health(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        return 200, {
            'service': 'vantage',
            'status': self.runtime.state,
            'port': VANTAGE_PORT,
            'calls_total': self._call_count,
            'calls_blocked': self._blocked_count,
        }

    def handle_registry(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        crew_data = fetch_crew_registry()
        operators = crew_data.get('operators', {})
        registry: Dict[str, Any] = {
            op_id: [
                {'capability': c, 'risk_level': get_risk_level(c)}
                for c in op_data.get('capabilities', [])
            ]
            for op_id, op_data in operators.items()
        }
        return 200, {'service': 'vantage', 'operator_count': len(registry), 'registry': registry}

    def handle_capabilities(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        operator_id = payload.get('operator_id', '')
        crew_data = fetch_crew_registry()
        operators = crew_data.get('operators', {})
        if operator_id not in operators:
            return 404, {'error': f'operator {operator_id!r} not found in CREW registry'}
        caps = operators[operator_id].get('capabilities', [])
        return 200, {
            'operator_id': operator_id,
            'capabilities': [
                {'capability': c, 'risk_level': get_risk_level(c)} for c in caps
            ],
        }

    def handle_simulate(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Dry-run: classify and validate without forwarding to connector."""
        operator_id = payload.get('operator_id', '')
        capability = payload.get('capability', '')
        autonomy_level = payload.get('autonomy_level', 'manual_only')

        if not operator_id or not capability:
            return 400, {'error': 'operator_id and capability are required'}

        risk_level = get_risk_level(capability)
        crew_result = check_capability(operator_id, capability)
        crew_valid: Optional[bool]
        if crew_result.get('error'):
            crew_valid = None
        else:
            crew_valid = crew_result.get('ok', False)

        sentinel_verdict: Optional[str] = None
        if risk_level in _HIGH_RISK and crew_valid:
            s_result = call_sentinel(operator_id, capability, autonomy_level)
            sentinel_verdict = (
                s_result.get('verdict', 'unknown') if not s_result.get('error')
                else 'sentinel_unavailable'
            )

        if crew_valid is None:
            predicted_verdict = 'blocked_crew_unavailable'
        elif not crew_valid:
            predicted_verdict = 'blocked_undeclared'
        elif risk_level in _HIGH_RISK and sentinel_verdict and sentinel_verdict != 'allowed':
            predicted_verdict = f'blocked_{sentinel_verdict}'
        else:
            predicted_verdict = 'allowed'

        return 200, {
            'simulation': True,
            'operator_id': operator_id,
            'capability': capability,
            'risk_level': risk_level,
            'crew_valid': crew_valid,
            'sentinel_verdict': sentinel_verdict,
            'predicted_verdict': predicted_verdict,
        }

    def start(self) -> None:
        self.runtime.start()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True)
    p.add_argument('--name', required=True)
    a = p.parse_args()
    VantageService(a.config, a.name).start()


if __name__ == '__main__':
    main()
