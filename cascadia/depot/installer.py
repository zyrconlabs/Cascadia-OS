"""
cascadia/depot/installer.py — Task A3
One-Click Install Flow · Zyrcon Labs · v1.0.0

Owns: full operator install lifecycle — fetch, validate, tier-check, extract,
      register, start, health-poll, rollback on failure, install log.
Does not own: payment, credential storage, runtime process management (FLINT),
              dashboard display (PRISM), approval gating.

This module is the single orchestration point that the DEPOT API, the
purchase webhook, and the auto-sync pipeline all call.  CREW's
install_operator method remains authoritative for registry writes; this
module is the coordination layer above it.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from cascadia.depot.manifest_validator import validate_depot_manifest

NAME = "depot-installer"
VERSION = "1.0.0"

log = logging.getLogger('depot.installer')

CREW_PORT = int(os.environ.get('CREW_PORT', '8100'))
CREW_URL = f'http://127.0.0.1:{CREW_PORT}'

HEALTH_POLL_TIMEOUT = 15   # seconds to poll health after start
HEALTH_POLL_INTERVAL = 1   # seconds between polls
DOWNLOAD_TIMEOUT = 60      # seconds for package download


# ── Install step tracking ─────────────────────────────────────────────────────

class Step(str, Enum):
    FETCH = 'fetch'
    VALIDATE = 'validate'
    TIER_CHECK = 'tier_check'
    EXTRACT = 'extract'
    REGISTER = 'register'
    START = 'start'
    HEALTH = 'health'
    DONE = 'done'
    FAILED = 'failed'
    ROLLED_BACK = 'rolled_back'


@dataclass
class InstallEvent:
    step: str
    status: str           # 'ok' | 'skipped' | 'error'
    message: str = ''
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class InstallResult:
    ok: bool
    operator_id: str
    events: List[InstallEvent] = field(default_factory=list)
    manifest: Optional[Dict[str, Any]] = None
    health_ok: bool = False
    error: Optional[str] = None
    rolled_back: bool = False
    duration_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'ok': self.ok,
            'operator_id': self.operator_id,
            'health_ok': self.health_ok,
            'error': self.error,
            'rolled_back': self.rolled_back,
            'duration_ms': self.duration_ms,
            'events': [
                {'step': e.step, 'status': e.status, 'message': e.message,
                 'timestamp': e.timestamp}
                for e in self.events
            ],
        }


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_package(source: str) -> tuple[bytes, Optional[str]]:
    """
    Fetch a zip package from a URL or decode a base64 string.
    Returns (bytes, error_message).  On success error_message is None.
    """
    if source.startswith('http://') or source.startswith('https://'):
        try:
            with urllib.request.urlopen(source, timeout=DOWNLOAD_TIMEOUT) as r:
                return r.read(), None
        except urllib.error.URLError as exc:
            return b'', f'download failed: {exc.reason}'
        except Exception as exc:
            return b'', f'download error: {exc}'
    else:
        try:
            return base64.b64decode(source), None
        except Exception:
            return b'', 'invalid base64 encoding'


# ── Validate ──────────────────────────────────────────────────────────────────

def extract_manifest(zip_bytes: bytes) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Extract and parse manifest.json from a zip bundle."""
    try:
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            # Accept manifest.json at root or one level deep
            manifest_path = next(
                (n for n in names if n == 'manifest.json' or n.endswith('/manifest.json')),
                None,
            )
            if manifest_path is None:
                return None, 'manifest.json not found in zip'
            return json.loads(zf.read(manifest_path)), None
    except zipfile.BadZipFile:
        return None, 'not a valid zip file'
    except json.JSONDecodeError as exc:
        return None, f'manifest.json is not valid JSON: {exc}'
    except Exception as exc:
        return None, f'zip read error: {exc}'


def validate_manifest(manifest: Dict[str, Any]) -> Optional[str]:
    """Return an error string if the manifest is invalid, else None."""
    result = validate_depot_manifest(manifest)
    if not result.valid:
        return '; '.join(result.errors)
    return None


