# MATURITY: PRODUCTION — Validated operator manifest schema.
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

VALID_TYPES = {'system', 'service', 'skill', 'composite'}
VALID_AUTONOMY = {'manual_only', 'assistive', 'semi_autonomous', 'autonomous'}
VALID_RISK_LEVELS = {'low', 'medium', 'high'}
VALID_FIELD_TYPES = {'string', 'boolean', 'select', 'number', 'slider', 'tags', 'secret'}

_MANIFEST_FIELDS = {
    'id', 'name', 'version', 'type', 'capabilities', 'required_dependencies',
    'requested_permissions', 'autonomy_level', 'health_hook', 'description',
    'risk_level', 'permissions', 'requires_approval_for', 'data_access',
    'writes_external_systems', 'network_access', 'setup_fields',
}


@dataclass
class SetupField:
    """Describes a single configurable field in an operator's setup wizard."""
    name: str
    label: str
    type: str
    required: bool = False
    default: Any = None
    help_text: Optional[str] = None
    placeholder: Optional[str] = None
    # UI mode visibility
    simple_mode: bool = True
    advanced_mode: bool = False
    developer_mode: bool = False
    # validation
    options: Optional[list] = None   # for select type
    min: Optional[int] = None
    max: Optional[int] = None
    pattern: Optional[str] = None
    # secret handling
    secret: bool = False
    vault_key: Optional[str] = None  # e.g. "google_accounts:client_secret"
    # approval awareness
    affects_permissions: Optional[list] = None
    requires_approval_if_enabled: Optional[list] = None


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
    risk_level: str = 'low'
    permissions: List[str] = field(default_factory=list)
    requires_approval_for: List[str] = field(default_factory=list)
    data_access: List[str] = field(default_factory=list)
    writes_external_systems: bool = False
    network_access: bool = False
    setup_fields: List[SetupField] = field(default_factory=list)


class ManifestValidationError(ValueError):
    pass


def _coerce_setup_field(data: Any) -> SetupField:
    """Convert a dict (from JSON) into a SetupField, validating required attributes."""
    if isinstance(data, SetupField):
        return data
    if not isinstance(data, dict):
        raise ManifestValidationError(f'setup_fields entries must be objects, got: {type(data).__name__}')
    name = data.get('name', '')
    label = data.get('label', '')
    ftype = data.get('type', '')
    if not name:
        raise ManifestValidationError("setup_fields entry missing required 'name'")
    if not label:
        raise ManifestValidationError(f"setup_fields entry '{name}' missing required 'label'")
    if ftype not in VALID_FIELD_TYPES:
        raise ManifestValidationError(
            f"setup_fields entry '{name}' has invalid type {ftype!r}; "
            f"must be one of {sorted(VALID_FIELD_TYPES)}"
        )
    return SetupField(
        name=name,
        label=label,
        type=ftype,
        required=data.get('required', False),
        default=data.get('default'),
        help_text=data.get('help_text'),
        placeholder=data.get('placeholder'),
        simple_mode=data.get('simple_mode', True),
        advanced_mode=data.get('advanced_mode', False),
        developer_mode=data.get('developer_mode', False),
        options=data.get('options'),
        min=data.get('min'),
        max=data.get('max'),
        pattern=data.get('pattern'),
        secret=data.get('secret', False),
        vault_key=data.get('vault_key'),
        affects_permissions=data.get('affects_permissions'),
        requires_approval_if_enabled=data.get('requires_approval_if_enabled'),
    )


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
    risk = data.get('risk_level', 'low')
    if risk not in VALID_RISK_LEVELS:
        raise ManifestValidationError(f"Invalid risk_level: {risk!r}; must be one of {sorted(VALID_RISK_LEVELS)}")
    # Deserialize setup_fields
    raw_fields = data.get('setup_fields', [])
    if not isinstance(raw_fields, list):
        raise ManifestValidationError('setup_fields must be a list')
    setup_fields = [_coerce_setup_field(f) for f in raw_fields]
    known = {k: v for k, v in data.items() if k in _MANIFEST_FIELDS and k != 'setup_fields'}
    return Manifest(**known, setup_fields=setup_fields)


def load_manifest(path: str | Path) -> Manifest:
    """Owns manifest file loading. Does not own registry persistence."""
    return validate_manifest(json.loads(Path(path).read_text(encoding='utf-8')))
