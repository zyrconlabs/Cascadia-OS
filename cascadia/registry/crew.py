"""
crew.py - Cascadia OS 2026.5
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
from cascadia.depot.canonicalization import canonical_file_bytes, compute_package_digest
from cascadia.depot.signing import Verifier, verify_manifest
from cascadia.depot.kill_switch import KillSwitchProvider, NoopKillSwitchProvider
from cascadia.missions.manifest import MissionManifest

_MISSION_SIGNING_BUNDLE = Path(__file__).parent.parent / "depot" / "zyrcon_signing_keys.json"
_RESERVED_PATH_PATTERNS = (
    ".DS_Store", "__MACOSX/", "._", ".git/", ".svn/", ".hg/",
    "__pycache__/", ".pyc", "~", ".swp", ".swo", ".orig", ".#",
)

_STITCH_PORT = 6201
_HEALTH_POLL_INTERVAL = 30   # seconds between health checks
_HEALTH_EVICT_AFTER   = 2    # consecutive misses before eviction

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

    def __init__(self, config_path: str, name: str, *,
                 kill_switch_provider: KillSwitchProvider | None = None,
                 verifier: Verifier | None = None) -> None:
        config = load_config(config_path)
        component = next(c for c in config['components'] if c['name'] == name)
        self.runtime = ServiceRuntime(
            name=name, port=component['port'],
            pulse_file=component['pulse_file'],
            log_dir=config['log_dir'],
        )
        self._config = config
        self.registry: Dict[str, Dict[str, Any]] = {}
        self._health_failures: Dict[str, int] = {}
        self._watchdog = OperatorWatchdog(config, self.runtime.logger)
        self._kill_switch = kill_switch_provider or NoopKillSwitchProvider()
        self._verifier = verifier or Verifier()
        self.runtime.register_route('POST', '/register',              self.register)
        self.runtime.register_route('POST', '/validate',              self.validate)
        self.runtime.register_route('GET',  '/crew',                  self.list_crew)
        self.runtime.register_route('POST', '/deregister',            self.deregister)
        self.runtime.register_route('POST', '/install_operator',      self.install_operator)
        self.runtime.register_route('POST', '/remove_operator',       self.remove_operator)
        self.runtime.register_route('POST', '/restore_operator',      self.restore_operator)
        self.runtime.register_route('GET',  '/api/watchdog/status',   self.watchdog_status)
        self.runtime.register_route('POST', '/api/crew/install_mission',   self.install_mission)
        self.runtime.register_route('POST', '/api/crew/uninstall_mission', self.uninstall_mission)

    def register(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Register an operator into the Crew."""
        op_id = payload.get('operator_id')
        if not op_id:
            return 400, {'error': 'operator_id required'}
        self.registry[op_id] = payload
        self._health_failures.pop(op_id, None)  # clear stale failure count on re-registration
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
        def _op_record(op_id: str, rec: dict) -> dict:
            r: dict = {
                'operator_id': op_id,
                'type': rec.get('type', 'unknown'),
                'autonomy_level': rec.get('autonomy_level', 'assistive'),
                'capabilities': rec.get('capabilities', []),
                'health_hook': rec.get('health_hook', '/health'),
            }
            if rec.get('port'):
                r['port'] = rec['port']
            if rec.get('task_hook'):
                r['task_hook'] = rec['task_hook']
            return r

        return 200, {
            'crew_size': len(self.registry),
            'operators': {
                op_id: _op_record(op_id, rec)
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

    # ── Mission package install ────────────────────────────────────────────────

    @staticmethod
    def _is_reserved_path(name: str) -> bool:
        """True if a zip entry name matches a reserved/excluded path pattern."""
        for pat in _RESERVED_PATH_PATTERNS:
            if pat in name:
                return True
        return False

    @staticmethod
    def _verify_mission_package(zip_bytes: bytes, manifest: dict) -> List[str]:
        """Verify package digest and per-file hashes from zip_bytes against manifest.

        Returns a list of error strings. Empty list means all checks passed.
        """
        errors: List[str] = []
        declared_files = {
            entry["path"]: entry
            for entry in manifest.get("files", [])
            if isinstance(entry, dict) and "path" in entry
        }
        declared_digest = manifest.get("package_digest", "")

        try:
            with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
                # Build set of actual payload files (excluding reserved and mission.json)
                actual_paths: set[str] = set()
                file_map: dict[str, bytes] = {}
                for info in zf.infolist():
                    name = info.filename
                    if name == "mission.json":
                        continue
                    if CrewService._is_reserved_path(name):
                        continue
                    if info.is_dir():
                        continue
                    actual_paths.add(name)
                    raw = zf.read(name)
                    file_map[name] = canonical_file_bytes(name, raw)
        except zipfile.BadZipFile:
            return ["not a valid zip file"]
        except Exception as exc:
            return [f"zip extraction error: {exc}"]

        # Extra files in zip not declared in files[]
        extra = actual_paths - set(declared_files.keys())
        if extra:
            errors.append(
                f"extra_files_in_package: {sorted(extra)!r}"
            )

        # Missing files declared in files[] but absent from zip
        missing = set(declared_files.keys()) - actual_paths
        if missing:
            errors.append(
                f"missing_files: {sorted(missing)!r}"
            )

        # Per-file hash verification
        import hashlib as _hashlib
        for path, entry in declared_files.items():
            if path not in file_map:
                continue  # already reported as missing
            canonical = file_map[path]  # already canonicalized above
            actual_sha = _hashlib.sha256(canonical).hexdigest()
            declared_sha = entry.get("sha256", "")
            if actual_sha != declared_sha:
                errors.append(
                    f"file_hash_mismatch: {path!r} "
                    f"(expected {declared_sha!r}, got {actual_sha!r})"
                )
            # size_bytes advisory check
            size_bytes = entry.get("size_bytes")
            if size_bytes is not None and size_bytes != len(canonical):
                errors.append(
                    f"size_bytes_mismatch: {path!r} "
                    f"(declared {size_bytes}, actual {len(canonical)})"
                )

        # Package digest verification
        computed_digest = compute_package_digest(file_map)
        if computed_digest != declared_digest:
            errors.insert(0,
                f"package_digest_mismatch: "
                f"expected {declared_digest!r}, computed {computed_digest!r}"
            )

        return errors

    @staticmethod
    def _verify_mission_signature(manifest: dict, verifier: Verifier) -> tuple[bool, str]:
        """Verify the Ed25519 signature on a mission manifest.

        Returns (True, "") on success.
        Returns (False, error_code) on failure.
        """
        try:
            result = verify_manifest(manifest, verifier)
            if result:
                return True, ""
            return False, "invalid_signature"
        except ValueError as exc:
            if "unknown key_id" in str(exc):
                return False, "unknown_key_id"
            return False, f"signature_error: {exc}"

    def install_mission(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /api/crew/install_mission — full Section H install verification flow.

        Accepts: { zip_b64: str } or { zip_bytes: bytes (internal) }
        Returns 201 on success, 4xx/5xx on verification failure.
        """
        import base64
        from cascadia.missions.registry import MissionRegistry

        # ── Step 1: Parse zip and extract mission.json ─────────────────────
        zip_b64 = payload.get("zip_b64", "")
        zip_bytes_direct = payload.get("_zip_bytes")  # internal test hook

        if zip_b64:
            try:
                zip_bytes = base64.b64decode(zip_b64)
            except Exception:
                return 400, {"error": "invalid_base64", "message": "zip_b64 is not valid base64"}
        elif zip_bytes_direct:
            zip_bytes = zip_bytes_direct
        else:
            return 400, {"error": "bad_request", "message": "zip_b64 required"}

        try:
            with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
                if "mission.json" not in zf.namelist():
                    return 400, {"error": "bad_package", "message": "mission.json not found in zip"}
                manifest = json.loads(zf.read("mission.json"))
        except zipfile.BadZipFile:
            return 400, {"error": "bad_package", "message": "not a valid zip file"}
        except json.JSONDecodeError:
            return 400, {"error": "bad_package", "message": "mission.json is not valid JSON"}

        if not isinstance(manifest, dict) or manifest.get("type") != "mission":
            return 400, {"error": "bad_package", "message": "mission.json type must be 'mission'"}

        # ── Step 2: Schema validation ───────────────────────────────────────
        schema_errors = MissionManifest().validate(manifest)
        if schema_errors:
            return 400, {
                "error": "schema_invalid",
                "message": "mission.json failed schema validation",
                "details": {"errors": schema_errors},
            }

        mission_id = manifest.get("id", "")
        version = manifest.get("version", "")

        # ── Step 3: Signature verification ─────────────────────────────────
        sig_ok, sig_error = self._verify_mission_signature(manifest, self._verifier)
        if not sig_ok:
            return 400, {
                "error": sig_error,
                "message": f"signature verification failed: {sig_error}",
                "details": {},
            }

        # ── Step 4: Kill switch check ───────────────────────────────────────
        if self._kill_switch.is_revoked(mission_id, version):
            return 403, {
                "error": "package_revoked",
                "message": f"Mission {mission_id!r} v{version} has been revoked",
                "details": {},
            }

        # ── Step 5 & 6: Package digest + per-file hash verification ─────────
        pkg_errors = self._verify_mission_package(zip_bytes, manifest)
        if pkg_errors:
            # Determine the primary error code
            primary = pkg_errors[0]
            if primary.startswith("package_digest_mismatch"):
                error_code = "package_digest_mismatch"
            elif "file_hash_mismatch" in primary:
                error_code = "file_hash_mismatch"
            elif "extra_files" in primary:
                error_code = "extra_files_in_package"
            elif "missing_files" in primary:
                error_code = "missing_files"
            else:
                error_code = "package_verification_failed"
            return 400, {
                "error": error_code,
                "message": "package integrity check failed",
                "details": {"errors": pkg_errors},
            }

        # ── Step 7: Tier verification ───────────────────────────────────────
        tier_required = manifest.get("tier_required", "lite")
        try:
            body = json.dumps({"tier_required": tier_required}).encode()
            req = _urllib_request.Request(
                "http://127.0.0.1:6100/api/license/check_tier",
                data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with _urllib_request.urlopen(req, timeout=3) as r:
                tier_data = json.loads(r.read().decode())
                if not tier_data.get("ok", True):
                    return 403, {
                        "error": "tier_insufficient",
                        "message": f"License tier insufficient for {tier_required!r}",
                        "details": {"reason": tier_data.get("reason", "")},
                    }
        except Exception:
            # LICENSE_GATE unreachable — fail closed
            return 503, {
                "error": "license_gate_unavailable",
                "message": "LICENSE_GATE is unavailable; install aborted (fail closed)",
                "details": {},
            }

        # ── Step 8: Version compatibility ───────────────────────────────────
        min_version = manifest.get("min_zyrcon_version")
        if min_version:
            try:
                from cascadia import VERSION as _cv
                def _parse(v: str) -> tuple:
                    return tuple(int(x) for x in v.split(".")[:3])
                if _parse(_cv) < _parse(min_version):
                    return 422, {
                        "error": "version_incompatible",
                        "message": (
                            f"Mission requires Zyrcon AI Server >= {min_version}, "
                            f"installed: {_cv}"
                        ),
                        "details": {},
                    }
            except Exception:
                pass  # version check is best-effort

        # ── Step 9: Dependency check ────────────────────────────────────────
        _cfg = getattr(self, "_config", {})
        reg_path = _registry_path(_cfg)
        reg = _load_registry(reg_path)
        installed_operators = {op.get("id") for op in reg.get("operators", [])}
        required_ops = manifest.get("operators", {}).get("required", [])
        missing_ops = []
        for dep in required_ops:
            # dep may be "scout" or "scout@>=1.2.0" — extract bare ID
            dep_id = dep.split("@")[0]
            if dep_id not in installed_operators:
                missing_ops.append(dep)
        if missing_ops:
            return 422, {
                "error": "missing_operator",
                "message": f"Required operators not installed: {missing_ops!r}",
                "details": {"missing": missing_ops},
            }

        # ── Step 10: Extract, register, return ─────────────────────────────
        # Determine install path
        missions_cfg = _cfg.get("missions", {})
        packages_root = missions_cfg.get("packages_root") if isinstance(missions_cfg, dict) else None
        if not packages_root:
            packages_root = str(Path(__file__).parent.parent.parent / "missions")
        install_path = Path(packages_root) / mission_id

        # Extract to temp first, then move (atomicity)
        import tempfile
        tmp_dir = Path(packages_root) / ".install_tmp" / mission_id
        try:
            tmp_dir.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
                # Strip reserved paths on extraction
                for info in zf.infolist():
                    if self._is_reserved_path(info.filename):
                        continue
                    zf.extract(info, tmp_dir)
            if install_path.exists():
                shutil.rmtree(install_path)
            shutil.move(str(tmp_dir), str(install_path))
        except Exception as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return 500, {"error": "extraction_failed", "message": str(exc), "details": {}}

        # Write to MissionRegistry with stitch_registered: false
        _mission_registry_file = str(Path(packages_root) / "missions_registry.json")
        registry = MissionRegistry(packages_root=packages_root, registry_file=_mission_registry_file)
        registry_entry = {
            "id": mission_id,
            "version": version,
            "name": manifest.get("name", mission_id),
            "tier_required": tier_required,
            "runtime": manifest.get("runtime", "server"),
            "author": manifest.get("author", ""),
            "signed_by": manifest.get("signed_by", ""),
            "key_id": manifest.get("key_id", ""),
            "install_path": str(install_path),
            "installed_at": _now_iso(),
            "capabilities": manifest.get("capabilities", []),
            "workflow_ids": list((manifest.get("workflows") or {}).keys()),
            "stitch_registered": False,
        }
        registry.register_install(registry_entry)

        # ── STITCH registration (best-effort) ──────────────────────────────
        stitch_registered = False
        workflows_map = manifest.get("workflows") or {}
        if workflows_map:
            try:
                stitch_payload = json.dumps({
                    "mission_id": mission_id,
                    "install_path": str(install_path),
                    "workflows": workflows_map,
                    "manifest": {k: v for k, v in manifest.items() if k not in ("signature",)},
                }).encode()
                stitch_req = _urllib_request.Request(
                    f"http://127.0.0.1:{_STITCH_PORT}/api/workflows/register_mission",
                    data=stitch_payload, method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with _urllib_request.urlopen(stitch_req, timeout=5) as r:
                    if r.status in (200, 201):
                        stitch_registered = True
                        registry.update_stitch_registered(mission_id, True)
            except Exception:
                pass  # best-effort; stitch_pending remains True in response
        else:
            # No workflows to register; mark as done immediately
            stitch_registered = True
            registry.update_stitch_registered(mission_id, True)

        _append_install_log(_cfg, {
            "action": "install_mission",
            "mission_id": mission_id,
            "version": version,
            "stitch_registered": stitch_registered,
            "installed_at": _now_iso(),
        })
        self.runtime.logger.info(
            "CREW installed mission: %s v%s stitch=%s", mission_id, version, stitch_registered
        )
        return 201, {
            "installed": mission_id,
            "version": version,
            "install_path": str(install_path),
            "stitch_registered": stitch_registered,
            "stitch_pending": not stitch_registered,
        }

    def uninstall_mission(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /api/crew/uninstall_mission — two-phase mission removal.

        Phase 1 (dry_run: true): returns affected_workflows, keeps files.
        Phase 2 (dry_run: false, confirmed: true): removes files, updates registry.
        """
        from cascadia.missions.registry import MissionRegistry

        mission_id = payload.get("mission_id", "")
        dry_run = payload.get("dry_run", True)
        confirmed = payload.get("confirmed", False)

        if not mission_id:
            return 400, {"error": "bad_request", "message": "mission_id required"}

        _cfg = getattr(self, "_config", {})
        missions_cfg = _cfg.get("missions", {})
        packages_root = missions_cfg.get("packages_root") if isinstance(missions_cfg, dict) else None
        if not packages_root:
            packages_root = str(Path(__file__).parent.parent.parent / "missions")

        _mission_registry_file2 = str(Path(packages_root) / "missions_registry.json")
        registry = MissionRegistry(packages_root=packages_root, registry_file=_mission_registry_file2)
        entry = next(
            (m for m in registry.list_installed() if m.get("id") == mission_id),
            None,
        )
        if entry is None:
            return 404, {"error": "not_found", "message": f"Mission {mission_id!r} not installed"}

        # Find workflows that reference this mission
        affected_workflows: List[str] = []
        try:
            db_path = _cfg.get("database_path", "./data/runtime/cascadia.db")
            import sqlite3
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT id, name, nodes FROM workflow_definitions WHERE deleted_at IS NULL"
                ).fetchall()
            for row in rows:
                try:
                    nodes = json.loads(row["nodes"] or "[]")
                except Exception:
                    nodes = []
                if isinstance(nodes, list):
                    for node in nodes:
                        if isinstance(node, dict) and mission_id in str(node):
                            affected_workflows.append(row["name"] or row["id"])
                            break
        except Exception:
            pass

        if dry_run:
            install_path = entry.get("install_path", str(Path(packages_root) / mission_id))
            return 200, {
                "dry_run": True,
                "mission_id": mission_id,
                "version": entry.get("version", ""),
                "install_path": install_path,
                "affected_workflows": affected_workflows,
                "message": "Pass dry_run: false and confirmed: true to proceed.",
            }

        if not confirmed:
            return 400, {
                "error": "confirmation_required",
                "message": "Pass confirmed: true to confirm uninstall.",
            }

        # Remove files
        install_path = Path(entry.get("install_path", str(Path(packages_root) / mission_id)))
        if install_path.exists():
            try:
                shutil.rmtree(install_path)
            except Exception as exc:
                return 500, {"error": "removal_failed", "message": str(exc)}

        # Remove from registry
        try:
            _rfile = Path(packages_root) / "missions_registry.json"
            if _rfile.exists():
                data = json.loads(_rfile.read_text(encoding="utf-8"))
                data["installed"] = [
                    m for m in data.get("installed", [])
                    if not (isinstance(m, dict) and m.get("id") == mission_id)
                ]
                _rfile.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

        _append_install_log(_cfg, {
            "action": "uninstall_mission",
            "mission_id": mission_id,
            "uninstalled_at": _now_iso(),
            "affected_workflows": affected_workflows,
        })
        self.runtime.logger.info("CREW uninstalled mission: %s", mission_id)
        return 200, {
            "uninstalled": mission_id,
            "affected_workflows": affected_workflows,
        }

    def watchdog_status(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """GET /api/watchdog/status — operator health and restart counts."""
        return 200, self._watchdog.get_status()

    def _start_health_poller(self) -> None:
        """Background thread: poll registered operators and evict dead ones.

        Operators that crash or are killed cannot call /deregister. This poller
        detects them via health checks and removes stale entries so CHIEF does not
        route to ports that refuse connections.
        """
        def _loop() -> None:
            while True:
                _time.sleep(_HEALTH_POLL_INTERVAL)
                to_evict = []
                for op_id, rec in list(self.registry.items()):
                    port = rec.get('port')
                    if not port:
                        continue
                    health_hook = rec.get('health_hook', '/health')
                    try:
                        with _urllib_request.urlopen(
                            f'http://127.0.0.1:{port}{health_hook}', timeout=3
                        ) as r:
                            alive = r.status == 200
                    except Exception:
                        alive = False
                    if alive:
                        self._health_failures.pop(op_id, None)
                    else:
                        count = self._health_failures.get(op_id, 0) + 1
                        self._health_failures[op_id] = count
                        if count >= _HEALTH_EVICT_AFTER:
                            to_evict.append(op_id)
                for op_id in to_evict:
                    self.registry.pop(op_id, None)
                    self._health_failures.pop(op_id, None)
                    self.runtime.logger.warning(
                        'CREW: evicted %s — unreachable for %d consecutive checks',
                        op_id, _HEALTH_EVICT_AFTER,
                    )

        threading.Thread(target=_loop, daemon=True, name='crew-health-poller').start()
        self.runtime.logger.info(
            'CREW health poller started (%ds interval, evict after %d misses)',
            _HEALTH_POLL_INTERVAL, _HEALTH_EVICT_AFTER,
        )

    def start(self) -> None:
        self._watchdog.start()
        self._start_health_poller()
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
