#!/usr/bin/env python3
"""
validate_manifest.py — Cascadia OS SDK v0.47
Operator manifest validator. Checks manifest.json, start_cmd, syntax, secrets, and network declarations.

Usage:
    python sdk/validator/validate_manifest.py path/to/operator/
    python sdk/validator/validate_manifest.py sdk/operator_template/
"""
from __future__ import annotations

import ast
import json
import py_compile
import re
import sys
from pathlib import Path
from typing import List, Tuple

REQUIRED_FIELDS = {
    'id', 'name', 'version', 'type', 'autonomy_level',
    'capabilities', 'port', 'start_cmd', 'health_hook',
}

VALID_TYPES = {'system', 'service', 'skill', 'composite'}
VALID_AUTONOMY = {'manual_only', 'assistive', 'semi_autonomous', 'autonomous'}

SECRET_PATTERNS = [
    re.compile(r'(?i)(password|secret|api_key|token|passwd)\s*=\s*["\'][^"\']{6,}["\']'),
    re.compile(r'(?i)bearer\s+[a-z0-9_\-]{16,}', re.IGNORECASE),
    re.compile(r'sk-[a-zA-Z0-9]{20,}'),  # OpenAI-style key
]

NETWORK_IMPORTS = {'urllib', 'requests', 'httpx', 'aiohttp', 'socket', 'http.client'}


def _check(condition: bool, message: str) -> Tuple[bool, str]:
    return condition, message


def validate(operator_dir: Path) -> List[str]:
    """
    Validate an operator directory. Returns list of issues (empty = pass).
    """
    issues: List[str] = []

    # 1. manifest.json must exist and be valid JSON
    manifest_path = operator_dir / 'manifest.json'
    if not manifest_path.exists():
        issues.append('FAIL: manifest.json not found')
        return issues

    try:
        data = json.loads(manifest_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        issues.append(f'FAIL: manifest.json is not valid JSON: {e}')
        return issues

    # 2. Try cascadia.shared.manifest_schema if available
    schema_ok = False
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from cascadia.shared.manifest_schema import validate_manifest as _schema_validate
        _schema_validate(data)
        schema_ok = True
    except ImportError:
        pass  # manifest_schema not available — fall through to basic check
    except Exception as e:
        issues.append(f'FAIL: manifest_schema validation: {e}')

    if not schema_ok:
        # Basic field checking
        missing = REQUIRED_FIELDS - set(data.keys())
        if missing:
            issues.append(f'FAIL: Missing required fields: {sorted(missing)}')

        op_type = data.get('type', '')
        if op_type and op_type not in VALID_TYPES:
            issues.append(f'FAIL: Invalid type {op_type!r}. Must be one of {sorted(VALID_TYPES)}')

        autonomy = data.get('autonomy_level', '')
        if autonomy and autonomy not in VALID_AUTONOMY:
            issues.append(f'FAIL: Invalid autonomy_level {autonomy!r}. Must be one of {sorted(VALID_AUTONOMY)}')

        op_id = data.get('id', '')
        if op_id and (not op_id.islower() or '-' in op_id):
            issues.append(f'FAIL: id must be lowercase with underscores (no hyphens). Got: {op_id!r}')

        for field in ('capabilities', 'required_dependencies', 'requested_permissions'):
            if field in data and not isinstance(data[field], list):
                issues.append(f'FAIL: {field} must be a list')

    # 3. start_cmd script must exist
    start_cmd = data.get('start_cmd', '')
    if start_cmd:
        script_path = operator_dir / start_cmd.replace('python3 ', '').strip()
        if not script_path.exists():
            issues.append(f'FAIL: start_cmd {start_cmd!r} not found at {script_path}')
        else:
            # 4. Python syntax check
            if script_path.suffix == '.py':
                try:
                    py_compile.compile(str(script_path), doraise=True)
                except py_compile.PyCompileError as e:
                    issues.append(f'FAIL: Syntax error in {script_path.name}: {e}')

            # 5. Scan for hardcoded secrets
            try:
                source = script_path.read_text(encoding='utf-8', errors='replace')
                for pattern in SECRET_PATTERNS:
                    matches = pattern.findall(source)
                    if matches:
                        issues.append(f'WARN: Possible hardcoded secret in {script_path.name}: {matches[0]!r}')
                        break
            except Exception:
                pass

            # 6. Check network_access declaration vs actual network imports
            network_declared = bool(data.get('network_access', False))
            if script_path.suffix == '.py':
                try:
                    source = script_path.read_text(encoding='utf-8', errors='replace')
                    found_network = any(imp in source for imp in NETWORK_IMPORTS)
                    if found_network and not network_declared:
                        issues.append(
                            f'WARN: {script_path.name} imports network libraries but manifest has network_access=false. '
                            f'Set "network_access": true if this operator makes external calls.'
                        )
                except Exception:
                    pass

    # 7. requirements.txt presence
    req_path = operator_dir / 'requirements.txt'
    if not req_path.exists():
        issues.append('WARN: requirements.txt not found. Add one even if empty for clarity.')

    return issues


def main() -> int:
    if len(sys.argv) < 2:
        print('Usage: python validate_manifest.py <operator_directory>')
        return 2

    operator_dir = Path(sys.argv[1]).resolve()
    if not operator_dir.is_dir():
        print(f'ERROR: {operator_dir} is not a directory')
        return 2

    print(f'Validating: {operator_dir}')
    issues = validate(operator_dir)

    failures = [i for i in issues if i.startswith('FAIL')]
    warnings = [i for i in issues if i.startswith('WARN')]

    for issue in issues:
        prefix = 'PASS' if not issue.startswith(('FAIL', 'WARN')) else ''
        print(f'  {issue}')

    if warnings:
        print(f'\n{len(warnings)} warning(s)')
    if failures:
        print(f'\n{len(failures)} error(s) — operator is NOT valid for DEPOT submission')
        return 1
    else:
        print('\nPASS: Operator manifest is valid.')
        return 0


if __name__ == '__main__':
    sys.exit(main())
