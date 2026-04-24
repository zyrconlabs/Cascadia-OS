"""
once.py - Cascadia OS v0.44
ONCE: Installer software for Cascadia OS.

Owns: environment checks, directory setup, database initialization,
      config generation, operator manifest installation, first-run validation.
Does not own: process supervision (FLINT), operator execution,
              runtime management, or AI setup UI (handled by PRISM Settings).

AI mode is configured through PRISM dashboard Settings surface.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from cascadia import VERSION, VERSION_SHORT

REQUIRED_PYTHON = (3, 11)

DEFAULT_DIRS = [
    'data/runtime',
    'data/logs',
    'data/vault',
    'models',
    'cascadia/operators',
]

DEFAULT_CONFIG: Dict[str, Any] = {
    'log_dir': './data/logs',
    'database_path': './data/runtime/cascadia.db',
    'llm': {
        'provider': None,
        'model': None,
        'configured': False,
    },
    'flint': {
        'heartbeat_file': './data/runtime/flint.heartbeat',
        'heartbeat_interval_seconds': 5,
        'heartbeat_stale_after_seconds': 15,
        'status_port': 4011,
        'health_interval_seconds': 5,
        'drain_timeout_seconds': 10,
        'max_restart_attempts': 5,
        'restart_backoff_seconds': [5, 30, 120, 600],
    },
    'curtain': {
        'signing_secret': '',
    },
    'components': [
        {'name': 'crew',      'module': 'cascadia.registry.crew',            'port': 5100, 'tier': 1, 'heartbeat_file': './data/runtime/crew.heartbeat'},
        {'name': 'vault',     'module': 'cascadia.memory.vault',             'port': 5101, 'tier': 1, 'heartbeat_file': './data/runtime/vault.heartbeat'},
        {'name': 'sentinel',  'module': 'cascadia.security.sentinel',        'port': 5102, 'tier': 1, 'heartbeat_file': './data/runtime/sentinel.heartbeat'},
        {'name': 'curtain',   'module': 'cascadia.encryption.curtain',       'port': 5103, 'tier': 1, 'heartbeat_file': './data/runtime/curtain.heartbeat'},
        {'name': 'beacon',    'module': 'cascadia.orchestrator.beacon',      'port': 6200, 'tier': 2, 'heartbeat_file': './data/runtime/beacon.heartbeat', 'depends_on': ['crew']},
        {'name': 'stitch',    'module': 'cascadia.automation.stitch',        'port': 6201, 'tier': 2, 'heartbeat_file': './data/runtime/stitch.heartbeat'},
        {'name': 'vanguard',  'module': 'cascadia.gateway.vanguard',         'port': 6202, 'tier': 2, 'heartbeat_file': './data/runtime/vanguard.heartbeat'},
        {'name': 'handshake', 'module': 'cascadia.bridge.handshake',         'port': 6203, 'tier': 2, 'heartbeat_file': './data/runtime/handshake.heartbeat'},
        {'name': 'bell',      'module': 'cascadia.chat.bell',                'port': 6204, 'tier': 2, 'heartbeat_file': './data/runtime/bell.heartbeat'},
        {'name': 'almanac',   'module': 'cascadia.guide.almanac',            'port': 6205, 'tier': 2, 'heartbeat_file': './data/runtime/almanac.heartbeat'},
        {'name': 'prism',     'module': 'cascadia.dashboard.prism',          'port': 6300, 'tier': 3, 'heartbeat_file': './data/runtime/prism.heartbeat', 'depends_on': ['crew', 'sentinel', 'beacon']},
    ],
    'sentinel_fail_open': False,
    'models': [
        {'id': 'qwen2.5-3b',    'name': 'Qwen 2.5 3B',    'file': 'qwen2.5-3b-instruct-q4_k_m.gguf',    'desc': '3B · Fast · Local',     'size': '3B',  'context': 4096, 'recommended_for': 'quick tasks, lead classification, drafts'},
        {'id': 'qwen2.5-7b',    'name': 'Qwen 2.5 7B',    'file': 'qwen2.5-7b-instruct-q4_k_m.gguf',    'desc': '7B · Balanced · Local', 'size': '7B',  'context': 8192, 'recommended_for': 'proposals, analysis, general workflows'},
        {'id': 'qwen2.5-14b',   'name': 'Qwen 2.5 14B',   'file': 'Qwen2.5-14B-Instruct-Q4_K_M.gguf',   'desc': '14B · Powerful · Local','size': '14B', 'context': 8192, 'recommended_for': 'complex reasoning, large documents'},
        {'id': 'qwen2.5-vl-7b', 'name': 'Qwen 2.5 VL 7B', 'file': 'qwen2.5-vl-7b-instruct-q4_k_m.gguf', 'desc': '7B · Vision · Local',   'size': '7B',  'context': 8192, 'recommended_for': 'image analysis, document OCR'},
    ],
}


def _detect_ollama() -> Optional[List[str]]:
    try:
        from urllib import request as ur
        with ur.urlopen('http://localhost:11434/api/tags', timeout=2) as r:
            data = json.loads(r.read().decode())
            return [m['name'] for m in data.get('models', [])]
    except Exception:
        return None


class OnceInstaller:
    """
    ONCE - Cascadia OS installer.
    Run once to set up a new installation. Idempotent — safe to re-run.
    AI mode is configured in PRISM Settings surface after startup.
    """

    def __init__(self, install_dir: str = '.', config_path: str = 'config.json',
                 no_browser: bool = False) -> None:
        self.install_dir = Path(install_dir).resolve()
        self.config_path = self.install_dir / config_path
        self.no_browser = no_browser  # kept for CLI compat, always treated as True
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def _log(self, msg: str) -> None:
        print(f'  ONCE  {msg}')

    def _warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print(f'  WARN  {msg}')

    def _error(self, msg: str) -> None:
        self.errors.append(msg)
        print(f'  ERROR {msg}')

    def check_python(self) -> bool:
        current = sys.version_info[:2]
        if current < REQUIRED_PYTHON:
            self._error(f'Python {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}+ required. Found: {current[0]}.{current[1]}')
            return False
        self._log(f'Python {current[0]}.{current[1]} OK')
        return True

    def create_directories(self) -> None:
        for d in DEFAULT_DIRS:
            path = self.install_dir / d
            path.mkdir(parents=True, exist_ok=True)
            self._log(f'Directory ready: {d}')

    def generate_config(self) -> None:
        if self.config_path.exists():
            self._log(f'Config exists: {self.config_path.name} (skipping)')
            return
        config = dict(DEFAULT_CONFIG)
        config['curtain'] = {'signing_secret': secrets.token_hex(32)}
        self.config_path.write_text(json.dumps(config, indent=2))
        self._log(f'Config generated: {self.config_path.name}')

    def _apply_llm_config(self, llm: Dict[str, Any]) -> None:
        try:
            config = json.loads(self.config_path.read_text())
            if 'models' not in config:
                config['models'] = DEFAULT_CONFIG['models']
            llm['configured'] = True
            config['llm'] = llm
            self.config_path.write_text(json.dumps(config, indent=2))
            provider = llm.get('provider') or 'none'
            model = llm.get('model') or 'none'
            self._log(f'AI configured: {provider} / {model}')
        except Exception as exc:
            self._warn(f'Could not write AI config: {exc}')

    def init_database(self) -> None:
        try:
            config = json.loads(self.config_path.read_text())
            db_path = self.install_dir / config['database_path'].lstrip('./ ')
            db_path.parent.mkdir(parents=True, exist_ok=True)
            sys.path.insert(0, str(self.install_dir))
            from cascadia.shared.db import ensure_database
            ensure_database(str(db_path))
            self._log(f'Database initialized: {db_path.name}')
        except Exception as exc:
            self._warn(f'Database init skipped: {exc}')

    def install_manifests(self) -> None:
        manifest_dir = self.install_dir / 'cascadia' / 'operators'
        if not manifest_dir.exists():
            self._warn('Operator manifest directory not found')
            return
        manifests = [f for f in manifest_dir.glob('*.json') if f.name != 'registry.json']
        if not manifests:
            self._warn('No operator manifests found')
            return
        try:
            sys.path.insert(0, str(self.install_dir))
            from cascadia.shared.manifest_schema import load_manifest, ManifestValidationError
            for mf in manifests:
                try:
                    manifest = load_manifest(mf)
                    self._log(f'Manifest valid: {manifest.id} ({manifest.type})')
                except ManifestValidationError as exc:
                    self._warn(f'Manifest invalid: {mf.name}: {exc}')
        except ImportError:
            self._warn('Cannot validate manifests (run from project root)')

    def validate(self) -> bool:
        checks = [
            ('config.json', self.config_path.exists()),
            ('data/runtime/', (self.install_dir / 'data/runtime').exists()),
            ('data/logs/', (self.install_dir / 'data/logs').exists()),
        ]
        all_ok = True
        for name, ok in checks:
            self._log(f'{name}: {"OK" if ok else "MISSING"}')
            if not ok:
                all_ok = False
        return all_ok

    def run(self) -> int:
        print(f'\n  Cascadia OS v{VERSION} — ONCE Installer')
        print(f'  Install directory: {self.install_dir}\n')
        print(f'  AI mode will be configured in PRISM Settings after startup.\n')

        if not self.check_python():
            return 1

        self.create_directories()
        self.generate_config()
        self.init_database()
        self.install_manifests()

        print()
        ok = self.validate()

        if self.warnings:
            print(f'\n  {len(self.warnings)} warning(s):')
            for w in self.warnings:
                print(f'    - {w}')

        if self.errors:
            print(f'\n  {len(self.errors)} error(s):')
            for e in self.errors:
                print(f'    - {e}')
            return 1

        if ok:
            print(f'\n  Cascadia OS v{VERSION} — ready to start.')
            print('  AI setup: open PRISM → Settings\n')
            return 0
        else:
            print('\n  Installation incomplete. Check warnings above.\n')
            return 1


def main() -> None:
    p = argparse.ArgumentParser(description='ONCE - Cascadia OS installer')
    p.add_argument('--dir', default='.', help='Installation directory')
    p.add_argument('--config', default='config.json', help='Config file name')
    p.add_argument('--no-browser', action='store_true',
                   help='Ignored — AI setup is always done via PRISM Settings')
    a = p.parse_args()
    sys.exit(OnceInstaller(a.dir, a.config, a.no_browser).run())


if __name__ == '__main__':
    main()
