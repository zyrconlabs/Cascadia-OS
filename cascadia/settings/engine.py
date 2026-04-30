"""
cascadia/settings/engine.py
Owns: single backend for all configuration.
Routes non-secret settings to store.py, secret settings to VaultStore.
Does not own: HTTP routing, validation beyond field routing, UI rendering.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from cascadia.memory.vault import VaultStore
from cascadia.settings.store import SettingsStore
from cascadia.shared.manifest_schema import Manifest, SetupField

_MASK = "••••••••"
_DEFAULT_SETTINGS_DB = "data/settings.db"
_DEFAULT_VAULT_DB = "data/runtime/cascadia_vault.db"

VALID_TARGET_TYPES = {
    "mission", "operator", "connector", "approval_policy", "business_profile"
}


class SettingsEngine:
    """
    Single backend for all configuration.
    Routes to store (non-secret) or VAULT (secret) based on field.secret flag.
    Never returns raw secret values.
    """

    def __init__(
        self,
        settings_db: str = _DEFAULT_SETTINGS_DB,
        vault_db: Optional[str] = None,
    ) -> None:
        self._store = SettingsStore(settings_db)
        self._vault = VaultStore(vault_db or _DEFAULT_VAULT_DB)

    # ── Read ─────────────────────────────────────────────────────────────────

    def get_settings(
        self,
        target_type: str,
        target_id: str,
        manifest: Optional[Manifest] = None,
    ) -> Dict[str, Any]:
        """Return current settings merged with defaults. Secret fields return masked status."""
        saved = self._store.get_all_settings(target_type, target_id)
        if manifest is None:
            return saved
        defaults = self._store.get_defaults(target_type, target_id, manifest)
        result: Dict[str, Any] = {**defaults, **saved}
        # Layer in secret field status (never raw values)
        for f in manifest.setup_fields:
            if f.secret and f.vault_key:
                configured = self._vault.read(f.vault_key, namespace="secrets") is not None
                result[f.name] = {"configured": configured, "masked": _MASK if configured else None}
        return result

    def get_effective_settings(
        self,
        target_type: str,
        target_id: str,
        manifest: Optional[Manifest] = None,
    ) -> Dict[str, Any]:
        """Same as get_settings but includes validation state per field."""
        settings = self.get_settings(target_type, target_id, manifest)
        if manifest is None:
            return settings
        validation: Dict[str, Any] = {}
        for f in manifest.setup_fields:
            val = settings.get(f.name)
            if f.required and (val is None or val == ""):
                validation[f.name] = {"valid": False, "error": "Required field has no value"}
            else:
                validation[f.name] = {"valid": True}
        return {"settings": settings, "validation": validation}

    def get_defaults(
        self, target_type: str, target_id: str, manifest: Manifest
    ) -> Dict[str, Any]:
        return self._store.get_defaults(target_type, target_id, manifest)

    # ── Patch preview ─────────────────────────────────────────────────────────

    def build_patch(
        self,
        target_type: str,
        target_id: str,
        changes: Dict[str, Any],
        manifest: Optional[Manifest] = None,
    ) -> Dict[str, Any]:
        """Build a diff showing before/after for every changed field."""
        current = self.get_settings(target_type, target_id, manifest)
        field_map: Dict[str, SetupField] = (
            {f.name: f for f in manifest.setup_fields} if manifest else {}
        )
        diffs: List[Dict[str, Any]] = []
        for name, after in changes.items():
            before = current.get(name)
            f = field_map.get(name)
            diffs.append({
                "field": name,
                "before": before,
                "after": after,
                "secret": getattr(f, "secret", False),
                "requires_approval": bool(
                    f and f.requires_approval_if_enabled and
                    isinstance(after, bool) and after
                ),
            })
        return {
            "target_type": target_type,
            "target_id": target_id,
            "changes": diffs,
        }

    def preview_patch(
        self,
        target_type: str,
        target_id: str,
        changes: Dict[str, Any],
        manifest: Optional[Manifest] = None,
    ) -> Dict[str, Any]:
        """Human-readable preview of what a patch will do."""
        patch = self.build_patch(target_type, target_id, changes, manifest)
        will_do: List[str] = []
        will_not_do: List[str] = []
        approval_required: List[str] = []
        for diff in patch["changes"]:
            name = diff["field"]
            before = diff["before"]
            after = diff["after"]
            if diff["secret"]:
                will_do.append(f"Update secret '{name}' in VAULT")
            elif before != after:
                will_do.append(f"Set '{name}': {before!r} → {after!r}")
            else:
                will_not_do.append(f"'{name}' unchanged (already {after!r})")
            if diff["requires_approval"]:
                approval_required.append(name)
        return {
            "will_do": will_do,
            "will_not_do": will_not_do,
            "approval_required": approval_required,
            "settings_changes": patch["changes"],
        }

    def validate_patch(
        self,
        changes: Dict[str, Any],
        manifest: Optional[Manifest] = None,
    ) -> Dict[str, Any]:
        errors: List[str] = []
        warnings: List[str] = []
        if manifest:
            field_names = {f.name for f in manifest.setup_fields}
            for name in changes:
                if name not in field_names:
                    errors.append(f"Unknown field: '{name}'")
            for f in manifest.setup_fields:
                if f.name in changes:
                    val = changes[f.name]
                    err = _validate_field_value(f, val)
                    if err:
                        errors.append(err)
        return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}

    # ── Save ──────────────────────────────────────────────────────────────────

    def save_patch(
        self,
        target_type: str,
        target_id: str,
        changes: Dict[str, Any],
        confirmed: bool,
        source: str,
        manifest: Optional[Manifest] = None,
    ) -> Dict[str, Any]:
        """Save settings. confirmed=False returns preview only; confirmed=True persists."""
        preview = self.preview_patch(target_type, target_id, changes, manifest)
        if not confirmed:
            return {**preview, "saved": False, "message": "Pass confirmed=true to save."}
        field_map: Dict[str, SetupField] = (
            {f.name: f for f in manifest.setup_fields} if manifest else {}
        )
        non_secret: Dict[str, Any] = {}
        for name, value in changes.items():
            f = field_map.get(name)
            if f and f.secret:
                # Route secrets to VAULT
                if f.vault_key:
                    self._vault.write(f.vault_key, value, created_by=source, namespace="secrets")
            else:
                non_secret[name] = value
        if non_secret:
            self._store.set_many_settings(target_type, target_id, non_secret, source, manifest)
        return {**preview, "saved": True}

    # ── Reset ─────────────────────────────────────────────────────────────────

    def reset_settings(
        self,
        target_type: str,
        target_id: str,
        manifest: Manifest,
        mode: str = "recommended",
        confirmed: bool = False,
        source: str = "reset",
    ) -> Dict[str, Any]:
        """Build reset preview; apply only if confirmed=True."""
        defaults = self._store.get_defaults(target_type, target_id, manifest)
        preview = self.preview_patch(target_type, target_id, defaults, manifest)
        if not confirmed:
            return {**preview, "reset": False, "mode": mode,
                    "message": "Pass confirmed=true to apply reset."}
        self._store.reset_to_defaults(target_type, target_id, manifest, source)
        return {**preview, "reset": True, "mode": mode}

    # ── Health test ───────────────────────────────────────────────────────────

    def test_settings(
        self,
        target_type: str,
        target_id: str,
        manifest: Optional[Manifest] = None,
    ) -> Dict[str, Any]:
        """Run a basic health check with current settings."""
        settings = self.get_settings(target_type, target_id, manifest)
        missing_required: List[str] = []
        if manifest:
            for f in manifest.setup_fields:
                if f.required and not f.secret:
                    val = settings.get(f.name)
                    if val is None or val == "":
                        missing_required.append(f.name)
        success = len(missing_required) == 0
        return {
            "success": success,
            "message": "Settings valid." if success else f"Missing required fields: {missing_required}",
            "details": {"missing_required": missing_required, "settings_count": len(settings)},
        }


def _validate_field_value(field: SetupField, value: Any) -> Optional[str]:
    """Return an error string or None if value is acceptable for the field."""
    if field.type == "select" and field.options and value not in field.options:
        return f"'{field.name}' value {value!r} not in options {field.options}"
    if field.type in ("number", "slider") and value is not None:
        try:
            n = float(value)
        except (TypeError, ValueError):
            return f"'{field.name}' must be numeric"
        if field.min is not None and n < field.min:
            return f"'{field.name}' value {n} is below minimum {field.min}"
        if field.max is not None and n > field.max:
            return f"'{field.name}' value {n} exceeds maximum {field.max}"
    if field.type == "boolean" and value is not None and not isinstance(value, bool):
        return f"'{field.name}' must be a boolean"
    return None
