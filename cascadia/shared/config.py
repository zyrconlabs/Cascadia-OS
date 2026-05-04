# MATURITY: PRODUCTION — JSON config loader.
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

_log = logging.getLogger('cascadia.config')

_PULSE_MIGRATIONS = {
    'heartbeat_file':              'pulse_file',
    'heartbeat_interval_seconds':  'pulse_interval_seconds',
    'heartbeat_stale_after_seconds': 'pulse_stale_after_seconds',
}


def _migrate_pulse_keys(component: dict) -> dict:
    """
    Backward compatibility: accept old heartbeat_* keys from pre-v0.50 config.json.
    Silently maps old keys to new pulse_* keys. Removed in v0.52.
    """
    for old_key, new_key in _PULSE_MIGRATIONS.items():
        if old_key in component and new_key not in component:
            _log.warning(
                'Config: deprecated key "%s" found — '
                'please rename to "%s" in config.json. '
                'Compatibility shim active until 2026.7.',
                old_key, new_key,
            )
            component[new_key] = component[old_key]
    return component


def _migrate_flint_pulse_keys(flint_cfg: dict) -> dict:
    """Same migration for the flint section. Removed in v0.52."""
    for old_key, new_key in _PULSE_MIGRATIONS.items():
        if old_key in flint_cfg and new_key not in flint_cfg:
            _log.warning(
                'Config: deprecated key "%s" found — '
                'please rename to "%s" in config.json. '
                'Compatibility shim active until 2026.7.',
                old_key, new_key,
            )
            flint_cfg[new_key] = flint_cfg[old_key]
    return flint_cfg


def load_config(config_path: str) -> Dict[str, Any]:
    """Owns loading JSON config. Does not own validation beyond basic file existence."""
    config = json.loads(Path(config_path).read_text(encoding='utf-8'))
    if 'flint' in config:
        config['flint'] = _migrate_flint_pulse_keys(config['flint'])
    config['components'] = [_migrate_pulse_keys(c) for c in config.get('components', [])]
    return config
