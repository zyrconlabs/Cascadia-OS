# MATURITY: PRODUCTION — Validated operator manifest schema.
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

VALID_TYPES = {'system', 'service', 'skill', 'composite'}
VALID_AUTONOMY = {'manual_only', 'assistive', 'semi_autonomous', 'autonomous'}


@dataclass(slots=True)
class Manifest:
    """Owns validated operator-asset metadata. Does not own registration side effects."""
    id: str
    name: str
    version: str
    type: str
    capabilities: List[str]
    required_dependencies: List[str]
    requested_permissions: List[str]
    autonomy_level: str
    health_hook: str
    description: str


class ManifestValidationError(ValueError):
    pass


def validate_manifest(data: Dict[str, Any]) -> Manifest:
    """Owns manifest validation. Does not own installation or enforcement."""
    required = {'id', 'name', 'version', 'type', 'capabilities', 'required_dependencies', 'requested_permissions', 'autonomy_level', 'health_hook', 'description'}
    missing = required - set(data)
    if missing:
        raise ManifestValidationError(f'Missing keys: {sorted(missing)}')
    if data['type'] not in VALID_TYPES:
        raise ManifestValidationError(f"Invalid type: {data['type']}")
    if data['autonomy_level'] not in VALID_AUTONOMY:
        raise ManifestValidationError(f"Invalid autonomy level: {data['autonomy_level']}")
    if not data['id'].islower() or '-' in data['id']:
        raise ManifestValidationError('Manifest id must be lowercase and underscored')
    for key in ('capabilities', 'required_dependencies', 'requested_permissions'):
        if not isinstance(data[key], list):
            raise ManifestValidationError(f'{key} must be a list')
    return Manifest(**data)


def load_manifest(path: str | Path) -> Manifest:
    """Owns manifest file loading. Does not own registry persistence."""
    return validate_manifest(json.loads(Path(path).read_text(encoding='utf-8')))
