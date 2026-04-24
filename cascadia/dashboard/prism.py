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
from pathlib import Path
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib import request as urllib_request

from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
            'beacon': 'Runtime', 'stitch': 'Runtime', 'vanguard': 'Runtime',
            'handshake': 'Runtime', 'bell': 'Runtime', 'almanac': 'Runtime',
            'prism': 'Dashboard',
        }
        COMPONENTS = (
            [('flint', self._flint_port, 'Kernel')] +
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

        return 200, {
            'cascadia_os': 'v0.44',
            'generated_at': _now(),
            'system': {
                'flint_state': flint.get('state', 'unknown'),
                'components_healthy': f'{healthy_count}/{total_count}',
                'component_states': component_states,
            },
            'crew': {
                'operator_count': crew.get('crew_size', 0),
                'operators': list(crew.get('operators', {}).keys()),
            },
            'runs': runs,
            'attention_required': {
                'pending_approvals': len(approvals),
                'blocked_runs': len(blocked),
                'approvals': approvals[:5],   # Show first 5
                'blocked': blocked[:5],
            },
        }

    def system_status(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Full FLINT component status. Includes process_state, health, restart counts."""
        flint = _http_get(self._flint_port, '/api/flint/status') or {}
        sentinel = _http_get(self._ports.get('sentinel', 0), '/risk-levels') or {}
        return 200, {
            'flint': flint,
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
                    'a.created_at, r.goal '
                    'FROM approvals a '
                    'JOIN runs r ON a.run_id = r.run_id '
                    "WHERE a.decision = 'pending' "
                    'ORDER BY a.created_at ASC'
                ).fetchall()
            return [dict(r) for r in rows]
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

    def start(self) -> None:
        self.runtime.logger.info('PRISM dashboard active')
        self.runtime.start()


def main() -> None:
    p = argparse.ArgumentParser(description='PRISM - Cascadia OS dashboard')
    p.add_argument('--config', required=True)
    p.add_argument('--name', required=True)
    a = p.parse_args()
    PrismService(a.config, a.name).start()


if __name__ == '__main__':
    main()