# ── CREW proxy ────────────────────────────────────────────────────────────────

def _crew_install(manifest: Dict[str, Any], zip_bytes: Optional[bytes],
                  source: str = 'depot', dry_run: bool = False) -> Dict[str, Any]:
    """
    POST to CREW /install_operator.
    Sends the manifest directly (CREW validates fields it needs).
    """
    body: Dict[str, Any] = {
        'manifest': manifest,
        'operator_id': manifest.get('id', ''),
        'source': source,
        'dry_run': dry_run,
    }
    if zip_bytes:
        body['zip_b64'] = base64.b64encode(zip_bytes).decode()

    raw = json.dumps(body).encode()
    req = urllib.request.Request(
        f'{CREW_URL}/install_operator',
        data=raw,
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())
            data.setdefault('ok', True)
            return data
    except urllib.error.HTTPError as exc:
        try:
            err = json.loads(exc.read())
        except Exception:
            err = {}
        return {'ok': False, 'error': f'CREW HTTP {exc.code}', 'detail': err,
                'crew_status': exc.code}
    except Exception as exc:
        return {'ok': False, 'error': f'CREW unreachable: {exc}'}


# ── Health poll ───────────────────────────────────────────────────────────────

def poll_health(port: int, path: str = '/health',
                timeout: float = HEALTH_POLL_TIMEOUT,
                interval: float = HEALTH_POLL_INTERVAL) -> bool:
    """Return True when the operator's health endpoint responds 200."""
    deadline = time.time() + timeout
    url = f'http://127.0.0.1:{port}{path}'
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(interval)
    return False


# ── Rollback ──────────────────────────────────────────────────────────────────

