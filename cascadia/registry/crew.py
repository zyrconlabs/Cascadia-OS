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
import shutil
import threading
import time as _time
import urllib.request as _urllib_request
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List

from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime
from cascadia.core.watchdog import OperatorWatchdog

_REQUIRED_MANIFEST_FIELDS = {'operator_id', 'name', 'version', 'capabilities'}
_REQUIRED_DEPOT_MANIFEST_FIELDS = {'id', 'name', 'version', 'port', 'start_cmd',
                                    'autonomy_level', 'capabilities', 'tier_required'}
_OPERATORS_DIR = Path(__file__).parent.parent.parent / 'operators'
_FLINT_PORT = 4011


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _registry_path(config: dict) -> Path:
    """Resolve operators_registry_path from config, with fallback."""
    configured = config.get('operators_registry_path', '')
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).parent.parent / 'operators' / 'registry.json'


def _load_registry(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {'version': '0.44', 'operators': []}


def _save_registry(path: Path, reg: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reg, indent=2))


def _install_log_path(config: dict) -> Path:
    db_path = config.get('database_path', './data/runtime/cascadia.db')
    return Path(db_path).parent / 'install_log.json'


def _append_install_log(config: dict, entry: dict) -> None:
    log_path = _install_log_path(config)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        entries = json.loads(log_path.read_text()) if log_path.exists() else []
    except Exception:
        entries = []
    entries.append(entry)
    entries = entries[-500:]  # cap
    log_path.write_text(json.dumps(entries, indent=2))


def _try_flint(action: str, payload: dict) -> dict:
    """Best-effort call to FLINT process manager. Does not raise on failure."""
    try:
        body = json.dumps(payload).encode()
        req = _urllib_request.Request(
            f'http://127.0.0.1:{_FLINT_PORT}/api/process/{action}',
            data=body, method='POST',
            headers={'Content-Type': 'application/json'},
        )
        with _urllib_request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {'ok': False, 'reason': 'flint_unavailable'}


def _poll_health(port: int, path: str = '/api/health', timeout_s: int = 10) -> bool:
    """Poll operator health endpoint until it responds or timeout."""
    deadline = _time.monotonic() + timeout_s
    while _time.monotonic() < deadline:
        try:
            with _urllib_request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=2) as r:
                return r.status == 200
        except Exception:
            _time.sleep(1)
    return False


def _check_tier(config: dict, tier_required: str) -> tuple[bool, str]:
    """Check license tier via LICENSE_GATE. Returns (ok, reason)."""
    try:
        body = json.dumps({'tier_required': tier_required}).encode()
        req = _urllib_request.Request(
            'http://127.0.0.1:6100/api/license/check_tier',
            data=body, method='POST',
            headers={'Content-Type': 'application/json'},
        )
        with _urllib_request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read().decode())
            return data.get('ok', True), data.get('reason', '')
    except Exception:
        return True, ''  # fail-open if license_gate unavailable


