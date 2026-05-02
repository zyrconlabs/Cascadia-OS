"""
cascadia/depot/manifest_validator.py
Owns: DEPOT manifest schema validation for operators and connectors.
Does not own: installation, registry writes, or runtime manifest validation
(that lives in cascadia/shared/manifest_schema.py).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── Schema constants ────────────────────────────────────────────────────────

VALID_TYPES = {'operator', 'connector', 'orchestrator'}

VALID_TIERS = {'lite', 'pro', 'business', 'enterprise'}

VALID_CATEGORIES = {
    'sales', 'marketing', 'support', 'finance', 'operations',
    'devops', 'ecommerce', 'data', 'hr', 'industry',
    'communication', 'productivity', 'iot', 'legal', 'integration',
    'analytics', 'identity', 'runtime',
}

VALID_AUTH_TYPES = {
    'oauth2', 'api_key', 'bearer', 'basic', 'hmac',
    'iam', 'service_account', 'signed_token', 'none',
}

VALID_RISK_LEVELS = {'low', 'medium', 'high'}

# Fields every DEPOT manifest must have
REQUIRED_FIELDS = {
    'id', 'name', 'type', 'version', 'description',
    'author', 'price', 'tier_required', 'port',
    'entry_point', 'dependencies', 'install_hook',
    'uninstall_hook', 'category', 'industries',
    'installed_by_default', 'safe_to_uninstall',
    'risk_level', 'permissions', 'requires_approval_for',
    'data_access', 'writes_external_systems', 'network_access',
}

# Fields that are valid but not required
OPTIONAL_FIELDS = {
    'icon', 'approval_required', 'approval_required_for_writes',
    'nats_subjects', 'auth_type', 'screenshots', 'readme',
    'changelog', 'homepage_url', 'support_email',
}

ALL_KNOWN_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS


# ── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.valid = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


# ── Core validator ───────────────────────────────────────────────────────────

def validate_depot_manifest(data: Dict[str, Any], base_path=None) -> ValidationResult:
    """
    Validate a DEPOT manifest dict against the full schema.
    Returns a ValidationResult with all errors and warnings found.
    Does not raise — callers check result.valid.
    """
    if data.get("type") == "mission":
        from cascadia.missions.manifest import MissionManifest
        mm = MissionManifest()
        errors = mm.validate(data, base_path)
        return ValidationResult(valid=len(errors) == 0, errors=errors)

    result = ValidationResult(valid=True)

    # 1. Required fields present
    missing = REQUIRED_FIELDS - set(data.keys())
    if missing:
        result.add_error(f"Missing required fields: {sorted(missing)}")

    # 2. No unknown fields (warn, don't error — forward compat)
    unknown = set(data.keys()) - ALL_KNOWN_FIELDS
    if unknown:
        result.add_warning(f"Unknown fields (ignored): {sorted(unknown)}")

    # Stop deep validation if required fields are missing — avoids cascade errors
    if not result.valid:
        return result

    # 3. id — lowercase, underscored slug, no spaces
    id_val = data['id']
    if not isinstance(id_val, str) or not id_val:
        result.add_error("'id' must be a non-empty string")
    elif not all(c.isalnum() or c in ('-', '_') for c in id_val):
        result.add_error("'id' must contain only alphanumeric characters, hyphens, or underscores")
    elif id_val != id_val.lower():
        result.add_error("'id' must be lowercase")

    # 4. name
    if not isinstance(data['name'], str) or not data['name'].strip():
        result.add_error("'name' must be a non-empty string")

    # 5. type
    if data['type'] not in VALID_TYPES:
        result.add_error(f"'type' must be one of {sorted(VALID_TYPES)}, got: {data['type']!r}")

    # 6. version — semver pattern x.y.z
    version = data['version']
    if not isinstance(version, str):
        result.add_error("'version' must be a string")
    else:
        parts = version.split('.')
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            result.add_error(f"'version' must be semver (x.y.z), got: {version!r}")

    # 7. description — non-empty string, warn if too long
    desc = data['description']
    if not isinstance(desc, str) or not desc.strip():
        result.add_error("'description' must be a non-empty string")
    elif len(desc) > 280:
        result.add_warning("'description' exceeds 280 characters — consider shortening for catalog display")

    # 8. author
    if not isinstance(data['author'], str) or not data['author'].strip():
        result.add_error("'author' must be a non-empty string")

    # 9. price — non-negative number
    price = data['price']
    if not isinstance(price, (int, float)) or price < 0:
        result.add_error("'price' must be a non-negative number")

    # 10. tier_required
    if data['tier_required'] not in VALID_TIERS:
        result.add_error(f"'tier_required' must be one of {sorted(VALID_TIERS)}, got: {data['tier_required']!r}")

    # 11. port — positive integer in valid range
    port = data['port']
    if not isinstance(port, int) or port <= 0:
        result.add_error("'port' must be a positive integer")
    elif not (8100 <= port <= 9999):
        result.add_warning(f"'port' {port} is outside the standard Cascadia range (8100–9999)")

    # 12. entry_point
    if not isinstance(data['entry_point'], str) or not data['entry_point'].strip():
        result.add_error("'entry_point' must be a non-empty string")

    # 13. dependencies — list of strings
    deps = data['dependencies']
    if not isinstance(deps, list):
        result.add_error("'dependencies' must be a list")
    elif not all(isinstance(d, str) for d in deps):
        result.add_error("'dependencies' must be a list of strings")

    # 14. install_hook / uninstall_hook
    for hook_field in ('install_hook', 'uninstall_hook'):
        if not isinstance(data[hook_field], str) or not data[hook_field].strip():
            result.add_error(f"'{hook_field}' must be a non-empty string")

    # 15. category
    if data['category'] not in VALID_CATEGORIES:
        result.add_error(f"'category' must be one of {sorted(VALID_CATEGORIES)}, got: {data['category']!r}")

    # 16. industries — non-empty list of strings
    industries = data['industries']
    if not isinstance(industries, list) or len(industries) == 0:
        result.add_error("'industries' must be a non-empty list")
    elif not all(isinstance(i, str) for i in industries):
        result.add_error("'industries' must be a list of strings")

    # 17. installed_by_default — must be False for all DEPOT items
    if not isinstance(data['installed_by_default'], bool):
        result.add_error("'installed_by_default' must be a boolean")
    elif data['installed_by_default'] is True:
        result.add_error("'installed_by_default' must be false for all DEPOT items")

    # 18. safe_to_uninstall
    if not isinstance(data['safe_to_uninstall'], bool):
        result.add_error("'safe_to_uninstall' must be a boolean")

    # 19. Optional: auth_type (connectors)
    if 'auth_type' in data and data['auth_type'] not in VALID_AUTH_TYPES:
        result.add_error(f"'auth_type' must be one of {sorted(VALID_AUTH_TYPES)}, got: {data['auth_type']!r}")

    # 20. Optional: nats_subjects — list of strings
    if 'nats_subjects' in data:
        ns = data['nats_subjects']
        if not isinstance(ns, list) or not all(isinstance(s, str) for s in ns):
            result.add_error("'nats_subjects' must be a list of strings")

    # 21. Connector-specific: warn if type==connector but no auth_type
    if data.get('type') == 'connector' and 'auth_type' not in data:
        result.add_warning("Connectors should specify 'auth_type'")

    # 22. risk_level — must be a valid level
    risk = data.get('risk_level')
    if not isinstance(risk, str) or risk not in VALID_RISK_LEVELS:
        result.add_error(f"'risk_level' must be one of {sorted(VALID_RISK_LEVELS)}, got: {risk!r}")

    # 23. permissions / requires_approval_for / data_access — lists of strings
    for list_field in ('permissions', 'requires_approval_for', 'data_access'):
        val = data.get(list_field)
        if not isinstance(val, list):
            result.add_error(f"'{list_field}' must be a list")
        elif not all(isinstance(s, str) for s in val):
            result.add_error(f"'{list_field}' must be a list of strings")

    # 24. writes_external_systems / network_access — booleans
    for bool_field in ('writes_external_systems', 'network_access'):
        val = data.get(bool_field)
        if not isinstance(val, bool):
            result.add_error(f"'{bool_field}' must be a boolean")

    return result


def validate_depot_manifest_file(path: str | Path) -> ValidationResult:
    """Load a manifest.json from disk and validate it."""
    path = Path(path)
    if not path.exists():
        result = ValidationResult(valid=False)
        result.add_error(f"File not found: {path}")
        return result
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        result = ValidationResult(valid=False)
        result.add_error(f"Invalid JSON: {exc}")
        return result
    if not isinstance(data, dict):
        result = ValidationResult(valid=False)
        result.add_error("Manifest must be a JSON object")
        return result
    return validate_depot_manifest(data)


# ── CLI helper ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m cascadia.depot.manifest_validator <manifest.json>")
        sys.exit(1)
    result = validate_depot_manifest_file(sys.argv[1])
    if result.valid:
        print("PASS: DEPOT manifest is valid.")
    else:
        print("FAIL: DEPOT manifest validation errors:")
        for err in result.errors:
            print(f"  ERROR: {err}")
    for warn in result.warnings:
        print(f"  WARN:  {warn}")
    sys.exit(0 if result.valid else 1)
