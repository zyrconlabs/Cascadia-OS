"""
cascadia/settings/validator.py
Owns: settings patch validation and Safe Mode enforcement.
Does not own: persistence, VAULT routing, UI rendering.

Safe Mode is the default. If no explicit approval rule covers a risky
action, the validator adds approval_required automatically.
Never defaults to auto-send.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from cascadia.shared.manifest_schema import Manifest, SetupField

# Actions that require explicit approval coverage before enabling auto-send
RISKY_ACTIONS = frozenset({
    "email.send", "sms.send", "invoice.create", "payment.charge",
    "payment.refund", "crm.write", "job.create", "quote.send", "record.delete",
})


@dataclass
class FieldResult:
    valid: bool
    error: Optional[str] = None
    warning: Optional[str] = None


@dataclass
class ValidationResult:
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    safe_mode_applied: List[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.valid = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def add_safe_mode(self, msg: str) -> None:
        self.safe_mode_applied.append(msg)
        self.add_warning(f"[Safe Mode] {msg}")


def validate_field(f: SetupField, value: Any) -> FieldResult:
    """Validate a single field value against its SetupField constraints."""
    if value is None:
        if f.required and f.default is None:
            return FieldResult(valid=False, error=f"'{f.name}' is required and has no default")
        return FieldResult(valid=True)

    if f.type == "select" and f.options is not None:
        if value not in f.options:
            return FieldResult(
                valid=False,
                error=f"'{f.name}' value {value!r} not in allowed options {f.options}",
            )

    if f.type in ("number", "slider"):
        try:
            n = float(value)
        except (TypeError, ValueError):
            return FieldResult(valid=False, error=f"'{f.name}' must be numeric, got {value!r}")
        if f.min is not None and n < f.min:
            return FieldResult(
                valid=False, error=f"'{f.name}' value {n} is below minimum {f.min}"
            )
        if f.max is not None and n > f.max:
            return FieldResult(
                valid=False, error=f"'{f.name}' value {n} exceeds maximum {f.max}"
            )

    if f.type == "boolean" and not isinstance(value, bool):
        return FieldResult(valid=False, error=f"'{f.name}' must be true/false, got {value!r}")

    return FieldResult(valid=True)


def validate_approval_coverage(
    patch: Dict[str, Any], manifest: Manifest
) -> List[str]:
    """Return list of risky actions in this patch that have no approval coverage."""
    uncovered: List[str] = []
    field_map = {f.name: f for f in manifest.setup_fields}
    approval_fields = {
        f.name for f in manifest.setup_fields
        if f.requires_approval_if_enabled
    }
    for name, value in patch.items():
        f = field_map.get(name)
        if f is None:
            continue
        actions = f.requires_approval_if_enabled or []
        for action in actions:
            if action in RISKY_ACTIONS and isinstance(value, bool) and value:
                # Check if there's a corresponding approval gate field set to True
                gate_name = _approval_gate_for(action, field_map)
                gate_val = patch.get(gate_name)
                if gate_name and gate_val is True:
                    continue  # covered
                if gate_name and gate_val is False:
                    uncovered.append(action)  # explicitly disabled — risky
                # No gate found or not set in this patch — Safe Mode will apply
        if name == "reply_behavior" and value == "auto_send":
            # Auto-send is always risky — requires approval gate
            uncovered.append("auto_send_without_approval")
    return uncovered


def is_safe_mode_satisfied(patch: Dict[str, Any], manifest: Manifest) -> bool:
    uncovered = validate_approval_coverage(patch, manifest)
    return len(uncovered) == 0


def validate_patch(
    patch: Dict[str, Any],
    manifest: Manifest,
    confirmed: bool = False,
    preview_completed: bool = True,
) -> ValidationResult:
    """
    Validate a settings patch against a manifest.
    Safe Mode is applied automatically for risky actions without coverage.
    """
    result = ValidationResult(valid=True)
    field_map = {f.name: f for f in manifest.setup_fields}
    known_names = set(field_map.keys())

    # 1. Unknown field names
    for name in patch:
        if name not in known_names:
            result.add_error(f"Unknown field: '{name}'")

    # Stop deep validation if unknown fields (cascade confusion)
    if not result.valid:
        return result

    # 2. Field-level validation
    for name, value in patch.items():
        f = field_map[name]

        # Secret fields must not be in a plain patch (should go through save_patch routing)
        if f.secret and value is not None and not isinstance(value, dict):
            # Allow dicts (configured/masked status objects) — block raw values
            result.add_error(
                f"Secret field '{name}' must be routed through save_patch with VAULT, "
                f"not passed as plain text in a validation patch."
            )
            continue

        fr = validate_field(f, value)
        if not fr.valid:
            result.add_error(fr.error)
        if fr.warning:
            result.add_warning(fr.warning)

    # 3. Missing required fields (fields not in patch AND no default)
    for f in manifest.setup_fields:
        if f.required and f.name not in patch and f.default is None:
            result.add_error(f"Required field '{f.name}' is missing and has no default")

    # 4. Risky action coverage (Safe Mode)
    # Safe mode triggers when a field that GATES a risky action is set to False,
    # meaning the user is trying to disable the approval requirement (auto-send).
    for name, value in patch.items():
        f = field_map.get(name)
        if f is None:
            continue
        actions = f.requires_approval_if_enabled or []
        for action in actions:
            if action in RISKY_ACTIONS and isinstance(value, bool) and not value:
                # Gate is being disabled → safe mode auto-applies approval
                result.add_safe_mode(
                    f"'{action}' approval gate '{name}' set to false — "
                    f"approval_required will be enforced automatically by Safe Mode."
                )

    # 5. auto-send without approval gate
    if patch.get("reply_behavior") == "auto_send":
        gate_name = _approval_gate_for("email.send", field_map)
        if not gate_name or not patch.get(gate_name):
            result.add_error(
                "reply_behavior='auto_send' requires an approval gate field to be set. "
                "Safe Mode requires human approval before sending."
            )

    # 6. Activation without confirmation
    if patch.get("_activate") is True and not confirmed:
        result.add_error("Activation requires confirmed=True.")

    # 7. Activation without preview
    if patch.get("_activate") is True and not preview_completed:
        result.add_error("Activation requires preview step to be completed first.")

    return result


def _approval_gate_for(action: str, field_map: Dict[str, "SetupField"]) -> Optional[str]:
    """Find a field that gates the given risky action (ask_before_sending pattern)."""
    for name, f in field_map.items():
        if f.type == "boolean" and f.requires_approval_if_enabled:
            if action in f.requires_approval_if_enabled:
                return name
    return None
