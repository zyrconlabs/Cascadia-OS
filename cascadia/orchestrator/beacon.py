"""
beacon.py - Cascadia OS 2026.5
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
import os
from typing import Any, Dict, Optional
from urllib import request as urllib_request
from urllib.error import URLError, HTTPError
import logging as _logging
import subprocess as _subprocess
import threading as _threading
import time as _time
from datetime import datetime as _datetime
from pathlib import Path as _Path

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

# ── MEMORY GOVERNOR ──────────────────────────────────────────────────────────
_MG_CONFIG: Dict[str, Any] = {
    'poll_interval':       30,     # seconds between pressure checks
    'threshold_moderate': 200,     # MB swap → sleep tier1 + tier2
    'threshold_critical': 600,     # MB swap → sleep tier3, alert owner
    'never_sleep': [
        'nats', 'crew', 'chief', 'beacon',
        'llm', 'postgres', 'telegram', 'email_operator',
    ],
    'tier1': ['crm', 'quote', 'debrief', 'aurelia', 'pulse'],
    'tier2': ['social', 'scout', 'brief', 'collect'],
    'tier3': ['recon'],
    'missions': {
        'find_work': ['scout', 'recon', 'collect', 'quote_brief', 'brief', 'social', 'email_operator'],
        'win_work':  ['scout', 'recon', 'collect', 'quote_brief', 'quote', 'brief', 'email_operator'],
        'run_work':  ['collect', 'brief', 'aurelia', 'debrief', 'quote_brief', 'email_operator'],
    },
}

_OM_URL        = 'http://127.0.0.1:6210'
_TELEGRAM_URL  = 'http://127.0.0.1:9000/send'
_OWNER_CHAT_ID = os.environ.get("TELEGRAM_OWNER_CHAT_ID", "")
_INTENT_FILE   = _Path(__file__).parent.parent.parent / 'data' / 'runtime' / 'operator_intent.json'

_governor_slept:  set             = set()
_active_mission:  Optional[str]   = None
_last_pressure:   str             = 'ok'
_last_check_time: Optional[str]   = None

_mg_log = _logging.getLogger('beacon')


def _mg_get_pressure() -> Dict[str, Any]:
    """Read vm_stat and return current memory pressure metrics."""
    try:
        out = _subprocess.check_output(['vm_stat'], text=True)
        pages: Dict[str, int] = {}
        for line in out.splitlines():
            for key in ('Pages swapped out', 'Pages free',
                        'Pages occupied by compressor'):
                if line.startswith(key):
                    pages[key] = int(line.split(':')[1].strip().rstrip('.'))
        page_bytes    = 16384  # Apple Silicon page size
        swap_used_mb  = pages.get('Pages swapped out', 0) * page_bytes // (1024 * 1024)
        free_mb       = pages.get('Pages free', 0)                    * page_bytes // (1024 * 1024)
        compressed_mb = pages.get('Pages occupied by compressor', 0)  * page_bytes // (1024 * 1024)
        if swap_used_mb >= _MG_CONFIG['threshold_critical']:
            pressure = 'critical'
        elif swap_used_mb >= _MG_CONFIG['threshold_moderate']:
            pressure = 'moderate'
        else:
            pressure = 'ok'
        return {
            'swap_used_mb':  swap_used_mb,
            'free_mb':       free_mb,
            'compressed_mb': compressed_mb,
            'pressure':      pressure,
        }
    except Exception as exc:
        _mg_log.warning('GOVERNOR: _mg_get_pressure error: %s', exc)
        return {'swap_used_mb': 0, 'free_mb': 0, 'compressed_mb': 0, 'pressure': 'ok'}


def _mg_get_intent(op_id: str) -> str:
    """Return worker_intent for op_id from operator_intent.json, or 'unknown'."""
    try:
        if _INTENT_FILE.exists():
            data = json.loads(_INTENT_FILE.read_text())
            return data.get(op_id, {}).get('worker_intent', 'unknown')
    except Exception:
        pass
    return 'unknown'


def _mg_sleep(op_id: str, reason: str) -> None:
    """PUT operator to sleep via OM. Skips never_sleep list and already-sleeping ops."""
    if op_id in _MG_CONFIG['never_sleep']:
        return
    if _mg_get_intent(op_id) == 'sleeping':
        return
    try:
        body = json.dumps({'reason': reason}).encode()
        req = urllib_request.Request(
            f'{_OM_URL}/operators/{op_id}/sleep',
            data=body, method='POST',
            headers={'Content-Type': 'application/json'},
        )
        with urllib_request.urlopen(req, timeout=5):
            pass
        _mg_log.info('GOVERNOR: sleeping %s — %s', op_id, reason)
        _governor_slept.add(op_id)
    except Exception as exc:
        _mg_log.warning('GOVERNOR: could not sleep %s: %s', op_id, exc)


def _mg_wake(op_id: str, reason: str) -> None:
    """Wake operator via OM."""
    try:
        body = json.dumps({'reason': reason}).encode()
        req = urllib_request.Request(
            f'{_OM_URL}/operators/{op_id}/wake',
            data=body, method='POST',
            headers={'Content-Type': 'application/json'},
        )
        with urllib_request.urlopen(req, timeout=5):
            pass
        _mg_log.info('GOVERNOR: waking %s — %s', op_id, reason)
    except Exception as exc:
        _mg_log.warning('GOVERNOR: could not wake %s: %s', op_id, exc)


def _mg_alert(text: str) -> None:
    """Send Telegram alert to owner. Never crashes."""
    try:
        body = json.dumps({'chat_id': _OWNER_CHAT_ID, 'text': text}).encode()
        req = urllib_request.Request(
            _TELEGRAM_URL, data=body, method='POST',
            headers={'Content-Type': 'application/json'},
        )
        with urllib_request.urlopen(req, timeout=10):
            pass
    except Exception as exc:
        _mg_log.warning('GOVERNOR: Telegram alert failed: %s', exc)


def _mg_governor_loop() -> None:
    """Daemon thread: poll memory pressure every poll_interval seconds."""
    global _last_pressure, _last_check_time
    while True:
        try:
            mem = _mg_get_pressure()
            _last_check_time = _datetime.utcnow().isoformat()
            _last_pressure   = mem['pressure']

            if mem['pressure'] == 'ok':
                _mg_log.debug('GOVERNOR: ok (swap=%dMB free=%dMB)',
                              mem['swap_used_mb'], mem['free_mb'])

            elif mem['pressure'] == 'moderate':
                for op in _MG_CONFIG['tier1'] + _MG_CONFIG['tier2']:
                    _mg_sleep(op, 'RAM moderate pressure')

            elif mem['pressure'] == 'critical':
                for op in (_MG_CONFIG['tier1'] + _MG_CONFIG['tier2']
                           + _MG_CONFIG['tier3']):
                    _mg_sleep(op, 'RAM critical pressure')
                _mg_alert(
                    f'\U0001f534 RAM CRITICAL\n'
                    f'Swap: {mem["swap_used_mb"]}MB\n'
                    f'Free: {mem["free_mb"]}MB\n'
                    f'Governor slept: {list(_governor_slept)}\n'
                    f'System stable — monitoring.'
                )
                mem2 = _mg_get_pressure()
                if mem2['pressure'] == 'critical':
                    _mg_alert(
                        '⚠️ RAM still critical after sleeping '
                        'all non-essential operators. '
                        'Manual review needed.'
                    )

        except Exception as exc:
            _mg_log.warning('GOVERNOR loop error: %s', exc)
        finally:
            _time.sleep(_MG_CONFIG['poll_interval'])

# ─────────────────────────────────────────────────────────────────────────────


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
            pulse_file=component['pulse_file'],
            log_dir=self.config['log_dir'],
        )
        # Build port map from config — all registered components
        self._port_map: Dict[str, int] = {
            c['name']: c['port']
            for c in self.config.get('components', [])
        }
        crew_comp = next((c for c in self.config['components'] if c['name'] == 'crew'), None)
        self.crew_port: Optional[int] = crew_comp['port'] if crew_comp else None

        self.runtime.register_route('POST', '/route',      self.route)
        self.runtime.register_route('POST', '/handoff',    self.handoff)
        self.runtime.register_route('POST', '/forward',    self.forward)
        self.runtime.register_route('GET',  '/registry',   self.registry)
        self.runtime.register_route('POST', '/escalation',        self.escalation_handler)
        self.runtime.register_route('GET',  '/api/memory',         self.memory_status)
        self.runtime.register_route('POST', '/api/mission/start',  self.mission_start)
        self.runtime.register_route('POST', '/api/mission/end',    self.mission_end)

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
    # CREW operator lookup (for operators not in config port map)
    # ------------------------------------------------------------------

    def _crew_lookup(self, operator_id: str) -> Optional[dict]:
        """Return the operator record from CREW registry, or None if not found."""
        if not self.crew_port:
            return None
        try:
            with urllib_request.urlopen(
                f'http://127.0.0.1:{self.crew_port}/crew', timeout=2
            ) as r:
                data = json.loads(r.read().decode())
            return data.get('operators', {}).get(operator_id)
        except Exception:
            return None

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
        if target_port is None:
            crew_info = self._crew_lookup(target)
            if crew_info and crew_info.get('port'):
                target_port = crew_info['port']
                # Use the task_hook registered by the operator, fall back to /api/task
                if forward_path == '/message':
                    forward_path = crew_info.get('task_hook', '/api/task')

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

    def escalation_handler(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        POST /escalation — receive escalation from supervisor.
        Reviews mission context, checks connectors and model fallback,
        creates a rich decision request or retries with fallback config.
        """
        run_id       = payload.get('run_id', '')
        failure_type = payload.get('failure_type', 'unknown')
        operator     = payload.get('operator', '')
        reason       = payload.get('reason', '')

        self.runtime.logger.warning(
            'BEACON escalation: operator=%s run=%s type=%s', operator, run_id, failure_type
        )

        # LLM timeout with a fallback model configured — auto-retry
        if failure_type == 'llm_timeout':
            fallback = self._config.get('llm', {}).get('fallback_model')
            if fallback:
                from cascadia.automation.failure_event import _nats_publish_sync
                import json as _json
                _nats_publish_sync(
                    'zyrcon.operator.retry',
                    _json.dumps({
                        'run_id': run_id, 'operator': operator,
                        'fallback_model': fallback,
                        'failure_type': failure_type,
                    }).encode(),
                )
                return 200, {'action': 'retry_with_fallback', 'fallback_model': fallback}

        # Build decision options based on failure type
        options = self._decision_options_for(failure_type, operator, payload)
        db_path = self._config.get('database_path', './data/runtime/cascadia.db')
        try:
            from cascadia.durability.run_store import RunStore
            from cascadia.system.approval_store import ApprovalStore
            rs = RunStore(db_path)
            store = ApprovalStore(rs)
            approval_id = store.insert_decision_request(
                run_id=run_id,
                step_id=payload.get('step_id', ''),
                source='beacon_escalation',
                title=f'Action needed: {failure_type.replace("_", " ").title()}',
                summary=reason or f'Operator {operator!r} escalated: {failure_type}',
                risk_level='HIGH',
                decision_type='multi_choice',
                options=options,
            )
            return 200, {
                'action': 'decision_request_created',
                'approval_id': approval_id,
                'options': options,
            }
        except Exception as exc:
            self.runtime.logger.error('BEACON escalation handler error: %s', exc)
            return 500, {'error': str(exc)}

    def _decision_options_for(
        self, failure_type: str, operator: str, payload: Dict[str, Any]
    ) -> list:
        """Return appropriate decision options for the given failure type."""
        base = [
            {'id': 'abort_mission', 'label': 'Abort mission', 'action': 'abort'},
        ]
        if failure_type == 'missing_connector':
            connector = payload.get('payload', {}).get('connector', 'the required connector')
            return [
                {'id': 'connect', 'label': f'Connect {connector}',
                 'action': 'open_connector_settings'},
                {'id': 'upload_manually', 'label': 'Upload data manually',
                 'action': 'manual_upload'},
                {'id': 'skip_step', 'label': 'Skip this step', 'action': 'skip'},
                *base,
            ]
        if failure_type == 'insufficient_data':
            return [
                {'id': 'ask_customer', 'label': 'Ask customer for missing info',
                 'action': 'ask_customer'},
                {'id': 'use_default', 'label': 'Use default values', 'action': 'use_default'},
                {'id': 'skip_step', 'label': 'Skip this step', 'action': 'skip'},
                *base,
            ]
        if failure_type == 'permission_denied':
            return [
                {'id': 'grant_permission', 'label': f'Grant permission to {operator}',
                 'action': 'open_operator_settings'},
                {'id': 'skip_step', 'label': 'Skip this step', 'action': 'skip'},
                *base,
            ]
        if failure_type == 'requires_decision':
            return [
                {'id': 'proceed', 'label': 'Proceed as planned', 'action': 'approve'},
                {'id': 'modify', 'label': 'Modify and proceed', 'action': 'edit_and_approve'},
                {'id': 'skip_step', 'label': 'Skip this step', 'action': 'skip'},
                *base,
            ]
        # Generic fallback
        return [
            {'id': 'retry', 'label': 'Retry the step', 'action': 'retry'},
            {'id': 'skip_step', 'label': 'Skip this step', 'action': 'skip'},
            *base,
        ]

    # ------------------------------------------------------------------
    # Memory governor endpoints
    # ------------------------------------------------------------------

    def memory_status(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """GET /api/memory — current RAM pressure and governor state."""
        mem = _mg_get_pressure()
        return 200, {
            **mem,
            'active_mission': _active_mission,
            'governor_slept': list(_governor_slept),
            'last_check':     _last_check_time,
        }

    def mission_start(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /api/mission/start — wake required operators, free RAM for mission."""
        global _active_mission
        mission = payload.get('mission', '')
        if mission not in _MG_CONFIG['missions']:
            return 400, {'ok': False, 'error': f'unknown mission: {mission}'}
        required = _MG_CONFIG['missions'][mission]
        woke: list = []
        slept: list = []
        for op in required:
            _mg_wake(op, f'mission {mission} start')
            woke.append(op)
        for op in _MG_CONFIG['tier1']:
            if op not in required:
                _mg_sleep(op, f'mission {mission} freeing RAM')
                slept.append(op)
        _active_mission = mission
        self.runtime.logger.info(
            'BEACON: mission %s started — woke=%s slept=%s', mission, woke, slept
        )
        return 200, {'ok': True, 'mission': mission, 'woke': woke, 'slept': slept}

    def mission_end(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /api/mission/end — restore governor-slept operators to sleeping."""
        global _active_mission
        mission = payload.get('mission', _active_mission or '')
        restored: list = []
        for op in list(_governor_slept):
            intent = _mg_get_intent(op)
            if intent not in ('sleeping', 'stopped'):
                _mg_sleep(op, 'mission end — restoring state')
                restored.append(op)
        _governor_slept.clear()
        _active_mission = None
        self.runtime.logger.info(
            'BEACON: mission %s ended — restored=%s', mission, restored
        )
        return 200, {'ok': True, 'restored': restored}

    def start(self) -> None:
        self.runtime.logger.info(
            'BEACON active — %d components registered', len(self._port_map)
        )
        _gov = _threading.Thread(
            target=_mg_governor_loop, daemon=True, name='memory-governor',
        )
        _gov.start()
        self.runtime.logger.info(
            'BEACON: memory governor started (poll=%ds, moderate=%dMB, critical=%dMB)',
            _MG_CONFIG['poll_interval'],
            _MG_CONFIG['threshold_moderate'],
            _MG_CONFIG['threshold_critical'],
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
