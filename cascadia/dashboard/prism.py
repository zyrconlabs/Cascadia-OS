"""
prism/prism.py - Cascadia OS v0.44
PRISM: Command center and dashboard aggregation layer.

Owns: aggregating status from all Cascadia OS components,
      surfacing run states, approval queues, dependency blocks,
      crew membership, and system health in one queryable API.
Does not own: execution (FLINT/BEACON/STITCH), storage (VAULT),
              encryption (CURTAIN), communication (BELL/VANGUARD).

PRISM is the window into everything running on Cascadia OS.
A non-technical user should be able to understand the system state
from PRISM alone without reading logs.
"""
# MATURITY: FUNCTIONAL — DB aggregation queries work. Real-time push is v0.35.
from __future__ import annotations

import argparse
import threading
import time as _time
from collections import defaultdict
from pathlib import Path
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib import request as urllib_request

from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RateLimiter:
    """Per-IP sliding-window rate limiter backed by an in-memory deque."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: Dict[str, list] = defaultdict(list)

    def check(self, key: str, limit: int = 30, window: int = 60) -> bool:
        """Return True if key is within limit. Side-effect: records this call."""
        now = _time.monotonic()
        cutoff = now - window
        with self._lock:
            hits = self._windows[key]
            # Evict expired entries
            while hits and hits[0] < cutoff:
                hits.pop(0)
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True


def _http_get(port: int, path: str, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
    try:
        with urllib_request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _http_post(port: int, path: str, payload: Dict[str, Any], timeout: float = 2.0) -> Optional[Dict[str, Any]]:
    try:
        data = json.dumps(payload).encode()
        req = urllib_request.Request(
            f'http://127.0.0.1:{port}{path}', data=data, method='POST',
            headers={'Content-Type': 'application/json'},
        )
        with urllib_request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _http_delete(port: int, path: str, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
    try:
        req = urllib_request.Request(
            f'http://127.0.0.1:{port}{path}', method='DELETE',
            headers={'Content-Type': 'application/json'},
        )
        with urllib_request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


class PrismService:
    """
    PRISM - Dashboard and command center.
    Owns status aggregation and readable system state.
    Does not own execution, storage, or communication.
    """

    def __init__(self, config_path: str, name: str) -> None:
        self.config = load_config(config_path)
        component = next(c for c in self.config['components'] if c['name'] == name)
        self.runtime = ServiceRuntime(
            name=name, port=component['port'],
            heartbeat_file=component['heartbeat_file'],
            log_dir=self.config['log_dir'],
        )
        self.config['__config_path__'] = config_path
        # Build port map from config
        self._ports: Dict[str, int] = {
            c['name']: c['port'] for c in self.config['components']
        }
        self._flint_port: int = self.config['flint']['status_port']
        self._rate_limiter = RateLimiter()

        # Register all PRISM routes
        self.runtime.register_route('GET',  '/',                      self.serve_ui)
        self.runtime.register_route('GET',  '/api/prism/overview',    self.overview)
        self.runtime.register_route('GET',  '/api/prism/system',      self.system_status)
        self.runtime.register_route('GET',  '/api/prism/crew',        self.crew_status)
        self.runtime.register_route('GET',  '/api/prism/runs',        self.run_summary)
        self.runtime.register_route('POST', '/api/prism/run',         self.run_detail)
        self.runtime.register_route('GET',  '/api/prism/approvals',   self.pending_approvals)
        self.runtime.register_route('GET',  '/api/prism/blocked',     self.blocked_runs)
        self.runtime.register_route('GET',  '/api/prism/workflows',   self.workflow_list)
        self.runtime.register_route('GET',  '/api/prism/sentinel',    self.sentinel_status)
        self.runtime.register_route('POST', '/api/prism/approve',    self.approve_action)
        self.runtime.register_route('GET',  '/api/prism/models',     self.models_list)
        self.runtime.register_route('GET',  '/api/prism/operators',  self.operator_status)
        self.runtime.register_route('GET',  '/setup',                self.serve_setup)
        self.runtime.register_route('GET',  '/api/prism/health-check',   self.full_health_check)
        self.runtime.register_route('GET',  '/api/prism/hardware',        self.hardware_info)
        self.runtime.register_route('GET',  '/api/prism/settings',        self.get_settings)
        self.runtime.register_route('POST', '/api/prism/settings',        self.save_settings)
        self.runtime.register_route('GET',  '/api/prism/setup-progress',  self.setup_progress)
        self.runtime.register_route('POST', '/api/prism/almanac',          self.almanac_query)
        self.runtime.register_route('GET',  '/api/prism/scheduler',        self.scheduler_status)
        self.runtime.register_route('POST', '/api/prism/runs/outcome',     self.record_run_outcome)
        self.runtime.register_route('GET',  '/api/prism/pairing/code',     self.pairing_code)
        self.runtime.register_route('POST', '/api/prism/pairing/validate', self.pairing_validate)
        self.runtime.register_route('GET',  '/api/prism/pairing/status',   self.pairing_status)
        self.runtime.register_route('POST', '/api/prism/leads/recover',    self.leads_recover)
        # Sprint v2
        self.runtime.register_route('POST', '/api/prism/stripe/webhook',       self.stripe_webhook)
        self.runtime.register_route('POST', '/api/prism/approve/edit',         self.approve_edit)
        self.runtime.register_route('GET',  '/api/prism/approvals/analytics',  self.approval_analytics)
        self.runtime.register_route('GET',  '/api/prism/outcomes',             self.approval_outcomes)
        self.runtime.register_route('GET',  '/api/prism/audit',                self.audit_log)
        self.runtime.register_route('GET',  '/api/prism/audit/export',         self.audit_export)
        self.runtime.register_route('GET',  '/api/prism/audit/verify',         self.audit_verify)
        self.runtime.register_route('GET',  '/api/prism/fleet',                self.fleet_status)
        self.runtime.register_route('POST', '/api/prism/fleet/register',       self.fleet_register)
        self.runtime.register_route('POST', '/api/prism/fleet/remove',         self.fleet_remove)
        self.runtime.register_route('GET',  '/api/prism/depot/operators',      self.depot_operators)
        self.runtime.register_route('POST', '/api/prism/depot/operator',       self.depot_operator)
        self.runtime.register_route('GET',  '/api/prism/social/scheduled',     self.social_scheduled)
        self.runtime.register_route('GET',  '/api/prism/system/monitor',       self.system_monitor)
        # Production hardening
        self.runtime.register_route('GET',  '/api/prism/config/payment',       self.config_payment)
        self.runtime.register_route('GET',  '/api/prism/production',           self.production_status)
        # Sprint 3
        self.runtime.register_route('GET',  '/api/overview',                   self.operator_overview)
        # Workflow Designer routes
        self.runtime.register_route('GET',    '/api/prism/workflows',          self.wf_list)
        self.runtime.register_route('POST',   '/api/prism/workflows',          self.wf_save)
        self.runtime.register_route('DELETE', '/api/prism/workflows/{id}',     self.wf_delete)
        self.runtime.register_route('GET',    '/api/prism/workflow/palette',   self.wf_palette)
        # Backup routes
        self.runtime.register_route('GET',  '/api/prism/backups',              self.list_backups)
        self.runtime.register_route('POST', '/api/prism/backups/create',       self.create_backup)
        self.runtime.register_route('GET',  '/api/prism/backups/verify',       self.verify_backup)
        # Social campaign integration (R11)
        self.runtime.register_route('POST', '/api/prism/campaign/notify',      self.campaign_notify)
        self.runtime.register_route('GET',  '/api/prism/campaign/states',      self.campaign_states)
        # Sprint 3 — SENTINEL alert ingestion
        self.runtime.register_route('POST', '/api/prism/alert',                self.receive_alert)
        self.runtime.register_route('GET',  '/api/prism/alerts',               self.list_alerts)
        # Sprint 3 — Watchdog status
        self.runtime.register_route('GET',  '/api/watchdog/status',            self.watchdog_status)
        # License gate
        self.runtime.register_route('GET',  '/api/prism/license',              self.license_status)
        self.runtime.register_route('GET',  '/activate',                        self.license_activate_page)
        self.runtime.register_route('POST', '/api/prism/license/activate',      self.license_activate_api)
        # Sales Funnel trigger (Sprint 4)
        self.runtime.register_route('POST', '/api/prism/sales_funnel/run',               self.sales_funnel_run)
        self.runtime.register_route('GET',  '/api/prism/sales_funnel/run/{run_id}',      self.sales_funnel_run_status)
        self.runtime.register_route('POST', '/api/prism/sales_funnel/approve/{run_id}',  self.sales_funnel_approve)
        self.runtime.register_route('GET',  '/sales-funnel',                             self.serve_sales_funnel)
        # DEPOT one-button install/remove (Sprint 4 Task 7)
        self.runtime.register_route('POST', '/api/prism/depot/install',                  self.depot_install)
        self.runtime.register_route('POST', '/api/prism/depot/remove',                   self.depot_remove)
        # Billing sprint — Stripe webhook, billing portal, checkout, waitlist, notifications, tier
        self.runtime.register_route('POST', '/api/stripe/webhook',                       self.billing_stripe_webhook)
        self.runtime.register_route('GET',  '/api/prism/billing',                        self.get_billing_status)
        self.runtime.register_route('POST', '/api/prism/billing/portal',                 self.create_portal_session)
        self.runtime.register_route('POST', '/api/prism/billing/checkout',               self.create_checkout_session)
        self.runtime.register_route('POST', '/api/waitlist',                             self.handle_waitlist)
        self.runtime.register_route('GET',  '/api/waitlist/export',                      self.waitlist_export)
        self.runtime.register_route('POST', '/api/prism/notifications/register',         self.register_device_token)
        self.runtime.register_route('GET',  '/api/prism/tier',                           self.tier_status)

        # Start operator watchdog
        try:
            from cascadia.core.watchdog import OperatorWatchdog
            self._watchdog: Optional[Any] = OperatorWatchdog(self.config, self.runtime.logger)
        except Exception:
            self._watchdog = None

    # ------------------------------------------------------------------
    # Aggregated views
    # ------------------------------------------------------------------



    def hardware_info(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Detect hardware — RAM, GPU, arch. Called fresh every time from PRISM."""
        import platform as _pl, subprocess as _sp, shutil
        arch  = _pl.machine()
        ram_gb = 0
        gpu_type = 'unknown'
        chip = ''
        try:
            if _pl.system() == 'Darwin':
                ram_bytes = int(_sp.check_output(['sysctl','-n','hw.memsize'],
                                                  stderr=_sp.DEVNULL).strip())
                ram_gb = ram_bytes // (1024 ** 3)
                if arch == 'arm64':
                    gpu_type = 'apple_silicon'
                    try:
                        raw = _sp.check_output(
                            ['system_profiler','SPHardwareDataType'],
                            stderr=_sp.DEVNULL, text=True)
                        for line in raw.splitlines():
                            if 'Chip:' in line:
                                chip = line.split(':',1)[1].strip()
                                break
                    except Exception:
                        chip = 'Apple Silicon'
                else:
                    gpu_type = 'intel_mac'
                    try:
                        chip = _sp.check_output(
                            ['sysctl','-n','machdep.cpu.brand_string'],
                            stderr=_sp.DEVNULL, text=True).strip()
                    except Exception:
                        chip = 'Intel'
            elif _pl.system() == 'Linux':
                with open('/proc/meminfo') as f:
                    for line in f:
                        if line.startswith('MemTotal:'):
                            ram_gb = int(line.split()[1]) // (1024 * 1024)
                try:
                    _sp.run(['nvidia-smi'], capture_output=True, check=True, timeout=3)
                    gpu_type = 'nvidia'
                except Exception:
                    gpu_type = 'cpu_only'
        except Exception:
            pass

        # Ollama detection
        ollama_models: list = []
        try:
            import urllib.request as _ur
            with _ur.urlopen('http://localhost:11434/api/tags', timeout=2) as r:
                import json as _j
                ollama_models = [m['name'] for m in _j.loads(r.read()).get('models', [])]
        except Exception:
            pass

        # llama-server binary detection
        llama_bin = ''
        for candidate in ['/opt/homebrew/bin/llama-server',
                          '/usr/local/bin/llama-server',
                          str(Path.home() / 'llama.cpp/build/bin/llama-server')]:
            if Path(candidate).is_file():
                llama_bin = candidate
                break

        # Recommendation
        gpu_ok = gpu_type in ('apple_silicon', 'nvidia', 'amd')
        if gpu_ok and ram_gb >= 4:
            recommend = 'local'
            rec_model = '7b' if ram_gb >= 8 else '3b'
        else:
            recommend = 'api'
            rec_model = '3b'

        return 200, {
            'arch': arch, 'ram_gb': ram_gb, 'gpu_type': gpu_type,
            'chip': chip, 'platform': _pl.system(),
            'recommend': recommend, 'rec_model': rec_model,
            'llama_bin': llama_bin, 'llama_installed': bool(llama_bin),
            'ollama_models': ollama_models,
            'generated_at': _now(),
        }

    def get_settings(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Return current LLM config and models list for the Settings surface."""
        llm  = self.config.get('llm', {})
        mods = self.config.get('models', [])
        return 200, {
            'llm': llm,
            'models': mods,
            'sentinel_fail_open': self.config.get('sentinel_fail_open', False),
        }

    def save_settings(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Save LLM settings to config.json.
        If local mode chosen with a model that needs downloading,
        triggers setup-llm.sh in the background and returns immediately.
        """
        import subprocess as _sp, threading as _th, os as _os
        config_path = self.config.get('__config_path__', 'config.json')

        try:
            with open(config_path) as f:
                disk_config = json.load(f)
        except Exception as e:
            return 500, {'error': f'Could not read config: {e}'}

        provider   = payload.get('provider', 'llamacpp')
        model_file = payload.get('model', '')
        api_key    = payload.get('api_key', '')
        model_size = payload.get('model_size', '3b')

        disk_config.setdefault('llm', {})
        disk_config['llm']['provider']   = provider
        disk_config['llm']['model']      = model_file
        disk_config['llm']['configured'] = True

        if provider == 'llamacpp':
            disk_config['llm']['base_url']        = 'http://127.0.0.1:8080'
            disk_config['llm']['active_model_id'] = f'qwen2.5-{model_size}'
            # Detect and write llama_bin + models_dir so start.sh can find them
            import os as _os2
            install_dir = str(Path(config_path).parent)
            llama_bin = disk_config['llm'].get('llama_bin', '')
            if not llama_bin or not _os2.path.isfile(llama_bin):
                for candidate in [
                    '/opt/homebrew/bin/llama-server',
                    '/usr/local/bin/llama-server',
                    str(Path.home() / 'llama.cpp/build/bin/llama-server'),
                ]:
                    if _os2.path.isfile(candidate):
                        llama_bin = candidate
                        break
            disk_config['llm']['llama_bin']    = llama_bin
            disk_config['llm']['models_dir']   = disk_config['llm'].get('models_dir', './models')
            disk_config['llm']['n_gpu_layers']  = disk_config['llm'].get('n_gpu_layers', 99)
            disk_config['llm']['ctx_size']      = disk_config['llm'].get('ctx_size', 4096)
        elif provider in ('openai', 'anthropic', 'groq'):
            disk_config['llm']['api_key'] = api_key
            disk_config['llm']['base_url'] = None
        elif provider == 'ollama':
            disk_config['llm']['base_url'] = 'http://localhost:11434'

        try:
            with open(config_path, 'w') as f:
                json.dump(disk_config, f, indent=2)
            # Update in-memory config too
            self.config['llm'] = disk_config['llm']
        except Exception as e:
            return 500, {'error': f'Could not write config: {e}'}

        # If local provider — run setup-llm.sh then auto-start llama.cpp
        needs_setup = provider == 'llamacpp'
        if needs_setup:
            install_dir = str(Path(config_path).parent)
            def _run_setup():
                log_path = f'{install_dir}/data/logs/setup-llm.log'
                with open(log_path, 'a') as _log:
                    _sp.run(
                        ['bash', f'{install_dir}/setup-llm.sh', model_size],
                        cwd=install_dir,
                        stdin=_sp.DEVNULL,
                        stdout=_log,
                        stderr=_log,
                    )
                # After download completes, start llama.cpp automatically
                try:
                    import json as _json
                    with open(config_path) as _f:
                        _cfg = _json.load(_f)
                    _llm = _cfg.get('llm', {})
                    _bin  = _llm.get('llama_bin', '')
                    _model = _llm.get('model', '')
                    _models_dir = _llm.get('models_dir', f'{install_dir}/models')
                    _gpu = _llm.get('n_gpu_layers', 99)
                    _model_path = f'{_models_dir}/{_model}'
                    if _bin and _os.path.isfile(_bin) and _os.path.isfile(_model_path):
                        import urllib.parse as _up
                        _base = _llm.get('base_url', 'http://127.0.0.1:8080')
                        _port = str(_up.urlparse(_base).port or 8080)
                        _ctx  = str(_llm.get('ctx_size', 4096))
                        # Kill any stale instance first
                        _sp.run(['pkill', '-f', 'llama-server'], capture_output=True)
                        import time as _time; _time.sleep(1)
                        _sp.Popen(
                            [_bin, '--model', _model_path,
                             '--host', '127.0.0.1', '--port', _port,
                             '--ctx-size', _ctx,
                             '--n-gpu-layers', str(_gpu)],
                            cwd=install_dir,
                            stdout=open(f'{install_dir}/data/logs/llamacpp.log', 'a'),
                            stderr=_sp.STDOUT,
                        )
                except Exception as _e:
                    pass  # best-effort — user can run start.sh manually
            _th.Thread(target=_run_setup, daemon=True).start()

        self.runtime.logger.info(
            'PRISM settings saved: provider=%s model=%s', provider, model_file
        )
        return 200, {
            'ok': True,
            'provider': provider,
            'model': model_file,
            'setup_running': needs_setup,
        }

    def setup_progress(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Return current setup-llm.sh progress by checking what exists on disk.
        Used by PRISM Settings/Health surfaces to show live install status.
        """
        import os as _os
        llm = self.config.get('llm', {})
        models_dir_raw = llm.get('models_dir', './models')
        config_path = self.config.get('__config_path__', 'config.json')
        if models_dir_raw.startswith('.'):
            models_dir = str(Path(config_path).parent / models_dir_raw)
        else:
            models_dir = _os.path.expanduser(models_dir_raw)

        model_file  = llm.get('model', '')
        model_path  = _os.path.join(models_dir, model_file) if model_file else ''
        model_exists = bool(model_path and _os.path.isfile(model_path))
        model_size_gb = round(_os.path.getsize(model_path) / 1e9, 1) if model_exists else 0

        llama_bin = llm.get('llama_bin', '')
        llama_ok  = bool(llama_bin and _os.path.isfile(llama_bin))
        if not llama_ok:
            for c in ['/opt/homebrew/bin/llama-server', '/usr/local/bin/llama-server']:
                if _os.path.isfile(c):
                    llama_ok = True
                    break

        # Check if llama-server is responding
        llm_live = False
        try:
            import urllib.request as _ur
            base = llm.get('base_url', 'http://127.0.0.1:8080')
            with _ur.urlopen(f'{base}/health', timeout=2) as r:
                llm_live = r.status == 200
        except Exception:
            pass

        configured = llm.get('configured', False)
        provider   = llm.get('provider')

        return 200, {
            'llama_installed': llama_ok,
            'model_downloaded': model_exists,
            'model_file': model_file,
            'model_size_gb': model_size_gb,
            'model_path': model_path,
            'llm_responding': llm_live,
            'configured': configured,
            'provider': provider,
            'ready': configured and (llm_live or provider not in ('llamacpp',)),
        }

    def serve_setup(self, _) -> tuple[int, Dict[str, Any]]:
        """Serve the post-install health check page."""
        html = (Path(__file__).parent / "setup-complete.html").read_bytes()
        return 200, {"__html__": html}

    def almanac_query(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Proxy natural-language queries to the ALMANAC service.
        Converts a {query} payload to ALMANAC's /search endpoint and
        returns a formatted answer. Same-origin proxy — no CORS issues.
        """
        import urllib.request as _ur
        import urllib.error as _ue

        query = payload.get('query', '').strip()
        if not query:
            return 400, {'error': 'query required'}

        almanac_port = self._ports.get('almanac', 6205)

        try:
            import json as _json
            body = _json.dumps({'query': query}).encode('utf-8')
            req = _ur.Request(
                f'http://127.0.0.1:{almanac_port}/search',
                data=body,
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            with _ur.urlopen(req, timeout=10) as r:
                data = _json.loads(r.read())

            results = data.get('results', [])
            if not results:
                return 200, {
                    'answer': f'No results found for "{query}". Try asking about a specific component (VAULT, BEACON, SENTINEL...) or a term like "capability", "run", or "approval".',
                    'query': query,
                    'results': [],
                }

            # Format results into a readable answer
            lines = []
            for r in results[:5]:
                if r['type'] == 'component':
                    lines.append(f"<strong>{r['name']}</strong>: {r['match']}")
                elif r['type'] == 'glossary':
                    lines.append(f"<strong>{r['term']}</strong>: {r['match']}")
                elif r['type'] == 'runbook':
                    lines.append(f"📋 <strong>Runbook: {r['title']}</strong>")

            answer = '<br>'.join(lines)
            if data.get('count', 0) > 5:
                answer += f'<br><em>…and {data["count"] - 5} more results</em>'

            return 200, {'answer': answer, 'query': query, 'results': results}

        except _ue.URLError as e:
            return 502, {'error': f'ALMANAC not reachable: {e}',
                         'answer': 'ALMANAC service is not responding. Make sure Cascadia is running: bash start.sh'}
        except Exception as e:
            return 500, {'error': str(e), 'answer': f'Error querying ALMANAC: {e}'}

    def full_health_check(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Full system health check for the setup-complete page.
        Checks every component including llama.cpp, model file, and SwiftBar.
        """
        import os, subprocess
        results = {}

        # ── Infrastructure checks ─────────────────────────────────────────────
        # Python version
        import sys
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
        py_ok = sys.version_info >= (3, 11)
        results['python'] = {
            'label': f'Python {py_ver}',
            'status': 'ok' if py_ok else 'error',
            'detail': f'Version {py_ver} — {"OK" if py_ok else "3.11+ required"}',
            'group': 'Infrastructure',
        }

        # llama.cpp binary
        llm_cfg = self.config.get('llm', {})
        llama_bin = llm_cfg.get('llama_bin', '')
        llama_candidates = [
            llama_bin,
            '/opt/homebrew/bin/llama-server',
            '/usr/local/bin/llama-server',
            os.path.expanduser('~/llama.cpp/build/bin/llama-server'),
        ]
        llama_found = next((b for b in llama_candidates if b and os.path.isfile(b)), None)
        results['llama_cpp'] = {
            'label': 'llama.cpp',
            'status': 'ok' if llama_found else 'error',
            'detail': llama_found or 'Not found — run: bash setup-llm.sh',
            'group': 'Infrastructure',
        }

        # AI model file
        models_dir = llm_cfg.get('models_dir', './models')
        if models_dir.startswith('.'):
            models_dir = str(Path(self.config.get('__config_path__', '.')).parent / models_dir)
        models_dir = os.path.expanduser(models_dir)
        model_file = llm_cfg.get('model', '')
        model_path = os.path.join(models_dir, model_file) if model_file else ''
        model_exists = bool(model_path and os.path.isfile(model_path))
        model_size = f"{os.path.getsize(model_path) / 1e9:.1f} GB" if model_exists else ''
        results['ai_model'] = {
            'label': f'AI Model ({model_file or "not configured"})',
            'status': 'ok' if model_exists else ('warning' if not model_file else 'error'),
            'detail': f'{model_path} — {model_size}' if model_exists else
                      ('No model configured — run: bash setup-llm.sh' if not model_file else
                       f'File not found: {model_path}'),
            'group': 'Infrastructure',
        }

        # llama.cpp server responding
        llm_base = llm_cfg.get('base_url', 'http://127.0.0.1:8080')
        llm_ok = False
        llm_detail = f'Not running at {llm_base}'
        try:
            import urllib.request
            with urllib.request.urlopen(f'{llm_base}/health', timeout=2) as r:
                llm_ok = r.status == 200
                llm_detail = f'Responding at {llm_base}'
        except Exception as e:
            llm_provider = llm_cfg.get('provider', '')
            if llm_provider == 'llamacpp':
                llm_detail = f'Not running — start with: bash start.sh'
            else:
                llm_ok = True  # API mode — no local server needed
                llm_detail = f'Cloud API mode ({llm_provider}) — no local server needed'
        results['ai_server'] = {
            'label': 'AI Inference',
            'status': 'ok' if llm_ok else 'warning',
            'detail': llm_detail,
            'group': 'Infrastructure',
        }

        # SwiftBar
        swiftbar_plugin = os.path.expanduser(
            '~/Library/Application Support/SwiftBar/Plugins/cascadia.5s.sh'
        )
        swiftbar_app = (
            os.path.isdir('/Applications/SwiftBar.app') or
            os.path.isdir(os.path.expanduser('~/Applications/SwiftBar.app'))
        )
        swiftbar_linked = os.path.islink(swiftbar_plugin) or os.path.isfile(swiftbar_plugin)
        results['swiftbar'] = {
            'label': 'SwiftBar Menu Bar',
            'status': 'ok' if (swiftbar_app and swiftbar_linked) else
                      'warning' if swiftbar_app else 'warning',
            'detail': ('Installed and linked — menu bar active' if swiftbar_app and swiftbar_linked
                       else 'Plugin not linked — run: bash flint-link.sh' if swiftbar_app
                       else 'Not installed — install with: brew install swiftbar'),
            'group': 'Infrastructure',
        }

        # ── Cascadia components — port list built from config ─────────────────
        _groups = {
            'crew': 'Foundation', 'vault': 'Foundation',
            'sentinel': 'Foundation', 'curtain': 'Foundation',
            'license_gate': 'Foundation',
            'beacon': 'Runtime', 'stitch': 'Runtime', 'vanguard': 'Runtime',
            'handshake': 'Runtime', 'bell': 'Runtime', 'almanac': 'Runtime',
            'prism': 'Dashboard',
        }
        COMPONENTS = (
            [('flint', self._flint_port, 'Kernel'),
             ('license_gate', 6100, 'Foundation')] +
            [(c['name'], c['port'], _groups.get(c['name'], 'Runtime'))
             for c in self.config.get('components', [])]
        )
        # ── Operator agents — loaded from registry.json ──────────────────────
        _configured_reg = self.config.get('operators_registry_path', '')
        _reg_path = (Path(_configured_reg).expanduser() if _configured_reg
                     else Path(__file__).parent.parent / 'operators' / 'registry.json')
        try:
            registry = json.loads(_reg_path.read_text())
            for op in registry.get('operators', []):
                port = op.get('port')
                if not port:
                    continue
                health_path = op.get('health_path', '/api/health')
                op_status = 'error'
                op_detail = f'Port {port} — not running'
                try:
                    import urllib.request as _ur2
                    with _ur2.urlopen(
                        f'http://127.0.0.1:{port}{health_path}', timeout=1
                    ) as r:
                        op_status = 'ok'
                        op_detail = f'Port {port} — online'
                except Exception:
                    pass
                results[op['id']] = {
                    'label': op.get('name', op['id']),
                    'status': op_status,
                    'detail': op_detail,
                    'group': 'Operators',
                    'port': port,
                }
        except Exception:
            pass

        import urllib.request as _ur
        for name, port, group in COMPONENTS:
            try:
                with _ur.urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as r:
                    data = json.loads(r.read().decode())
                    ok = data.get('ok', True)
                    results[name] = {
                        'label': name.upper(),
                        'status': 'ok' if ok else 'error',
                        'detail': f'Port {port} — ready',
                        'group': group,
                        'port': port,
                    }
            except Exception:
                results[name] = {
                    'label': name.upper(),
                    'status': 'error',
                    'detail': f'Port {port} — not responding',
                    'group': group,
                    'port': port,
                }

        total   = len(results)
        ok_count = sum(1 for r in results.values() if r['status'] == 'ok')
        warn_count = sum(1 for r in results.values() if r['status'] == 'warning')
        all_critical_ok = all(
            results.get(k, {}).get('status') == 'ok'
            for k in ['python', 'flint', 'prism', 'crew', 'vault']
        )
        # Operators offline is a warning not a blocker — kernel being ready is what matters

        return 200, {
            'checks': results,
            'summary': {
                'total': total,
                'ok': ok_count,
                'warnings': warn_count,
                'errors': total - ok_count - warn_count,
                'all_critical_ok': all_critical_ok,
                'ready': all_critical_ok,
            },
            'generated_at': _now(),
        }

    def serve_ui(self, _):
        html = (Path(__file__).parent / "prism.html").read_bytes()
        return 200, {"__html__": html}

    def overview(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        One-call system snapshot. Everything a non-technical user needs
        to understand what Cascadia OS is doing right now.
        """
        flint = _http_get(self._flint_port, '/api/flint/status') or {}
        crew = _http_get(self._ports.get('crew', 0), '/crew') or {}
        runs = self._get_runs_summary()
        approvals = self._get_pending_approvals()
        blocked = self._get_blocked_runs()

        component_states = {
            c['name']: c.get('process_state', 'unknown')
            for c in flint.get('components', [])
        }
        healthy_count = sum(1 for s in component_states.values() if s == 'ready')
        total_count = len(component_states)

        avg_rt = self._get_avg_response_time()
        try:
            from cascadia.hardware.system_monitor import SystemMonitor
            hw = SystemMonitor().snapshot()
        except Exception:
            hw = {'available': False}
        return 200, {
            'cascadia_os': 'v0.44',
            'generated_at': _now(),
            'system': {
                'flint_state': flint.get('state', 'unknown'),
                'components_healthy': f'{healthy_count}/{total_count}',
                'component_states': component_states,
            },
            'hardware': hw,
            'crew': {
                'operator_count': crew.get('crew_size', 0),
                'operators': list(crew.get('operators', {}).keys()),
            },
            'runs': runs,
            'avg_response_time_minutes': avg_rt,
            'attention_required': {
                'pending_approvals': len(approvals),
                'blocked_runs': len(blocked),
                'approvals': approvals[:5],   # Show first 5
                'blocked': blocked[:5],
            },
        }

    def system_status(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Full FLINT component status. Includes process_state, health, restart counts, LLM health."""
        flint = _http_get(self._flint_port, '/api/flint/status') or {}
        sentinel = _http_get(self._ports.get('sentinel', 0), '/risk-levels') or {}
        return 200, {
            'flint': flint,
            'llm': flint.get('llm', {'ok': None, 'latency_ms': None, 'error': None, 'checked_at': None}),
            'sentinel_rules_loaded': 'risk_levels' in sentinel,
            'generated_at': _now(),
        }

    def crew_status(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Who is in the Crew and what capabilities they have."""
        crew = _http_get(self._ports.get('crew', 0), '/crew') or {}
        return 200, {
            'crew_size': crew.get('crew_size', 0),
            'operators': crew.get('operators', {}),
            'generated_at': _now(),
        }

    def run_summary(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Summary of recent runs. Readable by a non-technical user."""
        runs = self._get_runs_summary()
        return 200, {'runs': runs, 'generated_at': _now()}

    def run_detail(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Full detail for one run: current step, last failure,
        committed side effects, approval state.
        Reads directly from the durability layer.
        """
        run_id = payload.get('run_id', '')
        if not run_id:
            return 400, {'error': 'run_id required'}

        # Query run_store directly for detailed state
        try:
            from cascadia.durability.run_store import RunStore
            from cascadia.durability.step_journal import StepJournal
            from cascadia.durability.idempotency import IdempotencyManager

            store = RunStore(self.config['database_path'])
            run = store.get_run(run_id)
            if run is None:
                return 404, {'error': 'run not found'}

            journal = StepJournal(store)
            steps = journal.list_steps(run_id)
            idem = IdempotencyManager(store)

            # Get side effects for all steps
            all_effects = []
            for step in steps:
                effects = idem.all_for_step(run_id, step['step_index'])
                all_effects.extend(effects)

            committed = [e for e in all_effects if e['status'] == 'committed']
            pending_approvals = store.pending_approvals(run_id)

            last_completed = next(
                (s for s in reversed(steps) if s.get('completed_at') and not s.get('failure_reason')),
                None,
            )
            last_failed = next(
                (s for s in reversed(steps) if s.get('failure_reason')),
                None,
            )

            return 200, {
                'run_id': run_id,
                'goal': run.get('goal'),
                'run_state': run.get('run_state'),
                'process_state': run.get('process_state'),
                'current_step': run.get('current_step'),
                'retry_count': run.get('retry_count', 0),
                'blocked_reason': run.get('blocked_reason'),
                'blocking_entity': run.get('blocking_entity'),
                'dependency_request': run.get('dependency_request'),
                'last_completed_step': last_completed['step_name'] if last_completed else None,
                'last_failed_step': last_failed['step_name'] if last_failed else None,
                'last_failure_reason': last_failed['failure_reason'] if last_failed else None,
                'committed_side_effects': len(committed),
                'side_effects': [
                    {'action': e['effect_type'], 'target': e['target'], 'status': e['status']}
                    for e in all_effects
                ],
                'pending_approvals': len(pending_approvals),
                'steps_completed': len([s for s in steps if s.get('completed_at') and not s.get('failure_reason')]),
                'total_steps_recorded': len(steps),
            }
        except Exception as exc:
            return 500, {'error': str(exc)}

    def pending_approvals(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """All runs waiting for a human decision. Primary BELL integration point."""
        approvals = self._get_pending_approvals()
        return 200, {
            'count': len(approvals),
            'approvals': approvals,
            'generated_at': _now(),
        }

    def blocked_runs(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """All runs blocked on a missing dependency or permission."""
        blocked = self._get_blocked_runs()
        return 200, {
            'count': len(blocked),
            'blocked': blocked,
            'generated_at': _now(),
        }

    def workflow_list(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Available STITCH workflows."""
        stitch = _http_get(self._ports.get('stitch', 0), '/workflow/list') or {}
        return 200, {
            'workflows': stitch.get('workflows', []),
            'count': stitch.get('count', 0),
            'generated_at': _now(),
        }

    def sentinel_status(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """SENTINEL risk levels and compliance rules."""
        sentinel = _http_get(self._ports.get('sentinel', 0), '/risk-levels') or {}
        return 200, {**sentinel, 'generated_at': _now()}

    def license_status(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Proxy to license_gate — returns current tier and validity."""
        status = _http_get(6100, '/api/license/status') or {
            'valid': False, 'tier': 'lite', 'operator_limit': 2, 'expires': None,
            'error': 'license_gate not running',
        }
        return 200, {**status, 'generated_at': _now()}

    def license_activate_page(self, _: Dict[str, Any]) -> tuple[int, str]:
        """Serve the license activation HTML page."""
        html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Activate Cascadia OS License</title>
<style>
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0d1117;color:#e6edf3;
       display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
  .card{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:40px;max-width:480px;width:100%}
  h1{margin:0 0 8px;font-size:1.4rem;font-weight:600}
  p{color:#8b949e;margin:0 0 24px;font-size:.9rem}
  label{display:block;font-size:.85rem;font-weight:500;margin-bottom:6px;color:#8b949e}
  input{width:100%;box-sizing:border-box;background:#0d1117;border:1px solid #30363d;border-radius:6px;
        padding:10px 12px;color:#e6edf3;font-size:.9rem;font-family:monospace;outline:none}
  input:focus{border-color:#58a6ff}
  button{margin-top:16px;width:100%;background:#238636;border:none;border-radius:6px;padding:10px;
         color:#fff;font-size:.9rem;font-weight:600;cursor:pointer}
  button:hover{background:#2ea043}
  #msg{margin-top:16px;padding:10px 14px;border-radius:6px;display:none;font-size:.875rem}
  .ok{background:#0d1117;border:1px solid #238636;color:#3fb950}
  .err{background:#0d1117;border:1px solid #f85149;color:#f85149}
</style>
</head>
<body>
<div class="card">
  <h1>Activate your license</h1>
  <p>Enter the license key from your welcome email to unlock your tier.</p>
  <label for="key">License key</label>
  <input id="key" type="text" placeholder="zyrcon_pro_..." autocomplete="off" spellcheck="false">
  <button onclick="activate()">Activate</button>
  <div id="msg"></div>
</div>
<script>
async function activate() {
  var key = document.getElementById('key').value.trim();
  var msg = document.getElementById('msg');
  if (!key) { show(msg, 'Please enter a license key.', false); return; }
  try {
    var r = await fetch('/api/prism/license/activate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({license_key: key})
    });
    var d = await r.json();
    if (r.ok && d.ok) {
      show(msg, 'License activated! Tier: ' + (d.tier || 'unknown') + '. Restarting dashboard...', true);
      setTimeout(function(){ window.location.href = '/'; }, 2500);
    } else {
      show(msg, d.error || 'Activation failed.', false);
    }
  } catch(e) { show(msg, 'Request failed: ' + e.message, false); }
}
function show(el, text, ok) {
  el.textContent = text; el.className = ok ? 'ok' : 'err'; el.style.display = 'block';
}
document.getElementById('key').addEventListener('keydown', function(e){
  if (e.key === 'Enter') activate();
});
</script>
</body>
</html>"""
        return 200, html

    def license_activate_api(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /api/prism/license/activate — validate and save a license key to config."""
        key = (payload.get('license_key') or '').strip()
        if not key:
            return 400, {'error': 'license_key required'}
        try:
            from cascadia.licensing.tier_validator import TierValidator
            secret = self.config.get('license_secret', '')
            if secret:
                validator = TierValidator(secret)
                result = validator.validate(key)
                if not result.get('valid'):
                    return 400, {'error': result.get('error', 'invalid_license')}
                tier = result['tier']
            else:
                # No HMAC secret — accept any well-formed key (license_gate handles format check)
                from cascadia.licensing.license_gate import _build_status
                status = _build_status(key)
                if not status.get('valid'):
                    return 400, {'error': 'invalid_license_format'}
                tier = status['tier']
        except Exception as exc:
            return 500, {'error': f'validation error: {exc}'}
        # Persist to config.json
        import json as _json
        from pathlib import Path as _Path
        config_path = _Path(__file__).parents[2] / 'config.json'
        try:
            cfg = _json.loads(config_path.read_text())
            cfg['license_key'] = key
            config_path.write_text(_json.dumps(cfg, indent=2))
            self.config['license_key'] = key
        except Exception as exc:
            return 500, {'error': f'could not save config: {exc}'}
        return 200, {'ok': True, 'tier': tier, 'key_prefix': key[:24] + '...'}

    def operator_status(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Live status of all registered operators from registry.json.

        Checks config operators_registry_path first; falls back to the
        inline cascadia/operators/registry.json in the install directory.
        """
        import urllib.request as _ur
        configured = self.config.get('operators_registry_path', '')
        if configured:
            registry_path = Path(configured).expanduser()
        else:
            registry_path = Path(__file__).parent.parent / "operators" / "registry.json"
        try:
            registry = json.loads(registry_path.read_text())
            operators = registry.get("operators", [])
        except Exception:
            operators = []

        result = []
        for op in operators:
            port = op.get("port")
            health_path = op.get("health_path", "/api/health")
            status = "offline"
            detail = {}
            if port:
                try:
                    with _ur.urlopen(
                        f"http://127.0.0.1:{port}{health_path}", timeout=1
                    ) as r:
                        detail = json.loads(r.read().decode())
                        status = detail.get("status", "online")
                except Exception:
                    status = "offline"
            result.append({
                "id":          op.get("id"),
                "name":        op.get("name"),
                "category":    op.get("category"),
                "description": op.get("description"),
                "status":      status,
                "port":        port,
                "autonomy":    op.get("autonomy"),
                "op_status":   op.get("status"),  # production/beta
                "ui_url":      f"http://localhost:{port}/" if port else None,
                "sample_output": op.get("sample_output"),
            })

        online = sum(1 for o in result if o["status"] != "offline")
        return 200, {
            "operators": result,
            "total": len(result),
            "online": online,
            "generated_at": _now(),
        }



    def models_list(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Return model list from config.
        PRISM reads models directly from config.json so the dashboard
        always reflects what is actually configured — no hardcoding.
        """
        models = self.config.get('models', [])
        llm = self.config.get('llm', {})
        active_id = llm.get('active_model_id', '')

        # If no models in config, return a sensible default
        if not models:
            models = [{
                'id': 'default',
                'name': llm.get('model', 'Local Model'),
                'file': llm.get('model', ''),
                'alias': llm.get('model', ''),
                'desc': 'Configured model · Local',
                'size': '—',
                'context': 4096,
                'recommended_for': 'all tasks',
            }]

        return 200, {
            'models': models,
            'active_model_id': active_id or (models[0]['id'] if models else ''),
            'llm_base_url': llm.get('base_url', 'http://127.0.0.1:8080'),
            'llm_provider': llm.get('provider', 'llamacpp'),
            # FLINT proxy is always available for chat — normalises model names
            'flint_proxy_url': f'http://127.0.0.1:{self._flint_port}',
            'count': len(models),
            'generated_at': _now(),
        }

    def approve_action(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Record an approval decision from PRISM UI and resume the workflow run.
        Called by the Approve / Reject buttons in the live approvals surface.
        """
        approval_id = payload.get('approval_id')
        decision    = payload.get('decision', '')
        actor       = payload.get('actor', 'prism_operator')
        reason      = payload.get('reason', '')
        run_id      = payload.get('run_id', '')

        if decision not in ('approved', 'denied'):
            return 400, {'error': 'decision must be approved or denied'}
        if approval_id is None:
            return 400, {'error': 'approval_id required'}

        try:
            from cascadia.durability.run_store import RunStore
            from cascadia.system.approval_store import ApprovalStore
            from cascadia.automation.workflow_runtime import WorkflowRuntime
            from cascadia.automation.stitch import WorkflowDefinition, WorkflowStep

            store     = RunStore(self.config['database_path'])
            approvals = ApprovalStore(store)

            # 1. Record the decision — wakes run to 'retrying' if approved
            approvals.record_decision(int(approval_id), decision, actor, reason)

            # 2. If approved, find run_id from approval record and resume
            resume_result: Optional[Dict[str, Any]] = None
            if decision == 'approved':
                if not run_id:
                    with store.connection() as conn:
                        row = conn.execute(
                            'SELECT run_id FROM approvals WHERE id = ?', (approval_id,)
                        ).fetchone()
                    run_id = row['run_id'] if row else ''

                if run_id:
                    definition = WorkflowDefinition(
                        'lead_follow_up', 'Lead Follow-Up', [
                            WorkflowStep('parse_lead',     'main_operator',  'parse_lead'),
                            WorkflowStep('enrich_company', 'main_operator',  'enrich_company'),
                            WorkflowStep('draft_email',    'main_operator',  'draft_email'),
                            WorkflowStep('send_email',     'gmail_operator', 'email.send', on_failure='stop'),
                            WorkflowStep('log_crm',        'main_operator',  'crm.write'),
                        ],
                    )
                    runtime = WorkflowRuntime(self.config['database_path'])
                    result  = runtime.execute('lead_follow_up', definition, {'run_id': run_id})
                    resume_result = result.to_dict()

            return 200, {
                'approval_id': approval_id,
                'decision':    decision,
                'recorded':    True,
                'run_id':      run_id,
                'resume_result': resume_result,
                'generated_at': _now(),
            }
        except Exception as exc:
            return 500, {'error': str(exc)}

    # ------------------------------------------------------------------
    # Internal helpers — query durability layer directly
    # ------------------------------------------------------------------

    def _get_avg_response_time(self) -> Optional[float]:
        try:
            from cascadia.durability.run_store import RunStore
            store = RunStore(self.config['database_path'])
            return store.avg_response_time_minutes()
        except Exception:
            return None

    def scheduler_status(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Proxy to STITCH /scheduler/jobs."""
        stitch = _http_get(self._ports.get('stitch', 0), '/scheduler/jobs') or {}
        return 200, {
            'jobs': stitch.get('jobs', []),
            'generated_at': _now(),
        }

    def record_run_outcome(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Record win/loss outcome: {run_id, outcome: 'won'|'lost'|'no_decision'}."""
        run_id = payload.get('run_id', '')
        outcome = payload.get('outcome', '')
        if not run_id:
            return 400, {'error': 'run_id required'}
        if outcome not in ('won', 'lost', 'no_decision'):
            return 400, {'error': 'outcome must be won | lost | no_decision'}
        try:
            from cascadia.durability.run_store import RunStore
            store = RunStore(self.config['database_path'])
            store.record_outcome(run_id, outcome, _now())
            return 200, {'run_id': run_id, 'outcome': outcome, 'recorded_at': _now()}
        except Exception as exc:
            return 500, {'error': str(exc)}

    def pairing_code(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Generate a 6-digit pairing code for iOS companion app."""
        remote = payload.get('__remote_addr__', '')
        if not self._rate_limiter.check(f'pair:{remote}', limit=10, window=60):
            return 429, {'error': 'rate limit exceeded'}
        try:
            from cascadia.network.discovery import generate_pairing_code
            code = generate_pairing_code()
            return 200, {'code': code, 'ttl_seconds': 300, 'generated_at': _now()}
        except Exception as exc:
            return 500, {'error': str(exc)}

    def pairing_validate(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Validate a pairing code from the iOS companion app."""
        remote = payload.get('__remote_addr__', '')
        if not self._rate_limiter.check(f'pair:{remote}', limit=10, window=60):
            return 429, {'error': 'rate limit exceeded'}
        code = payload.get('code', '')
        if not code:
            return 400, {'error': 'code required'}
        try:
            from cascadia.network.discovery import validate_pairing_code
            valid = validate_pairing_code(code)
            if valid:
                return 200, {'valid': True, 'message': 'Pairing successful'}
            return 401, {'valid': False, 'error': 'Invalid, expired, or already-used code'}
        except Exception as exc:
            return 500, {'error': str(exc)}

    def pairing_status(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """mDNS and pairing status."""
        try:
            from cascadia.network.discovery import pairing_status
            return 200, pairing_status()
        except Exception as exc:
            return 500, {'error': str(exc)}

    def leads_recover(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Missed lead recovery: score a list of leads via CHIEF and return enriched results.
        Expects {leads: [{name, company, notes}]}
        """
        leads = payload.get('leads', [])
        if not leads or not isinstance(leads, list):
            return 400, {'error': 'leads array required'}
        chief_port = self._ports.get('chief', 8006)
        scored = []
        for lead in leads[:50]:  # cap at 50 per call
            score_result = _http_post(chief_port, '/api/score', {
                'name': lead.get('name', ''),
                'company': lead.get('company', ''),
                'notes': lead.get('notes', ''),
            }) or {}
            scored.append({
                **lead,
                'score': score_result.get('score'),
                'priority': score_result.get('priority', 'unknown'),
                'notes_ai': score_result.get('notes', ''),
            })
        scored.sort(key=lambda x: -(x.get('score') or 0))
        return 200, {
            'leads': scored,
            'count': len(scored),
            'generated_at': _now(),
        }

    def _get_runs_summary(self) -> List[Dict[str, Any]]:
        try:
            from cascadia.durability.run_store import RunStore
            store = RunStore(self.config['database_path'])
            with store.connection() as conn:
                rows = conn.execute(
                    'SELECT run_id, goal, run_state, current_step, retry_count, '
                    'blocked_reason, blocking_entity, created_at, updated_at '
                    'FROM runs ORDER BY updated_at DESC LIMIT 20'
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _get_pending_approvals(self) -> List[Dict[str, Any]]:
        try:
            from cascadia.durability.run_store import RunStore
            store = RunStore(self.config['database_path'])
            with store.connection() as conn:
                rows = conn.execute(
                    'SELECT a.id, a.run_id, a.step_index, a.action_key, '
                    'a.created_at, a.risk_level, '
                    'r.goal, r.operator_id, r.state_snapshot '
                    'FROM approvals a '
                    'JOIN runs r ON a.run_id = r.run_id '
                    "WHERE a.decision = 'pending' "
                    'ORDER BY a.created_at ASC'
                ).fetchall()
            results = []
            for row in rows:
                d = dict(row)
                # Extract key fields from state_snapshot so the UI can show
                # what data the operator was working with at decision time
                snap_raw = d.pop('state_snapshot', None)
                data_used: Dict[str, Any] = {}
                if snap_raw:
                    try:
                        snap = json.loads(snap_raw)
                        for key in ('lead_name', 'name', 'email', 'company',
                                    'subject', 'to', 'phone', 'amount',
                                    'message', 'file_path', 'url'):
                            if key in snap:
                                data_used[key] = str(snap[key])[:100]
                    except Exception:
                        pass
                d['data_used'] = data_used
                results.append(d)
            return results
        except Exception:
            return []

    def _get_blocked_runs(self) -> List[Dict[str, Any]]:
        try:
            from cascadia.durability.run_store import RunStore
            store = RunStore(self.config['database_path'])
            with store.connection() as conn:
                rows = conn.execute(
                    'SELECT run_id, goal, blocked_reason, blocking_entity, '
                    'dependency_request, updated_at '
                    'FROM runs '
                    "WHERE run_state = 'blocked' "
                    'ORDER BY updated_at DESC'
                ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                if d.get('dependency_request'):
                    try:
                        d['dependency_request'] = json.loads(d['dependency_request'])
                    except Exception:
                        pass
                result.append(d)
            return result
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Sprint v2 handlers
    # ------------------------------------------------------------------

    def stripe_webhook(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Process Stripe webhook events — activate/deactivate licenses."""
        remote = payload.get('__remote_addr__', '')
        if not self._rate_limiter.check(f'webhook:{remote}', limit=20, window=60):
            return 429, {'error': 'rate limit exceeded'}
        try:
            from cascadia.billing.stripe_handler import StripeHandler
            from cascadia.billing.license_generator import LicenseGenerator
        except ImportError as exc:
            return 503, {'error': f'billing module unavailable: {exc}'}

        stripe_cfg = self.config.get('stripe', {})
        secret = stripe_cfg.get('webhook_secret', '')
        if not secret:
            return 503, {'error': 'stripe.webhook_secret not configured'}

        handler = StripeHandler(secret)
        event = handler.process_event(payload)
        if event is None:
            return 200, {'status': 'ignored'}

        action = event.get('action')
        if action == 'activate':
            gen = LicenseGenerator()
            gen.activate(
                customer_email=event.get('customer_email', ''),
                customer_id=event.get('customer_id', ''),
                tier=event.get('tier', 'solo'),
            )
            return 200, {'status': 'activated', 'tier': event.get('tier')}
        if action == 'deactivate':
            return 200, {'status': 'deactivated', 'customer_id': event.get('customer_id')}
        return 200, {'status': 'processed'}

    def approve_edit(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Approve with owner edits — stores edited_content, wakes the run."""
        approval_id = payload.get('approval_id')
        content     = payload.get('content', '')
        summary     = payload.get('summary', '')
        actor       = payload.get('actor', 'prism_operator')
        if approval_id is None:
            return 400, {'error': 'approval_id required'}
        if not content:
            return 400, {'error': 'content required'}
        try:
            from cascadia.durability.run_store import RunStore
            from cascadia.system.approval_store import ApprovalStore
            store     = RunStore(self.config['database_path'])
            approvals = ApprovalStore(store)
            approvals.edit_and_approve(int(approval_id), actor, content, summary)
            return 200, {'approval_id': approval_id, 'decision': 'approved', 'edited': True}
        except Exception as exc:
            return 500, {'error': str(exc)}

    def approval_analytics(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Approval gate intelligence — totals, timing, risk breakdown."""
        try:
            from cascadia.durability.run_store import RunStore
            store = RunStore(self.config['database_path'])
            analytics = store.approval_analytics()
            return 200, {**analytics, 'generated_at': _now()}
        except Exception as exc:
            return 500, {'error': str(exc)}

    def approval_outcomes(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Recently resolved approvals for the Outcomes dashboard (last 50)."""
        try:
            from cascadia.durability.run_store import RunStore
            store = RunStore(self.config['database_path'])
            with store.connection() as conn:
                rows = conn.execute(
                    'SELECT a.id, a.run_id, a.step_index, a.action_key, '
                    'a.decision, a.actor, a.reason, a.created_at, a.decided_at, '
                    'a.risk_level, a.edited_content, a.edit_summary, '
                    'r.goal, r.operator_id '
                    'FROM approvals a '
                    'JOIN runs r ON a.run_id = r.run_id '
                    "WHERE a.decision != 'pending' "
                    'ORDER BY a.decided_at DESC '
                    'LIMIT 50'
                ).fetchall()
            return 200, {
                'count': len(rows),
                'outcomes': [dict(r) for r in rows],
                'generated_at': _now(),
            }
        except Exception as exc:
            return 500, {'error': str(exc)}

    def audit_log(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Return recent audit log entries."""
        try:
            from cascadia.system.audit_log import AuditLog
            log = AuditLog()
            return 200, {'events': log.query(days=30), 'generated_at': _now()}
        except Exception as exc:
            return 500, {'error': str(exc)}

    def audit_export(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Export audit log as CSV string. Requires pro tier or above."""
        check = self._public_tier_check('pro')
        if check: return check
        try:
            from cascadia.system.audit_log import AuditLog
            log = AuditLog()
            return 200, {'csv': log.export_csv(days=30), 'generated_at': _now()}
        except Exception as exc:
            return 500, {'error': str(exc)}

    def audit_verify(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Verify the audit log hash chain integrity. Requires pro tier or above."""
        check = self._public_tier_check('pro')
        if check: return check
        try:
            from cascadia.system.audit_log import AuditLog
            log = AuditLog()
            ok = log.verify_chain()
            return 200, {'ok': ok, 'generated_at': _now()}
        except Exception as exc:
            return 500, {'error': str(exc)}

    def fleet_status(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """All fleet nodes and their current health."""
        try:
            from cascadia.fleet.fleet_registry import FleetRegistry
            reg = FleetRegistry()
            return 200, {'nodes': reg.list_nodes(), 'generated_at': _now()}
        except Exception as exc:
            return 500, {'error': str(exc)}

    def fleet_register(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Register a new fleet node: {node_id, name, host, port}. Requires enterprise tier."""
        check = self._public_tier_check('enterprise')
        if check: return check
        node_id = payload.get('node_id', '')
        name    = payload.get('name', '')
        host    = payload.get('host', '')
        port    = int(payload.get('port', 6300))
        if not node_id or not host:
            return 400, {'error': 'node_id and host required'}
        try:
            from cascadia.fleet.fleet_registry import FleetRegistry
            reg = FleetRegistry()
            node = reg.register(node_id, name or node_id, host, port)
            return 201, {'node': node}
        except Exception as exc:
            return 500, {'error': str(exc)}

    def fleet_remove(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Remove a fleet node: {node_id}. Requires enterprise tier."""
        check = self._public_tier_check('enterprise')
        if check: return check
        node_id = payload.get('node_id', '')
        if not node_id:
            return 400, {'error': 'node_id required'}
        try:
            from cascadia.fleet.fleet_registry import FleetRegistry
            reg = FleetRegistry()
            removed = reg.remove(node_id)
            if removed:
                return 200, {'removed': node_id}
            return 404, {'error': 'node not found'}
        except Exception as exc:
            return 500, {'error': str(exc)}

    def depot_operators(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Browse the DEPOT operator marketplace catalogue.
        Tries live fetch from depot.zyrcon.ai first; falls back to local catalog.
        Merges with registry to show installed/active status.
        """
        import urllib.request as _ur
        import urllib.error as _ue

        operators: List[Dict[str, Any]] = []

        # Try live DEPOT catalog
        try:
            with _ur.urlopen('https://depot.zyrcon.ai/api/v1/operators', timeout=2) as r:
                data = json.loads(r.read().decode())
                operators = data.get('operators', [])
        except Exception:
            pass

        # Fallback to local DEPOTClient catalog
        if not operators:
            try:
                from cascadia.marketplace.depot_client import DEPOTClient
                operators = DEPOTClient().list_operators()
            except Exception:
                operators = []

        # Load registry to mark installed operators
        try:
            configured = self.config.get('operators_registry_path', '')
            reg_path = (Path(configured).expanduser() if configured
                        else Path(__file__).parent.parent / 'operators' / 'registry.json')
            reg = json.loads(reg_path.read_text())
            installed_ids = {op.get('id') for op in reg.get('operators', [])}
        except Exception:
            installed_ids = set()

        for op in operators:
            op['installed'] = op.get('id', op.get('operator_id', '')) in installed_ids

        return 200, {'operators': operators, 'count': len(operators), 'generated_at': _now()}

    def depot_operator(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Get details for one DEPOT operator: {operator_id}."""
        op_id = payload.get('operator_id', '')
        if not op_id:
            return 400, {'error': 'operator_id required'}
        try:
            from cascadia.marketplace.depot_client import DEPOTClient
            client = DEPOTClient()
            op = client.get_operator(op_id)
            if op is None:
                return 404, {'error': 'operator not found'}
            return 200, op
        except Exception as exc:
            return 500, {'error': str(exc)}

    def social_scheduled(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Return queued scheduled posts across all social platforms."""
        try:
            from operators.social.pipeline.post_scheduler import PostScheduler
            scheduler = PostScheduler(publish_fn=lambda p, c: {})
            return 200, {'posts': scheduler.get_queue(), 'generated_at': _now()}
        except Exception as exc:
            return 500, {'error': str(exc)}

    def system_monitor(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Live hardware metrics via SystemMonitor (psutil-backed)."""
        try:
            from cascadia.hardware.system_monitor import SystemMonitor
            snap = SystemMonitor().snapshot()
            return 200, {**snap, 'generated_at': _now()}
        except Exception as exc:
            return 500, {'error': str(exc)}

    # ------------------------------------------------------------------
    # F1 — Payment config endpoint
    # ------------------------------------------------------------------

    def config_payment(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Return configured payment links — read-only view for the UI."""
        links = self.config.get('payment_links', {})
        stripe_cfg = self.config.get('stripe', {})
        return 200, {
            'payment_links': links,
            'stripe_configured': bool(stripe_cfg.get('webhook_secret')),
            'generated_at': _now(),
        }

    # ------------------------------------------------------------------
    # F2 — Production readiness endpoint
    # ------------------------------------------------------------------

    def production_status(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Return production readiness signals for ops monitoring."""
        issues: List[str] = []

        license_key = self.config.get('license_key', '')
        if not license_key or license_key.startswith('replace-'):
            issues.append('license_key not set')

        curtain_secret = self.config.get('curtain', {}).get('signing_secret', '')
        if not curtain_secret or curtain_secret.startswith('replace-'):
            issues.append('curtain.signing_secret not set')

        license_secret = self.config.get('license_secret', '')
        if not license_secret or license_secret.startswith('replace-'):
            issues.append('license_secret not set')

        if not self.config.get('stripe', {}).get('webhook_secret'):
            issues.append('stripe.webhook_secret not set')

        sentinel_fail_open = self.config.get('sentinel_fail_open', False)
        if sentinel_fail_open:
            issues.append('sentinel_fail_open=true (unsafe for production)')

        ready = len(issues) == 0
        return 200, {
            'production_ready': ready,
            'issues': issues,
            'generated_at': _now(),
        }

    # ------------------------------------------------------------------
    # F3 — Per-IP rate limiter (applied to high-value endpoints)
    # ------------------------------------------------------------------

    def _check_rate_limit(self, remote_addr: str, limit: int = 30, window: int = 60) -> bool:
        """Return True if request is within rate limit, False if exceeded."""
        return self._rate_limiter.check(remote_addr, limit=limit, window=window)

    @staticmethod
    def _get_category(operator_id: str, manifest_type: str = '') -> str:
        if manifest_type in ('orchestrator', 'channel', 'connector', 'operator'):
            return manifest_type
        return {
            'vanguard': 'orchestrator', 'stitch': 'orchestrator',
            'chief': 'orchestrator', 'crew': 'orchestrator',
            'beacon': 'channel', 'herald': 'channel',
            'connect': 'connector', 'social': 'connector',
        }.get(operator_id, 'operator')

    @staticmethod
    def _get_operator_mode(op: Dict[str, Any]) -> str:
        return op.get('autonomy_level', 'semi_autonomous')

    def operator_overview(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """GET /api/overview — operator-centric dashboard snapshot with ROI + secondary stats."""
        configured = self.config.get('operators_registry_path', '')
        registry_path = (Path(configured).expanduser() if configured
                         else Path(__file__).parent.parent / 'operators' / 'registry.json')
        try:
            registry = json.loads(registry_path.read_text())
            all_ops = registry.get('operators', [])
            ops_to_check = [op for op in all_ops if op.get('autostart')]
        except Exception:
            ops_to_check = []

        _SECONDARY: Dict[str, tuple] = {
            'scout':  (7002, '/api/leads/stats',  'leads_today',    'Leads Today'),
            'chief':  (8006, '/api/roi',           'revenue_30d',   'Rev 30d'),
            'social': (0,    '/api/social/stats',  'posts_today',   'Posts Today'),
        }

        result_ops: List[Dict[str, Any]] = []
        offline_count = 0

        for op in ops_to_check:
            port = op.get('port')
            op_id = op.get('id', '')
            health_path = op.get('health_path', '/api/health')

            health = _http_get(port, health_path, timeout=2.0) if port else None
            status = 'online' if health else 'offline'
            if not health:
                offline_count += 1

            runs_today = 0
            extra_stat = '—'
            extra_label = 'Stat'
            last_run = None

            s_port, s_path, s_key, s_label = _SECONDARY.get(op_id, (port, '/api/stats', 'runs_today', 'Runs'))
            effective_port = s_port or port
            if effective_port:
                stats = _http_get(effective_port, s_path, timeout=2.0) or {}
                runs_today = stats.get('runs_today', 0)
                raw = stats.get(s_key, 0)
                if op_id == 'chief' and isinstance(raw, (int, float)):
                    extra_stat = f'${raw:,.0f}'
                else:
                    extra_stat = str(raw) if raw else '—'
                extra_label = s_label
                last_run = stats.get('last_run')

            result_ops.append({
                'id':          op_id,
                'name':        op.get('name', op_id),
                'category':    self._get_category(op_id, op.get('type', '')),
                'mode':        self._get_operator_mode(op),
                'status':      status,
                'port':        port,
                'runs_today':  runs_today,
                'extra_stat':  extra_stat,
                'extra_label': extra_label,
                'last_run':    last_run,
            })

        categories: Dict[str, Dict[str, int]] = {
            cat: {'total': 0, 'healthy': 0}
            for cat in ('orchestrator', 'operator', 'connector', 'channel')
        }
        for r in result_ops:
            cat = r.get('category', 'operator')
            if cat not in categories:
                cat = 'operator'
            categories[cat]['total'] += 1
            if r['status'] == 'online':
                categories[cat]['healthy'] += 1

        if offline_count == 0:
            system_health = 'healthy'
        elif offline_count <= 3:
            system_health = 'degraded'
        else:
            system_health = 'critical'

        chief_port = self._ports.get('chief', 8006)
        roi_raw = _http_get(chief_port, '/api/roi', timeout=2.0) or {}
        roi = {
            'revenue_30d':      roi_raw.get('revenue_30d', 0),
            'leads_this_week':  roi_raw.get('leads_this_week', 0),
            'workflows_run':    roi_raw.get('workflows_run', 0),
            'time_saved_hours': roi_raw.get('time_saved_hours', 0),
        }

        return 200, {
            'generated_at':  _now(),
            'system_health': system_health,
            'operators':     result_ops,
            'roi':           roi,
            'categories':    categories,
        }

    # ------------------------------------------------------------------
    # Tier gate helpers
    # ------------------------------------------------------------------

    _TIER_RANKS = {'lite': 0, 'pro': 1, 'business': 2, 'enterprise': 3}

    def _public_tier_check(self, required: str = 'pro') -> Optional[tuple]:
        """Returns (403, dict) if the public license key is below required tier, else None.
        Uses license_gate (public) — fails open if gate is unavailable.
        """
        try:
            from cascadia.licensing.license_gate import _build_status
            key = self.config.get('license_key', '')
            status = _build_status(key or None)
            user_rank = self._TIER_RANKS.get(status.get('tier', 'lite'), 0)
            required_rank = self._TIER_RANKS.get(required, 1)
            if user_rank < required_rank:
                return 403, {
                    'error': 'tier_required',
                    'tier_required': required,
                    'current_tier': status.get('tier', 'lite'),
                    'upgrade_url': 'https://zyrcon.store',
                }
        except Exception:
            pass  # fail-open if license_gate unavailable
        return None

    # ------------------------------------------------------------------
    # Workflow Designer handlers
    # ------------------------------------------------------------------

    def _workflow_tier_check(self, required: str = 'pro') -> Optional[tuple]:
        """Returns (403, dict) if current license tier is insufficient, else None."""
        try:
            from cascadia.licensing.tier_validator import TierValidator, TIER_RANKS
            key = self.config.get('license_key', '')
            if not key:
                return 403, {'error': 'tier_required', 'tier_required': required, 'upgrade_url': 'https://zyrcon.store'}
            validator = TierValidator(self.config.get('license_secret', ''))
            info = validator.validate(key)
            if not info:
                return 403, {'error': 'invalid_license'}
            user_rank = TIER_RANKS.get(info.get('tier', 'lite'), 0)
            required_rank = TIER_RANKS.get(required, 1)
            if user_rank < required_rank:
                return 403, {'error': 'tier_required', 'tier_required': required,
                             'current_tier': info.get('tier'), 'upgrade_url': 'https://zyrcon.store'}
        except Exception:
            pass  # fail-open if validator unavailable
        return None

    def wf_list(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        stitch_port = self._ports.get('stitch', 6201)
        result = _http_get(stitch_port, '/api/stitch/workflows') or {'workflows': []}
        return 200, result

    def wf_save(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        check = self._workflow_tier_check('pro')
        if check: return check
        stitch_port = self._ports.get('stitch', 6201)
        result = _http_post(stitch_port, '/api/stitch/workflows', payload)
        return (200, result) if result else (502, {'error': 'stitch unavailable'})

    def wf_delete(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        check = self._workflow_tier_check('pro')
        if check: return check
        wf_id = payload.get('id', '')
        stitch_port = self._ports.get('stitch', 6201)
        result = _http_delete(stitch_port, f'/api/stitch/workflows/{wf_id}')
        if result is None:
            return 502, {'error': 'STITCH unavailable'}
        return 200, {'deleted': True, 'id': wf_id}

    def wf_palette(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Return available nodes for workflow palette — all tiers."""
        operators = _http_get(self._ports.get('crew', 5100), '/crew') or {}
        op_list = operators.get('operators', [])
        if isinstance(op_list, dict):
            op_list = [{'id': k, 'name': k, 'capabilities': []} for k in op_list]
        nodes = [
            {'type': 'operator', 'id': op.get('id'), 'name': op.get('name'), 'capabilities': op.get('capabilities', [])}
            for op in op_list
        ]
        nodes += [
            {'type': 'control', 'id': 'approval_gate', 'name': 'Approval Gate', 'capabilities': ['approval']},
            {'type': 'control', 'id': 'condition',     'name': 'Condition',     'capabilities': ['branch']},
            {'type': 'control', 'id': 'delay',         'name': 'Delay',         'capabilities': ['timing']},
        ]
        return 200, {'nodes': nodes, 'generated_at': _now()}

    # ------------------------------------------------------------------
    # Backup handlers
    # ------------------------------------------------------------------

    def list_backups(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        from cascadia.durability.backup import BackupManager
        db = self.config.get('database_path', './data/runtime/cascadia.db')
        bdir = self.config.get('backup_dir', './data/backups')
        mgr = BackupManager(db, bdir)
        return 200, {'backups': mgr.list_backups(), 'generated_at': _now()}

    def create_backup(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        from cascadia.durability.backup import BackupManager
        db = self.config.get('database_path', './data/runtime/cascadia.db')
        bdir = self.config.get('backup_dir', './data/backups')
        mgr = BackupManager(db, bdir)
        try:
            path = mgr.create_backup()
            return 200, {'created': True, 'path': str(path), 'generated_at': _now()}
        except Exception as e:
            return 500, {'error': str(e)}

    def verify_backup(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        from cascadia.durability.backup import BackupManager
        db = self.config.get('database_path', './data/runtime/cascadia.db')
        bdir = self.config.get('backup_dir', './data/backups')
        mgr = BackupManager(db, bdir)
        backups = mgr.list_backups()
        ok = mgr.verify_latest()
        return 200, {'verified': ok, 'latest': backups[0] if backups else None, 'generated_at': _now()}

    # ------------------------------------------------------------------
    # Campaign notify handler (Task 32)
    # ------------------------------------------------------------------

    def campaign_notify(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Receive campaign state push from social operator; store by session_id."""
        session_id = payload.get('session_id', '')
        state      = payload.get('state', {})
        source     = payload.get('source', '')
        if not hasattr(self, '_campaign_notifications'):
            self._campaign_notifications = []
        if not hasattr(self, '_campaign_states'):
            self._campaign_states: Dict[str, Any] = {}
        self._campaign_notifications.append({
            'session_id': session_id, 'state': state,
            'source': source, 'received_at': _now()
        })
        self._campaign_notifications = self._campaign_notifications[-100:]
        self._campaign_states[session_id] = {
            'session_id':  session_id,
            'state':       state,
            'source':      source,
            'received_at': _now(),
        }
        try:
            self.runtime.broadcast_event({'type': 'campaign_update', 'session_id': session_id, 'state': state})
        except Exception:
            pass
        return 200, {'received': True}

    def campaign_states(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Return all known campaign states pushed by the social operator."""
        if not hasattr(self, '_campaign_states'):
            self._campaign_states = {}
        states = sorted(
            self._campaign_states.values(),
            key=lambda s: s.get('received_at', ''),
            reverse=True,
        )
        return 200, {
            'states':       states,
            'count':        len(states),
            'generated_at': _now(),
        }

    # ------------------------------------------------------------------
    # Sprint 3 — SENTINEL alert ingestion
    # ------------------------------------------------------------------

    def receive_alert(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        POST /api/prism/alert
        Called by SENTINEL circuit breakers (e.g. Social operator) when they open.
        Body: { "type": "sentinel_down", "message": "...", "source": "social_operator" }
        Stores last 50 alerts; evicts oldest on overflow.
        """
        if not hasattr(self, '_alerts'):
            self._alerts: List[Dict[str, Any]] = []
        entry = {
            'type':        payload.get('type', 'unknown'),
            'message':     payload.get('message', ''),
            'source':      payload.get('source', ''),
            'received_at': _now(),
        }
        self.runtime.logger.error(
            'PRISM alert [%s] from %s: %s',
            entry['type'], entry['source'], entry['message'],
        )
        self._alerts.append(entry)
        if len(self._alerts) > 50:
            self._alerts = self._alerts[-50:]
        return 200, {'received': True}

    def list_alerts(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """GET /api/prism/alerts — return last 20 alerts, newest first."""
        if not hasattr(self, '_alerts'):
            self._alerts = []
        recent = list(reversed(self._alerts[-20:]))
        return 200, {
            'alerts':       recent,
            'count':        len(recent),
            'generated_at': _now(),
        }

    def watchdog_status(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """GET /api/watchdog/status — operator health as tracked by OperatorWatchdog."""
        if self._watchdog is None:
            return 200, {'operators': {}, 'generated_at': _now(), 'poll_interval_seconds': 30}
        return 200, self._watchdog.get_status()

    # ------------------------------------------------------------------
    # Sprint 4 — Sales Funnel trigger
    # ------------------------------------------------------------------

    def sales_funnel_run(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /api/prism/sales_funnel/run — trigger the Sales Funnel workflow via STITCH."""
        stitch_port = self._ports.get('stitch', 6201)
        result = _http_post(stitch_port, '/api/workflows/wf_sales_funnel/run', {
            'id': 'wf_sales_funnel',
            'input': {
                'company_name':    payload.get('company_name', ''),
                'contact_name':    payload.get('contact_name', ''),
                'contact_email':   payload.get('contact_email', ''),
                'service_interest': payload.get('service_interest', ''),
            },
        }, timeout=10.0)
        if result is None:
            return 502, {'error': 'STITCH not reachable'}
        return 202, result

    def sales_funnel_run_status(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """GET /api/prism/sales_funnel/run/{run_id} — poll run state from STITCH."""
        run_id = payload.get('run_id', '')
        if not run_id:
            return 400, {'error': 'run_id required'}
        stitch_port = self._ports.get('stitch', 6201)
        result = _http_get(stitch_port, f'/api/workflows/runs/{run_id}', timeout=5.0)
        if result is None:
            return 502, {'error': 'STITCH not reachable'}
        return 200, result

    def sales_funnel_approve(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /api/prism/sales_funnel/approve/{run_id} — proxy approval decision to STITCH."""
        run_id = payload.get('run_id', '')
        if not run_id:
            return 400, {'error': 'run_id required'}
        stitch_port = self._ports.get('stitch', 6201)
        result = _http_post(stitch_port, f'/api/workflows/runs/{run_id}/approve', {
            'run_id': run_id,
            'approved': payload.get('approved', False),
            'note': payload.get('note', ''),
        }, timeout=10.0)
        if result is None:
            return 502, {'error': 'STITCH not reachable'}
        return 200, result

    def serve_sales_funnel(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """GET /sales-funnel — serve the Sales Funnel trigger UI."""
        html = (Path(__file__).parent / 'templates' / 'sales_funnel.html').read_bytes()
        return 200, {'__html__': html}

    # ------------------------------------------------------------------
    # Sprint 4 Task 7 — DEPOT install / remove
    # ------------------------------------------------------------------

    def depot_install(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /api/prism/depot/install — proxy to CREW /install_operator."""
        crew_port = self._ports.get('crew', 5100)
        result = _http_post(crew_port, '/install_operator', payload, timeout=30.0)
        if result is None:
            return 502, {'error': 'CREW not reachable'}
        return 200, result

    def depot_remove(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /api/prism/depot/remove — proxy to CREW /remove_operator with dry-run support."""
        # Dry-run pass: check impact before confirming
        if not payload.get('confirmed', False):
            crew_port = self._ports.get('crew', 5100)
            dry = _http_post(crew_port, '/remove_operator', {**payload, 'dry_run': True}, timeout=10.0)
            if dry is None:
                return 502, {'error': 'CREW not reachable'}
            affected = dry.get('affected_workflows', [])
            return 200, {
                'requires_confirmation': True,
                'operator_id': payload.get('operator_id', ''),
                'affected_workflows': affected,
                'dry_run': True,
                'message': f'This will remove the operator and affect {len(affected)} workflow(s). POST again with confirmed=true to proceed.',
            }
        # Confirmed — execute removal
        crew_port = self._ports.get('crew', 5100)
        result = _http_post(crew_port, '/remove_operator', payload, timeout=15.0)
        if result is None:
            return 502, {'error': 'CREW not reachable'}
        return 200, result

    # ------------------------------------------------------------------
    # Billing sprint handlers
    # ------------------------------------------------------------------

    def billing_stripe_webhook(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /api/stripe/webhook — receive and process Stripe events."""
        import json as _json
        import os as _os
        try:
            from cascadia.billing.stripe_handler import StripeHandler
            from cascadia.billing.subscription_manager import SubscriptionManager
            from cascadia.billing.email_delivery import EmailDelivery
            from cascadia.billing.license_generator import LicenseGenerator
        except ImportError as exc:
            return 200, {'received': True, 'error': f'billing module unavailable: {exc}'}
        webhook_secret = _os.environ.get('STRIPE_WEBHOOK_SECRET',
                                         self.config.get('stripe', {}).get('webhook_secret', ''))
        sig = payload.get('__headers__', {}).get('Stripe-Signature', '')
        # Re-serialize without injected runtime metadata for signature verification
        clean = {k: v for k, v in payload.items() if not k.startswith('__')}
        body = _json.dumps(clean, separators=(',', ':')).encode()
        try:
            stripe_handler = StripeHandler(
                webhook_secret=webhook_secret or 'placeholder',
                sub_manager=SubscriptionManager(),
                email=EmailDelivery(self.config),
                license_gen=LicenseGenerator(self.config),
            )
            stripe_handler.handle(body, sig)
        except Exception as exc:
            self.runtime.logger.error('Stripe webhook error: %s', exc)
        return 200, {'received': True}

    def get_billing_status(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """GET /api/prism/billing — subscription stats for PRISM billing dashboard."""
        try:
            from cascadia.billing.subscription_manager import SubscriptionManager
            sub_mgr = SubscriptionManager()
            return 200, {
                'stats': sub_mgr.get_stats(),
                'customers': sub_mgr.list_customers(),
            }
        except Exception as exc:
            return 500, {'error': str(exc)}

    def create_portal_session(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /api/prism/billing/portal — create Stripe billing portal session."""
        import json as _json
        import urllib.request as _ur
        import os as _os
        stripe_customer_id = payload.get('stripe_customer_id', '')
        secret = _os.environ.get('STRIPE_SECRET_KEY', '')
        if not secret or not stripe_customer_id:
            return 400, {'error': 'Missing configuration'}
        try:
            data = (
                f'customer={stripe_customer_id}'
                f'&return_url=http://localhost:6300'
            ).encode()
            req = _ur.Request(
                'https://api.stripe.com/v1/billing_portal/sessions',
                data=data, method='POST',
                headers={
                    'Authorization': f'Bearer {secret}',
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
            )
            with _ur.urlopen(req, timeout=10) as r:
                result = _json.loads(r.read())
            return 200, {'url': result.get('url', '')}
        except Exception as exc:
            self.runtime.logger.error('Portal session error: %s', exc)
            return 500, {'error': str(exc)}

    def create_checkout_session(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /api/prism/billing/checkout — create Stripe checkout session."""
        import json as _json
        import urllib.request as _ur
        import os as _os
        price_id = payload.get('price_id', '')
        email = payload.get('email', '')
        secret = _os.environ.get('STRIPE_SECRET_KEY', '')
        if not secret or not price_id:
            return 400, {'error': 'Missing price_id'}
        try:
            data = (
                f'line_items[0][price]={price_id}'
                f'&line_items[0][quantity]=1'
                f'&mode=subscription'
                f'&customer_email={email}'
                f'&success_url=http://localhost:6300?checkout=success'
                f'&cancel_url=http://localhost:6300?checkout=cancelled'
            ).encode()
            req = _ur.Request(
                'https://api.stripe.com/v1/checkout/sessions',
                data=data, method='POST',
                headers={
                    'Authorization': f'Bearer {secret}',
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
            )
            with _ur.urlopen(req, timeout=10) as r:
                result = _json.loads(r.read())
            return 200, {'url': result.get('url', '')}
        except Exception as exc:
            self.runtime.logger.error('Checkout session error: %s', exc)
            return 500, {'error': str(exc)}

    def handle_waitlist(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /api/waitlist — join product waitlist."""
        import sqlite3 as _sqlite3
        email = payload.get('email', '').strip()
        product = payload.get('product', 'Zyrcon')
        source = payload.get('source', 'website')
        if not email or '@' not in email:
            return 400, {'error': 'Valid email required'}
        try:
            db_path = Path(self.config.get('database_path', './data/runtime/cascadia.db'))
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with _sqlite3.connect(str(db_path)) as conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS waitlist (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        email TEXT NOT NULL,
                        product TEXT NOT NULL,
                        source TEXT,
                        created_at TEXT NOT NULL
                    )
                ''')
                conn.execute(
                    'INSERT INTO waitlist (email, product, source, created_at) VALUES (?,?,?,?)',
                    (email, product, source, _now()),
                )
            try:
                from cascadia.billing.email_delivery import EmailDelivery
                EmailDelivery(self.config).send_waitlist_confirmation(email, product)
            except Exception:
                pass
            self.runtime.logger.info('Waitlist: %s → %s', email, product)
            return 200, {'joined': True}
        except Exception as exc:
            self.runtime.logger.error('Waitlist error: %s', exc)
            return 500, {'error': str(exc)}

    def waitlist_export(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """GET /api/waitlist/export — CSV export of waitlist entries. Requires X-Cascadia-Key."""
        import os as _os
        import sqlite3 as _sqlite3
        key_header = payload.get('__headers__', {}).get('X-Cascadia-Key', '')
        expected = _os.environ.get('CASCADIA_INTERNAL_KEY', '')
        if expected and key_header != expected:
            return 401, {'error': 'unauthorized'}
        try:
            db_path = str(self.config.get('database_path', './data/runtime/cascadia.db'))
            with _sqlite3.connect(db_path) as conn:
                conn.row_factory = _sqlite3.Row
                rows = conn.execute(
                    'SELECT email, product, source, created_at FROM waitlist ORDER BY created_at DESC'
                ).fetchall()
            lines = ['email,product,source,created_at']
            for r in rows:
                lines.append(f'{r["email"]},{r["product"]},{r["source"]},{r["created_at"]}')
            return 200, {'__html__': '\n'.join(lines).encode(), 'content_type': 'text/csv'}
        except Exception as exc:
            return 500, {'error': str(exc)}

    def register_device_token(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /api/prism/notifications/register — store APNs device token."""
        import sqlite3 as _sqlite3
        device_token = payload.get('device_token', '').strip()
        platform = payload.get('platform', 'ios')
        if not device_token:
            return 400, {'error': 'device_token required'}
        try:
            db_path = Path(self.config.get('database_path', './data/runtime/cascadia.db'))
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with _sqlite3.connect(str(db_path)) as conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS device_tokens (
                        device_token TEXT PRIMARY KEY,
                        platform TEXT NOT NULL,
                        registered_at TEXT NOT NULL
                    )
                ''')
                conn.execute('''
                    INSERT OR REPLACE INTO device_tokens (device_token, platform, registered_at)
                    VALUES (?,?,?)
                ''', (device_token, platform, _now()))
            self.runtime.logger.info('APNs: registered token for platform=%s', platform)
            return 200, {'registered': True}
        except Exception as exc:
            return 500, {'error': str(exc)}

    def tier_status(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """GET /api/prism/tier — return current license tier and rank."""
        from cascadia.licensing.tier_validator import TierValidator, TIER_RANKS
        license_key = self.config.get('license_key', '')
        license_secret = self.config.get('license_secret', '')
        if not license_key or not license_secret:
            return 200, {'tier': 'lite', 'rank': 0}
        try:
            result = TierValidator(license_secret).validate(license_key)
            if result.get('valid'):
                tier = result['tier']
                return 200, {'tier': tier, 'rank': TIER_RANKS.get(tier, 0)}
        except Exception:
            pass
        return 200, {'tier': 'lite', 'rank': 0}

    def start(self) -> None:
        self.runtime.logger.info('PRISM dashboard active')
        if self._watchdog is not None:
            self._watchdog.start()
        self._start_approval_timeout_daemon()
        try:
            from cascadia.network.discovery import start_discovery
            ok = start_discovery(port=self.runtime.port)
            if ok:
                self.runtime.logger.info('mDNS: registered _cascadia._tcp.local.')
        except Exception:
            pass  # mDNS is optional
        self.runtime.start()

    def _start_approval_timeout_daemon(self) -> None:
        try:
            from cascadia.system.approval_timeout import ApprovalTimeoutDaemon
            db_path   = self.config.get('database_path', './data/runtime/cascadia.db')
            hs_port   = self._ports.get('handshake', 6203)
            owner     = self.config.get('weekly_summary_email', '')
            escalation = self.config.get('approval_escalation_email', '') or owner
            timeouts  = self.config.get('approval_timeouts')
            daemon = ApprovalTimeoutDaemon(
                db_path=db_path,
                handshake_port=hs_port,
                owner_email=owner,
                escalation_email=escalation,
                timeouts=timeouts or None,
            )
            daemon.start()
            self.runtime.logger.info('PRISM: approval timeout daemon started')
        except Exception as exc:
            self.runtime.logger.warning('PRISM: approval timeout daemon failed to start: %s', exc)


def main() -> None:
    p = argparse.ArgumentParser(description='PRISM - Cascadia OS dashboard')
    p.add_argument('--config', required=True)
    p.add_argument('--name', required=True)
    a = p.parse_args()
    PrismService(a.config, a.name).start()


if __name__ == '__main__':
    main()