def _start_removed_cleanup_daemon(operators_dir: Path) -> None:
    """Background thread: purge .removed/<op_id>/* folders older than 30 days."""
    def _loop() -> None:
        while True:
            _time.sleep(86400)  # run daily
            removed_dir = operators_dir / '.removed'
            if not removed_dir.exists():
                continue
            cutoff = _time.time() - (30 * 86400)
            for op_dir in removed_dir.iterdir():
                if op_dir.is_dir():
                    try:
                        mtime = op_dir.stat().st_mtime
                        if mtime < cutoff:
                            shutil.rmtree(op_dir, ignore_errors=True)
                    except Exception:
                        pass

    t = threading.Thread(target=_loop, daemon=True, name='depot-cleanup')
    t.start()


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
        self.runtime.register_route('POST', '/remove_operator',       self.remove_operator)
        self.runtime.register_route('POST', '/restore_operator',      self.restore_operator)
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
        Install an operator from a base64-encoded zip bundle or package_url.
        Validates the manifest, extracts to operators/, checks tier + port conflicts,
        optionally starts via FLINT, polls health, and logs to data/install_log.json.
        """
        import base64, urllib.request as _ur, urllib.error as _ue

        operator_id = payload.get('operator_id', '')
        package_url = payload.get('package_url', '')
        manifest_in = payload.get('manifest', {})
        source      = payload.get('source', 'installed')
        dry_run     = payload.get('dry_run', False)

        # Determine zip bytes
        zip_b64 = payload.get('zip_b64', '')
        raw: bytes = b''

        if zip_b64:
            try:
                raw = base64.b64decode(zip_b64)
            except Exception:
                return 400, {'error': 'invalid base64 encoding'}

        elif package_url:
            try:
                with _ur.urlopen(package_url, timeout=30) as r:
                    raw = r.read()
            except Exception as exc:
                return 502, {'error': f'package download failed: {exc}'}

        if raw:
            manifest, error = self._extract_and_validate_manifest(raw)
            if error:
                return 400, {'error': error}
        elif manifest_in:
            # Manifest-only install (register without zip — local operator)
            manifest = manifest_in
            # Validate required DEPOT fields
            missing = _REQUIRED_DEPOT_MANIFEST_FIELDS - set(manifest.keys())
            if missing:
                return 400, {'error': f'manifest missing fields: {sorted(missing)}'}
        else:
            return 400, {'error': 'zip_b64, package_url, or manifest required'}

        op_id = manifest.get('operator_id') or manifest.get('id') or operator_id
        if not op_id:
            return 400, {'error': 'could not determine operator_id from manifest'}

        # Normalize manifest key
        manifest.setdefault('operator_id', op_id)

        # Tier check
        tier_required = manifest.get('tier_required', 'lite')
        tier_ok, tier_reason = _check_tier(getattr(self, '_config', {}), tier_required)
        if not tier_ok:
            return 403, {
                'error': 'tier_required',
                'tier_required': tier_required,
                'reason': tier_reason,
                'upgrade_url': 'https://zyrcon.store',
            }

        # Port conflict check
        port = manifest.get('port')
        _cfg = getattr(self, '_config', {})
        if port:
            reg_path = _registry_path(_cfg)
            reg = _load_registry(reg_path)
            for existing_op in reg.get('operators', []):
                if existing_op.get('port') == port and existing_op.get('id') != op_id:
                    return 409, {
                        'error': 'port_conflict',
                        'port': port,
                        'conflict_with': existing_op.get('id'),
                    }

        if dry_run:
            return 200, {'ok': True, 'dry_run': True, 'operator_id': op_id, 'manifest': manifest}

        # Extract zip to operators dir (if we have bytes)
        dest = _OPERATORS_DIR / op_id
        if raw:
            try:
                with zipfile.ZipFile(BytesIO(raw)) as zf:
                    zf.extractall(dest)
            except Exception as exc:
                return 500, {'error': f'extraction failed: {exc}'}

        # Register in operators registry.json
        reg_path = _registry_path(_cfg)
        reg = _load_registry(reg_path)
        existing_ids = [op.get('id') for op in reg.get('operators', [])]
        new_entry = {
            'id': op_id,
            'name': manifest.get('name', op_id),
            'version': manifest.get('version', ''),
            'port': port,
            'start_cmd': manifest.get('start_cmd', ''),
            'autonomy_level': manifest.get('autonomy_level', 'assistive'),
            'capabilities': manifest.get('capabilities', []),
            'tier_required': tier_required,
            'health_path': manifest.get('health_path', '/api/health'),
            'category': manifest.get('category', 'custom'),
            'description': manifest.get('description', ''),
            'status': 'installed',
            'source': source,
            'installed_at': _now_iso(),
        }
        if op_id in existing_ids:
            reg['operators'] = [
                new_entry if op.get('id') == op_id else op
                for op in reg['operators']
            ]
        else:
            reg['operators'].append(new_entry)
        _save_registry(reg_path, reg)

        # In-memory crew registration
        self.registry[op_id] = {
            'operator_id': op_id,
            'type': manifest.get('type', 'community'),
            'autonomy_level': manifest.get('autonomy_level', 'assistive'),
            'capabilities': manifest.get('capabilities', []),
            'health_hook': manifest.get('health_path', '/health'),
            'version': manifest.get('version'),
            'source': source,
        }

        # Attempt FLINT start (best-effort)
        start_cmd = manifest.get('start_cmd', '')
        flint_result = {}
        if start_cmd:
            flint_result = _try_flint('start', {
                'name': op_id,
                'cmd': start_cmd,
                'port': port,
            })

        # Poll health for up to 10s
        health_ok = False
        if port:
            health_path = manifest.get('health_path', '/api/health')
            health_ok = _poll_health(port, health_path, timeout_s=10)

        log_entry = {
            'action': 'install',
            'operator_id': op_id,
            'version': manifest.get('version', ''),
            'source': source,
            'health_ok': health_ok,
            'installed_at': _now_iso(),
        }
        _append_install_log(_cfg, log_entry)
        self.runtime.logger.info('CREW installed operator: %s v%s health=%s', op_id, manifest.get('version'), health_ok)
        return 201, {
            'installed': op_id,
            'manifest': manifest,
            'health_ok': health_ok,
            'flint': flint_result,
            'registry_updated': True,
        }

    def remove_operator(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Remove an operator: stop via FLINT, remove from registry, move files to .removed/.
        Finds affected STITCH workflows. Preserves data for 30-day recovery by default.
        """
        op_id = payload.get('operator_id', '')
        keep_data = payload.get('keep_data', True)
        dry_run = payload.get('dry_run', False)

        if not op_id:
            return 400, {'error': 'operator_id required'}

        # Find in registry
        reg_path = _registry_path(self._config)
        reg = _load_registry(reg_path)
        op_entry = next((op for op in reg.get('operators', []) if op.get('id') == op_id), None)
        if op_entry is None:
            return 404, {'error': f'operator not found: {op_id}'}

        # Find affected STITCH workflows
        affected_workflows: List[str] = []
        try:
            db_path = self._config.get('database_path', './data/runtime/cascadia.db')
            import sqlite3
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    'SELECT id, name, nodes FROM workflow_definitions WHERE deleted_at IS NULL'
                ).fetchall()
            for row in rows:
                nodes_raw = row['nodes'] or '[]'
                try:
                    nodes = json.loads(nodes_raw)
                except Exception:
                    nodes = []
                if isinstance(nodes, list):
                    for node in nodes:
                        if isinstance(node, dict):
                            if (node.get('operator') == op_id or
                                    node.get('data', {}).get('operator') == op_id):
                                affected_workflows.append(row['name'] or row['id'])
                                break
                elif isinstance(nodes, str) and op_id in nodes_raw:
                    affected_workflows.append(row['name'] or row['id'])
        except Exception:
            pass

        if dry_run:
            return 200, {
                'dry_run': True,
                'operator_id': op_id,
                'affected_workflows': affected_workflows,
                'keep_data': keep_data,
            }

        # Tell FLINT to stop (best-effort)
        _try_flint('stop', {'name': op_id})

        # Remove from registry
        reg['operators'] = [op for op in reg['operators'] if op.get('id') != op_id]
        _save_registry(reg_path, reg)

        # Remove from in-memory crew
        self.registry.pop(op_id, None)

        # Move operator files to .removed/<op_id>_<timestamp>
        src_dir = _OPERATORS_DIR / op_id
        data_kept_path = ''
        if src_dir.exists() and keep_data:
            removed_dir = _OPERATORS_DIR / '.removed'
            removed_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            dest = removed_dir / f'{op_id}_{stamp}'
            try:
                shutil.move(str(src_dir), str(dest))
                # Write removal manifest for restore
                meta = {**op_entry, 'removed_at': _now_iso(), 'removed_path': str(dest)}
                (dest / '_removal_meta.json').write_text(json.dumps(meta, indent=2))
                data_kept_path = str(dest)
            except Exception as exc:
                self.runtime.logger.warning('CREW: could not move operator dir: %s', exc)
        elif src_dir.exists() and not keep_data:
            shutil.rmtree(src_dir, ignore_errors=True)

        log_entry = {
            'action': 'remove',
            'operator_id': op_id,
            'keep_data': keep_data,
            'data_path': data_kept_path,
            'affected_workflows': affected_workflows,
            'removed_at': _now_iso(),
        }
        _append_install_log(self._config, log_entry)

        self.runtime.logger.info('CREW removed operator: %s (data_kept=%s)', op_id, bool(data_kept_path))
        return 200, {
            'removed': op_id,
            'data_kept': bool(data_kept_path),
            'data_kept_path': data_kept_path,
            'affected_workflows': affected_workflows,
            'registry_updated': True,
        }

    def restore_operator(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Restore a previously removed operator from .removed/<op_id>_*.
        Re-registers in the crew and attempts FLINT restart.
        """
        op_id = payload.get('operator_id', '')
        if not op_id:
            return 400, {'error': 'operator_id required'}

        removed_dir = _OPERATORS_DIR / '.removed'
        if not removed_dir.exists():
            return 404, {'error': 'no .removed directory found'}

        # Find the most recent matching removal folder
        candidates = sorted(
            [d for d in removed_dir.iterdir()
             if d.is_dir() and d.name.startswith(f'{op_id}_')],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return 404, {'error': f'no removed snapshot found for: {op_id}'}

        src = candidates[0]
        dest = _OPERATORS_DIR / op_id
        if dest.exists():
            return 409, {'error': f'operator directory already exists: {dest}'}

        try:
            shutil.move(str(src), str(dest))
        except Exception as exc:
            return 500, {'error': f'restore failed: {exc}'}

        # Read removal meta to recover manifest
        meta_file = dest / '_removal_meta.json'
        op_entry: dict = {}
        if meta_file.exists():
            try:
                op_entry = json.loads(meta_file.read_text())
            except Exception:
                pass

        # Re-register in registry.json
        reg_path = _registry_path(self._config)
        reg = _load_registry(reg_path)
        restore_entry = {
            'id': op_id,
            'name': op_entry.get('name', op_id),
            'version': op_entry.get('version', ''),
            'port': op_entry.get('port'),
            'start_cmd': op_entry.get('start_cmd', ''),
            'autonomy_level': op_entry.get('autonomy_level', 'assistive'),
            'capabilities': op_entry.get('capabilities', []),
            'tier_required': op_entry.get('tier_required', 'lite'),
            'health_path': op_entry.get('health_path', '/api/health'),
            'category': op_entry.get('category', 'custom'),
            'description': op_entry.get('description', ''),
            'status': 'restored',
            'restored_at': _now_iso(),
        }
        existing_ids = [op.get('id') for op in reg.get('operators', [])]
        if op_id in existing_ids:
            reg['operators'] = [
                restore_entry if op.get('id') == op_id else op
                for op in reg['operators']
            ]
        else:
            reg['operators'].append(restore_entry)
        _save_registry(reg_path, reg)

        # Re-register in memory
        self.registry[op_id] = {
            'operator_id': op_id,
            'autonomy_level': op_entry.get('autonomy_level', 'assistive'),
            'capabilities': op_entry.get('capabilities', []),
            'health_hook': op_entry.get('health_path', '/health'),
            'version': op_entry.get('version'),
            'source': 'restored',
        }

        # Attempt FLINT restart (best-effort)
        start_cmd = op_entry.get('start_cmd', '')
        flint_result = {}
        if start_cmd:
            flint_result = _try_flint('start', {
                'name': op_id,
                'cmd': start_cmd,
                'port': op_entry.get('port'),
            })

        log_entry = {
            'action': 'restore',
            'operator_id': op_id,
            'restored_from': str(src),
            'restored_at': _now_iso(),
        }
        _append_install_log(self._config, log_entry)

        self.runtime.logger.info('CREW restored operator: %s from %s', op_id, src)
        return 200, {
            'restored': op_id,
            'registry_updated': True,
            'flint': flint_result,
            'restored_from': str(src),
        }

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
        _start_removed_cleanup_daemon(_OPERATORS_DIR)
        self.runtime.logger.info('CREW: .removed cleanup daemon started (30-day purge)')
        self.runtime.start()


def main() -> None:
    p = argparse.ArgumentParser(description='CREW - Cascadia OS operator registry')
    p.add_argument('--config', required=True)
    p.add_argument('--name', required=True)
    a = p.parse_args()
    CrewService(a.config, a.name).start()


if __name__ == '__main__':
    main()
