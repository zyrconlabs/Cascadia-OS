"""
cascadia/depot/api.py — Task A2
DEPOT API Server · Zyrcon Labs · v1.0.0

Owns: marketplace browsing, operator manifest serving, purchase event handling,
      install proxying to CREW, catalog search and filtering.
Does not own: payment processing (Stripe webhook), credential storage (Vault),
              operator runtime (FLINT), dashboard display (PRISM).

Port: 6208
"""
from __future__ import annotations

import http.server
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from cascadia.depot.manifest_validator import (
    validate_depot_manifest,
    VALID_CATEGORIES,
    VALID_TIERS,
)

NAME = "depot-api"
VERSION = "1.0.0"
PORT = 6208

# CREW install endpoint — forward installs to the running CREW service
CREW_PORT = int(os.environ.get('CREW_PORT', '8100'))
CREW_URL = f'http://127.0.0.1:{CREW_PORT}'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [depot-api] %(message)s',
)
log = logging.getLogger(NAME)

_start_time = time.time()

# ── Catalog ───────────────────────────────────────────────────────────────────

# In production this is loaded from disk / NATS KV.  For now we index
# all manifest.json files found under cascadia/connectors/ and any
# operator package directories that ship with cascadia-os.
_catalog: Dict[str, Dict[str, Any]] = {}
_catalog_lock = threading.Lock()


def _scan_manifests(root: Path) -> List[Dict[str, Any]]:
    """Walk root recursively and collect all valid manifest.json files."""
    manifests = []
    for path in root.rglob('manifest.json'):
        try:
            data = json.loads(path.read_text())
            result = validate_depot_manifest(data)
            if result.valid:
                data['_manifest_path'] = str(path)
                manifests.append(data)
            else:
                log.debug('Skipping invalid manifest at %s: %s', path, result.errors)
        except Exception as exc:
            log.debug('Could not read manifest at %s: %s', path, exc)
    return manifests


def load_catalog(extra_dirs: Optional[List[Path]] = None) -> int:
    """
    (Re-)load the catalog by scanning for manifest.json files.
    Returns the number of entries loaded.
    """
    roots = [Path(__file__).parent.parent / 'connectors']
    if extra_dirs:
        roots.extend(extra_dirs)

    found: Dict[str, Dict[str, Any]] = {}
    for root in roots:
        if root.exists():
            for m in _scan_manifests(root):
                found[m['id']] = m

    with _catalog_lock:
        _catalog.clear()
        _catalog.update(found)

    log.info('Catalog loaded: %s entries', len(_catalog))
    return len(_catalog)


def get_catalog_entries(
    category: Optional[str] = None,
    tier: Optional[str] = None,
    q: Optional[str] = None,
    type_filter: Optional[str] = None,
    installed_only: bool = False,
    free_only: bool = False,
) -> List[Dict[str, Any]]:
    """Filter and search the catalog. Returns sanitized listing dicts."""
    with _catalog_lock:
        entries = list(_catalog.values())

    if category:
        entries = [e for e in entries if e.get('category') == category]
    if tier:
        entries = [e for e in entries if e.get('tier_required') == tier]
    if type_filter:
        entries = [e for e in entries if e.get('type') == type_filter]
    if installed_only:
        entries = [e for e in entries if e.get('installed_by_default')]
    if free_only:
        entries = [e for e in entries if float(e.get('price', 0)) == 0]
    if q:
        q_lower = q.lower()
        entries = [
            e for e in entries
            if q_lower in e.get('name', '').lower()
            or q_lower in e.get('description', '').lower()
            or q_lower in e.get('id', '').lower()
        ]

    return [_safe_listing(e) for e in entries]