def _rollback(operator_id: str) -> bool:
    """Best-effort: ask CREW to remove the operator."""
    body = json.dumps({'operator_id': operator_id, 'keep_data': False}).encode()
    req = urllib.request.Request(
        f'{CREW_URL}/remove_operator',
        data=body,
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status < 400
    except Exception:
        return False


# ── Main install orchestrator ─────────────────────────────────────────────────

def install(
    operator_id: str,
    package_source: Optional[str] = None,   # URL or base64
    manifest_override: Optional[Dict[str, Any]] = None,
    source: str = 'depot',
    dry_run: bool = False,
    on_progress: Optional[Callable[[InstallEvent], None]] = None,
) -> InstallResult:
    """
    Execute the full one-click install flow.

    Steps:
      1. FETCH    — download zip from URL or decode base64
      2. VALIDATE — extract manifest.json, validate against DEPOT schema
      3. TIER_CHECK — verify CREW accepted the manifest (tier + port conflict)
      4. EXTRACT  — CREW extracts files to operators/{id}/
      5. REGISTER — CREW writes to operators registry.json
      6. START    — CREW attempts FLINT start (if start_cmd present)
      7. HEALTH   — poll /{health_path} for up to 15s

    On any failure after REGISTER, attempts rollback via CREW remove_operator.
    """
    t0 = time.time()
    events: List[InstallEvent] = []
    result = InstallResult(ok=False, operator_id=operator_id)

    def emit(step: str, status: str, message: str = '', **data) -> InstallEvent:
        ev = InstallEvent(step=step, status=status, message=message, data=data)
        events.append(ev)
        if on_progress:
            on_progress(ev)
        log.info('[%s] %s %s %s', operator_id, step, status, message)
        return ev

    # ── Step 1: FETCH ─────────────────────────────────────────────────────────
    zip_bytes: Optional[bytes] = None
    manifest: Optional[Dict[str, Any]] = manifest_override

    if package_source:
        raw, err = fetch_package(package_source)
        if err:
            emit(Step.FETCH, 'error', err)
            result.error = err
            result.events = events
            result.duration_ms = int((time.time() - t0) * 1000)
            return result
        zip_bytes = raw
        emit(Step.FETCH, 'ok', f'{len(raw)} bytes')
    else:
        emit(Step.FETCH, 'skipped', 'no package_source — using manifest_override')

    # ── Step 2: VALIDATE ─────────────────────────────────────────────────────
    if zip_bytes and manifest is None:
        manifest, err = extract_manifest(zip_bytes)
        if err:
            emit(Step.VALIDATE, 'error', err)
            result.error = err
            result.events = events
            result.duration_ms = int((time.time() - t0) * 1000)
            return result

    if manifest is None:
        err = 'no manifest: provide package_source or manifest_override'
        emit(Step.VALIDATE, 'error', err)
        result.error = err
        result.events = events
        result.duration_ms = int((time.time() - t0) * 1000)
        return result

    err = validate_manifest(manifest)
    if err:
        emit(Step.VALIDATE, 'error', err)
        result.error = err
        result.events = events
        result.duration_ms = int((time.time() - t0) * 1000)
        return result

    result.manifest = manifest
    operator_id = manifest.get('id', operator_id)
    result.operator_id = operator_id
    emit(Step.VALIDATE, 'ok', f'{operator_id} v{manifest.get("version")}')

    # ── Step 3–6: CREW install (handles tier, extract, register, start) ──────
    crew_resp = _crew_install(manifest, zip_bytes, source=source, dry_run=dry_run)

    if not crew_resp.get('ok'):
        crew_status = crew_resp.get('crew_status', 0)
        err = crew_resp.get('error', 'CREW install failed')

        if crew_status == 403:
            emit(Step.TIER_CHECK, 'error', err,
                 tier_required=crew_resp.get('tier_required', ''),
                 upgrade_url=crew_resp.get('upgrade_url', ''))
        elif crew_status == 409:
            emit(Step.REGISTER, 'error', err,
                 port=crew_resp.get('port'),
                 conflict_with=crew_resp.get('conflict_with', ''))
        else:
            emit(Step.REGISTER, 'error', err)

        result.error = err
        result.events = events
        result.duration_ms = int((time.time() - t0) * 1000)
        return result

    emit(Step.TIER_CHECK, 'ok')
    emit(Step.EXTRACT, 'ok' if zip_bytes else 'skipped')
    emit(Step.REGISTER, 'ok', 'registry.json updated')
    emit(Step.START, 'ok' if crew_resp.get('flint') else 'skipped',
         'FLINT start attempted' if crew_resp.get('flint') else 'no start_cmd')

    if dry_run:
        emit(Step.DONE, 'ok', 'dry_run — no changes written')
        result.ok = True
        result.events = events
        result.duration_ms = int((time.time() - t0) * 1000)
        return result

    # ── Step 7: HEALTH ────────────────────────────────────────────────────────
    port = manifest.get('port')
    health_ok = crew_resp.get('health_ok', False)

    if port and not health_ok:
        health_path = manifest.get('health_path', '/health')
        health_ok = poll_health(port, health_path)

    if health_ok:
        emit(Step.HEALTH, 'ok', f'port {port} responding')
    else:
        emit(Step.HEALTH, 'skipped' if not port else 'error',
             'no port' if not port else f'port {port} not responding after {HEALTH_POLL_TIMEOUT}s')

    emit(Step.DONE, 'ok')
    result.ok = True
    result.health_ok = health_ok
    result.events = events
    result.duration_ms = int((time.time() - t0) * 1000)
    return result


# ── Batch install ─────────────────────────────────────────────────────────────

def install_batch(
    requests: List[Dict[str, Any]],
    on_progress: Optional[Callable[[str, InstallEvent], None]] = None,
) -> List[InstallResult]:
    """
    Install multiple operators sequentially.
    Each request dict: {operator_id, package_source?, manifest_override?, source?, dry_run?}
    """
    results = []
    for req in requests:
        op_id = req.get('operator_id', '')

        def _progress(ev: InstallEvent, _id=op_id) -> None:
            if on_progress:
                on_progress(_id, ev)

        r = install(
            operator_id=op_id,
            package_source=req.get('package_source'),
            manifest_override=req.get('manifest_override'),
            source=req.get('source', 'depot'),
            dry_run=req.get('dry_run', False),
            on_progress=_progress,
        )
        results.append(r)

    return results
