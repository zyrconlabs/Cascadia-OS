# MATURITY: PRODUCTION — Detects missing operators and permissions.
from __future__ import annotations

from typing import Any, Dict, Iterable, Set

from cascadia.durability.run_store import RunStore
from cascadia.shared.manifest_schema import Manifest


class DependencyManager:
    """Owns dependency and permission blocking checks. Does not own installation or remediation."""

    def __init__(self, run_store: RunStore) -> None:
        self.run_store = run_store

    def check(self, run_id: str, manifest: Manifest, installed_assets: Iterable[str], granted_permissions: Iterable[str]) -> Dict[str, Any] | None:
        """Owns missing dependency detection. Does not own retries, install, or user prompting."""
        installed: Set[str] = set(installed_assets)
        permissions: Set[str] = set(granted_permissions)
        for dependency in manifest.required_dependencies:
            if dependency not in installed:
                payload = {'type': 'missing_operator', 'entity': dependency, 'human_message': f'{manifest.name} requires {dependency} to be installed and healthy.'}
                self.run_store.set_blocked(run_id, 'missing_operator', dependency, payload)
                return payload
        for scope in manifest.requested_permissions:
            if scope not in permissions:
                payload = {'type': 'missing_permission', 'entity': scope, 'human_message': f'{manifest.name} requires permission {scope}.'}
                self.run_store.set_blocked(run_id, 'missing_permission', scope, payload)
                return payload
        self.run_store.clear_blocked(run_id)
        return None