def _safe_listing(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Return public-safe listing fields (strip internal paths)."""
    keys = ('id', 'name', 'type', 'version', 'description', 'author',
            'price', 'tier_required', 'port', 'category', 'industries',
            'installed_by_default', 'safe_to_uninstall', 'auth_type',
            'nats_subjects', 'approval_required_for_writes')
    return {k: entry[k] for k in keys if k in entry}


def get_entry(operator_id: str) -> Optional[Dict[str, Any]]:
    with _catalog_lock:
        return _catalog.get(operator_id)


# ── Install proxy ─────────────────────────────────────────────────────────────

def proxy_install(operator_id: str, requester: str = '',
                  options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Forward an install request to the CREW service.
    Returns the CREW response envelope.
    """
    entry = get_entry(operator_id)
    if entry is None:
        return {'ok': False, 'error': f'operator {operator_id!r} not in catalog'}

    body = json.dumps({
        'manifest': entry,
        'source': 'depot',
        'requested_by': requester,
        **(options or {}),
    }).encode()

    try:
        req = urllib.request.Request(
            f'{CREW_URL}/install_operator',
            data=body,
            method='POST',
            headers={'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            err_body = json.loads(exc.read())
        except Exception:
            err_body = {}
        return {'ok': False, 'error': f'CREW HTTP {exc.code}', 'detail': err_body}
    except Exception as exc:
        return {'ok': False, 'error': f'CREW unreachable: {exc}'}


# ── HTTP server ───────────────────────────────────────────────────────────────

class _DepotHandler(http.server.BaseHTTPRequestHandler):

    def _json(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> bytes:
        n = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(n) if n else b''

    def _qs(self) -> Dict[str, str]:
        parsed = urllib.parse.urlparse(self.path)
        return {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

    def _path(self) -> str:
        return urllib.parse.urlparse(self.path).path.rstrip('/')

    def do_GET(self) -> None:
        path = self._path()
        qs = self._qs()

        # ── Health ──
        if path == '/health':
            with _catalog_lock:
                count = len(_catalog)
            self._json(200, {
                'ok': True,
                'status': 'healthy', 'service': NAME, 'version': VERSION,
                'port': PORT, 'catalog_entries': count,
                'uptime_seconds': round(time.time() - _start_time),
            })
            return

        # ── Catalog listing ──
        if path == '/v1/operators':
            entries = get_catalog_entries(
                category=qs.get('category'),
                tier=qs.get('tier'),
                q=qs.get('q'),
                type_filter=qs.get('type'),
                installed_only=qs.get('installed_only') == '1',
                free_only=qs.get('free_only') == '1',
            )
            self._json(200, {
                'ok': True,
                'count': len(entries),
                'operators': entries,
                'timestamp': datetime.now(timezone.utc).isoformat(),
            })
            return

        # ── Single operator ──
        if path.startswith('/v1/operators/'):
            operator_id = path[len('/v1/operators/'):]
            entry = get_entry(operator_id)
            if entry:
                self._json(200, {'ok': True, 'operator': _safe_listing(entry)})
            else:
                self._json(404, {'ok': False, 'error': f'{operator_id!r} not in catalog'})
            return

        # ── Categories ──
        if path == '/v1/categories':
            self._json(200, {'ok': True, 'categories': sorted(VALID_CATEGORIES)})
            return

        # ── Tiers ──
        if path == '/v1/tiers':
            self._json(200, {'ok': True, 'tiers': sorted(VALID_TIERS)})
            return

        # ── Catalog reload (admin) ──
        if path == '/v1/catalog/reload':
            n = load_catalog()
            self._json(200, {'ok': True, 'loaded': n})
            return

        self._json(404, {'error': 'not found'})

    def do_POST(self) -> None:
        path = self._path()

        # ── Install ──
        if path.startswith('/v1/operators/') and path.endswith('/install'):
            operator_id = path[len('/v1/operators/'):-len('/install')]
            try:
                data = json.loads(self._body()) if self.headers.get('Content-Length', '0') != '0' else {}
            except Exception:
                self._json(400, {'error': 'invalid JSON'})
                return
            result = proxy_install(operator_id,
                                   requester=data.get('requested_by', ''),
                                   options=data.get('options'))
            status = 200 if result.get('ok') else 502
            self._json(status, result)
            return

        # ── Purchase webhook (Stripe) ──
        if path == '/v1/purchase':
            try:
                data = json.loads(self._body())
            except Exception:
                self._json(400, {'error': 'invalid JSON'})
                return
            result = handle_purchase(data)
            self._json(200 if result['ok'] else 400, result)
            return

        self._json(404, {'error': 'not found'})

    def log_message(self, *_args: Any) -> None:
        pass


# ── Purchase handler ──────────────────────────────────────────────────────────

def handle_purchase(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle a confirmed purchase event (from Stripe or internal billing).
    Triggers auto-install for the purchased operator.
    """
    operator_id = data.get('operator_id', '')
    customer_id = data.get('customer_id', '')

    if not operator_id:
        return {'ok': False, 'error': 'operator_id required'}

    entry = get_entry(operator_id)
    if entry is None:
        return {'ok': False, 'error': f'operator {operator_id!r} not in catalog'}

    log.info('Purchase received: %s for customer %s', operator_id, customer_id)

    # Auto-install after purchase
    result = proxy_install(operator_id, requester=customer_id,
                           options={'source': 'purchase'})

    return {
        'ok': True,
        'operator_id': operator_id,
        'customer_id': customer_id,
        'install_result': result,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def start(extra_catalog_dirs: Optional[List[Path]] = None,
          block: bool = True) -> http.server.HTTPServer:
    load_catalog(extra_catalog_dirs)
    server = http.server.HTTPServer(('', PORT), _DepotHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    log.info('%s v%s running on port %s (%s catalog entries)',
             NAME, VERSION, PORT, len(_catalog))
    if block:
        try:
            t.join()
        except KeyboardInterrupt:
            server.shutdown()
    return server


if __name__ == '__main__':
    start(block=True)
