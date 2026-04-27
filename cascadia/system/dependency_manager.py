# MATURITY: PRODUCTION — Detects missing operators and permissions.
from __future__ import annotations

from typing import Any, Dict, Iterable, Set

from cascadia.durability.run_store import RunStore
from cascadia.shared.manifest_schema import Manifest

# Map social permissions to the manifest file that grants them so the blocked
# message tells the operator exactly what to install rather than stalling silently.
_PERMISSION_TO_MANIFEST: Dict[str, str] = {
    'social.x.post':         'publisher_x_operator.json',
    'social.instagram.post': 'publisher_instagram_operator.json',
    'social.facebook.post':  'publisher_facebook_operator.json',
}


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
                payload = {
                    'type': 'missing_operator',
                    'entity': dependency,
                    'human_message': (
                        f'{manifest.name} requires operator "{dependency}" to be installed and healthy. '
                        f'Ensure cascadia/operators/{dependency}.json exists and the operator is registered.'
                    ),
                }
                self.run_store.set_blocked(run_id, 'missing_operator', dependency, payload)
                return payload
        for scope in manifest.requested_permissions:
            if scope not in permissions:
                manifest_hint = _PERMISSION_TO_MANIFEST.get(scope)
                hint = (
                    f' Deploy cascadia/operators/{manifest_hint} and restart to grant it.'
                    if manifest_hint
                    else ' Check your operator manifests to ensure the relevant operator is installed.'
                )
                payload = {
                    'type': 'missing_permission',
                    'entity': scope,
                    'human_message': (
                        f'{manifest.name} requires permission "{scope}" which is not currently granted.{hint}'
                    ),
                }
                self.run_store.set_blocked(run_id, 'missing_permission', scope, payload)
                return payload
        self.run_store.clear_blocked(run_id)
        return None
