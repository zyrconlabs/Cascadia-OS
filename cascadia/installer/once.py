"""
once/once.py - Cascadia OS v0.2
ONCE: Installer software for Cascadia OS.

Owns: environment checks, directory setup, database initialization,
      config generation, operator manifest installation,
      first-run validation.
Does not own: process supervision (FLINT), operator execution,
              or runtime management.

The name implies you run it one time to get everything set up.
"""
# MATURITY: FUNCTIONAL — Directory setup, config generation, and DB init work. Operator package install is v0.3.
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

VERSION = '0.2'

REQUIRED_PYTHON = (3, 11)

DEFAULT_DIRS = [
    'data/runtime',
    'data/logs',
    'data/vault',
    'cascadia/operators',
]

DEFAULT_CONFIG: Dict[str, Any] = {
    'log_dir': './data/logs',
    'database_path': './data/runtime/cascadia.db',
    'flint': {
        'heartbeat_file': './data/runtime/flint.heartbeat',
        'heartbeat_interval_seconds': 5,
        'heartbeat_stale_after_seconds': 15,
        'status_port': 18791,
        'health_interval_seconds': 5,
        'drain_timeout_seconds': 10,
        'max_restart_attempts': 5,
        'restart_backoff_seconds': [5, 30, 120, 600],
    },
    'curtain': {
        'signing_secret': '',  # Generated on first run
    },
    'components': [
        {'name': 'crew',      'module': 'cascadia.registry.crew',             'port': 18800, 'tier': 1, 'heartbeat_file': './data/runtime/crew.heartbeat'},
        {'name': 'vault',     'module': 'cascadia.memory.vault',            'port': 18801, 'tier': 1, 'heartbeat_file': './data/runtime/vault.heartbeat'},
        {'name': 'sentinel',  'module': 'cascadia.security.sentinel',         'port': 18802, 'tier': 1, 'heartbeat_file': './data/runtime/sentinel.heartbeat'},
        {'name': 'curtain',   'module': 'cascadia.encryption.curtain',  'port': 18803, 'tier': 1, 'heartbeat_file': './data/runtime/curtain.heartbeat'},
        {'name': 'beacon',    'module': 'cascadia.orchestrator.beacon',           'port': 18804, 'tier': 2, 'heartbeat_file': './data/runtime/beacon.heartbeat', 'depends_on': ['crew']},
        {'name': 'stitch',    'module': 'cascadia.automation.stitch',    'port': 18805, 'tier': 2, 'heartbeat_file': './data/runtime/stitch.heartbeat'},
        {'name': 'vanguard',  'module': 'cascadia.gateway.vanguard','port': 18806, 'tier': 2, 'heartbeat_file': './data/runtime/vanguard.heartbeat'},
        {'name': 'handshake', 'module': 'cascadia.bridge.handshake','port': 18807, 'tier': 2, 'heartbeat_file': './data/runtime/handshake.heartbeat'},
        {'name': 'bell',      'module': 'cascadia.chat.bell',        'port': 18808, 'tier': 2, 'heartbeat_file': './data/runtime/bell.heartbeat'},
        {'name': 'almanac',   'module': 'cascadia.guide.almanac',  'port': 18809, 'tier': 2, 'heartbeat_file': './data/runtime/almanac.heartbeat'},
        {'name': 'prism',     'module': 'cascadia.dashboard.prism',      'port': 18810, 'tier': 3, 'heartbeat_file': './data/runtime/prism.heartbeat', 'depends_on': ['crew', 'sentinel', 'beacon']},
    ],
}


class OnceInstaller:
    """
    ONCE - Cascadia OS installer.
    Run once to set up a new installation. Idempotent — safe to re-run.
    """

    def __init__(self, install_dir: str = '.', config_path: str = 'config.json') -> None:
        self.install_dir = Path(install_dir).resolve()
        self.config_path = self.install_dir / config_path
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
        """Verify Python version meets minimum requirement."""
        current = sys.version_info[:2]
        if current < REQUIRED_PYTHON:
            self._error(f'Python {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}+ required. Found: {current[0]}.{current[1]}')
            return False
        self._log(f'Python {current[0]}.{current[1]} OK')
        return True

    def create_directories(self) -> None:
        """Create required directories."""
        for d in DEFAULT_DIRS:
            path = self.install_dir / d
            path.mkdir(parents=True, exist_ok=True)
            self._log(f'Directory ready: {d}')

    def generate_config(self) -> None:
        """Generate config.json if it does not exist."""
        if self.config_path.exists():
            self._log(f'Config exists: {self.config_path.name} (skipping)')
            return
        import secrets
        config = dict(DEFAULT_CONFIG)
        config['curtain'] = {'signing_secret': secrets.token_hex(32)}
        self.config_path.write_text(json.dumps(config, indent=2))
        self._log(f'Config generated: {self.config_path.name}')

    def init_database(self) -> None:
        """Initialize the Cascadia OS database."""
        try:
            # Load config to get DB path
            config = json.loads(self.config_path.read_text())
            db_path = self.install_dir / config['database_path'].lstrip('./')
            db_path.parent.mkdir(parents=True, exist_ok=True)

            sys.path.insert(0, str(self.install_dir))
            from cascadia.shared.db import ensure_database
            ensure_database(str(db_path))
            self._log(f'Database initialized: {db_path.name}')
        except Exception as exc:
            self._warn(f'Database init skipped (run from project root): {exc}')

    def install_manifests(self) -> None:
        """Verify operator manifests are present and valid."""
        manifest_dir = self.install_dir / 'cascadia' / 'operators'
        if not manifest_dir.exists():
            self._warn('Operator manifest directory not found: cascadia/operators/')
            return
        manifests = list(manifest_dir.glob('*.json'))
        if not manifests:
            self._warn('No operator manifests found in cascadia/operators/')
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
        """Final validation check."""
        checks = [
            ('config.json', self.config_path.exists()),
            ('data/runtime/', (self.install_dir / 'data/runtime').exists()),
            ('data/logs/', (self.install_dir / 'data/logs').exists()),
        ]
        all_ok = True
        for name, ok in checks:
            status = 'OK' if ok else 'MISSING'
            self._log(f'{name}: {status}')
            if not ok:
                all_ok = False
        return all_ok

    def run(self) -> int:
        """Execute the full installation. Returns 0 on success, 1 on failure."""
        print(f'\n  Cascadia OS v{VERSION} — ONCE Installer')
        print(f'  Install directory: {self.install_dir}\n')

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
            print(f'\n  Cascadia OS v{VERSION} installation complete.')
            print(f'  Start with: python -m cascadia.watchdog --config config.json\n')
            return 0
        else:
            print('\n  Installation incomplete. Check warnings above.\n')
            return 1


def main() -> None:
    p = argparse.ArgumentParser(description='ONCE - Cascadia OS installer')
    p.add_argument('--dir', default='.', help='Installation directory')
    p.add_argument('--config', default='config.json', help='Config file name')
    a = p.parse_args()
    sys.exit(OnceInstaller(a.dir, a.config).run())


if __name__ == '__main__':
    main()
