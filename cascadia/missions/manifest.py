"""Mission manifest loader and validator."""
from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

_VALID_TIERS = {"lite", "pro", "business", "enterprise"}
_DEPRECATED_TIER_ALIASES = {"free": "lite"}

_VALID_RUNTIMES = {"server", "mobile", "both"}
_VALID_RISK_LEVELS = {"low", "medium", "high", "critical"}
_RISK_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}

# Pattern for operator/connector IDs with optional version pinning:
# "scout", "scout@1.2.0", "scout@>=1.2.0"
_DEPENDENCY_ID_RE = re.compile(
    r'^[a-z][a-z0-9_-]+(@(>=)?[0-9]+\.[0-9]+\.[0-9]+)?$'
)


class MissionManifestError(Exception):
    pass


class MissionManifest:

    def load(self, path: str) -> dict:
        """Read mission.json from path. Return parsed dict.
        Raise MissionManifestError if file missing or invalid JSON."""
        p = Path(path)
        if not p.exists():
            raise MissionManifestError(f"Mission file not found: {path}")
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise MissionManifestError(f"Invalid JSON in {path}: {exc}") from exc

    def validate(self, manifest: dict, base_path: str = None) -> list:
        """Validate manifest against all rules.
        Return list of error strings. Empty list means valid.
        base_path is the directory containing mission.json — used to
        verify that referenced files actually exist."""
        errors: list[str] = []

        # Rule 1: type == "mission"
        if manifest.get("type") != "mission":
            errors.append(f"'type' must be 'mission', got: {manifest.get('type')!r}")

        # Rule 2: id — non-empty string
        id_val = manifest.get("id")
        if not isinstance(id_val, str) or not id_val.strip():
            errors.append("'id' must be a non-empty string")

        # Rule 3: name — non-empty string
        if not isinstance(manifest.get("name"), str) or not manifest.get("name", "").strip():
            errors.append("'name' must be a non-empty string")

        # Rule 4: version — non-empty string
        if not isinstance(manifest.get("version"), str) or not manifest.get("version", "").strip():
            errors.append("'version' must be a non-empty string")

        # Rule 5: description — non-empty string
        if not isinstance(manifest.get("description"), str) or not manifest.get("description", "").strip():
            errors.append("'description' must be a non-empty string")

        # Rule 6: tier_required — one of: lite, pro, business, enterprise
        # "free" is accepted as a deprecated alias for "lite".
        tier = manifest.get("tier_required")
        if tier in _DEPRECATED_TIER_ALIASES:
            warnings.warn(
                f"tier_required value {tier!r} is deprecated; "
                f"use {_DEPRECATED_TIER_ALIASES[tier]!r} instead",
                DeprecationWarning,
                stacklevel=2,
            )
        elif tier not in _VALID_TIERS:
            errors.append(
                f"'tier_required' must be one of {sorted(_VALID_TIERS)}, "
                f"got: {tier!r}"
            )

        # Rule 7: industries — list (may be empty)
        if not isinstance(manifest.get("industries"), list):
            errors.append("'industries' must be a list")

        # Rule 8: operators — dict with required and optional lists
        operators = manifest.get("operators")
        if not isinstance(operators, dict):
            errors.append("'operators' must be a dict")
        else:
            if not isinstance(operators.get("required"), list):
                errors.append("'operators.required' must be a list")
            if not isinstance(operators.get("optional"), list):
                errors.append("'operators.optional' must be a list")

        # Rule 9: connectors — dict with required and optional lists
        connectors = manifest.get("connectors")
        if not isinstance(connectors, dict):
            errors.append("'connectors' must be a dict")
        else:
            if not isinstance(connectors.get("required"), list):
                errors.append("'connectors.required' must be a list")
            if not isinstance(connectors.get("optional"), list):
                errors.append("'connectors.optional' must be a list")

        # Rule 10: schedules — list
        if not isinstance(manifest.get("schedules"), list):
            errors.append("'schedules' must be a list")

        # Rule 11: approval_flows — list
        if not isinstance(manifest.get("approval_flows"), list):
            errors.append("'approval_flows' must be a list")

        # Rule 12: database — dict with schema_file string and owned_tables list
        database = manifest.get("database")
        if not isinstance(database, dict):
            errors.append("'database' must be a dict")
        else:
            if not isinstance(database.get("schema_file"), str):
                errors.append("'database.schema_file' must be a string")
            if not isinstance(database.get("owned_tables"), list):
                errors.append("'database.owned_tables' must be a list")

        # Rule 13: workflows — dict
        if not isinstance(manifest.get("workflows"), dict):
            errors.append("'workflows' must be a dict")

        # Rule 14: events — dict with produces and consumes lists
        events = manifest.get("events")
        if not isinstance(events, dict):
            errors.append("'events' must be a dict")
        else:
            if not isinstance(events.get("produces"), list):
                errors.append("'events.produces' must be a list")
            if not isinstance(events.get("consumes"), list):
                errors.append("'events.consumes' must be a list")

        # Rule 15: billing — dict present
        if not isinstance(manifest.get("billing"), dict):
            errors.append("'billing' must be a dict")

        # Rule 16: limits — dict present
        if not isinstance(manifest.get("limits"), dict):
            errors.append("'limits' must be a dict")

        # Rule 17: prism — dict with schema key string
        prism = manifest.get("prism")
        if not isinstance(prism, dict):
            errors.append("'prism' must be a dict")
        else:
            if not isinstance(prism.get("schema"), str) or not prism.get("schema", "").strip():
                errors.append("'prism.schema' must be a non-empty string")

        # Rule 18: mobile — dict with schema key string
        mobile = manifest.get("mobile")
        if not isinstance(mobile, dict):
            errors.append("'mobile' must be a dict")
        else:
            if not isinstance(mobile.get("schema"), str) or not mobile.get("schema", "").strip():
                errors.append("'mobile.schema' must be a non-empty string")

        # Rule 33: operator/connector dependency format (always applied)
        dep_lists: list[tuple[str, object]] = []
        if isinstance(operators, dict):
            dep_lists += [
                ("operators.required", operators.get("required")),
                ("operators.optional", operators.get("optional")),
            ]
        if isinstance(connectors, dict):
            dep_lists += [
                ("connectors.required", connectors.get("required")),
                ("connectors.optional", connectors.get("optional")),
            ]
        for field_name, dep_list in dep_lists:
            if isinstance(dep_list, list):
                for entry in dep_list:
                    if isinstance(entry, str) and not _DEPENDENCY_ID_RE.match(entry):
                        errors.append(
                            f"'{field_name}' contains invalid dependency format: {entry!r}"
                        )

        # Package-mode rules (Rules 22–31) — activated when 'signature' or
        # 'package_digest' is present, indicating a signed mission package.
        _is_package = ('signature' in manifest or 'package_digest' in manifest)
        if _is_package:
            from cascadia.shared.entitlements import CAPABILITY_REGISTRY

            # Rule 22: capabilities — list, each a key in CAPABILITY_REGISTRY
            caps = manifest.get('capabilities')
            if not isinstance(caps, list):
                errors.append("'capabilities' must be a list")
            else:
                for c in caps:
                    if not isinstance(c, str) or c not in CAPABILITY_REGISTRY:
                        errors.append(
                            f"'capabilities' contains unknown capability: {c!r}"
                        )

            # Rule 23: requires_approval — list, subset of capabilities
            req_approval = manifest.get('requires_approval')
            if not isinstance(req_approval, list):
                errors.append("'requires_approval' must be a list")
            elif isinstance(caps, list):
                for c in req_approval:
                    if c not in caps:
                        errors.append(
                            f"'requires_approval' entry {c!r} not in 'capabilities'"
                        )

            # Rule 24: risk_level — enum, must be ≥ highest capability risk level
            risk = manifest.get('risk_level')
            if not isinstance(risk, str) or risk not in _VALID_RISK_LEVELS:
                errors.append(
                    f"'risk_level' must be one of {sorted(_VALID_RISK_LEVELS)}, got: {risk!r}"
                )
            elif isinstance(caps, list):
                cap_levels = [
                    CAPABILITY_REGISTRY.get(c, "medium")
                    for c in caps
                    if isinstance(c, str) and c in CAPABILITY_REGISTRY
                ]
                if cap_levels:
                    max_cap_rank = max(_RISK_RANK[lvl] for lvl in cap_levels)
                    declared_rank = _RISK_RANK.get(risk, 0)
                    if declared_rank < max_cap_rank:
                        max_level = [k for k, v in _RISK_RANK.items() if v == max_cap_rank][0]
                        errors.append(
                            f"'risk_level' is {risk!r} but capabilities require "
                            f"at least {max_level!r}"
                        )

            # Rule 25: runtime — required, one of server/mobile/both
            if manifest.get('runtime') not in _VALID_RUNTIMES:
                errors.append(
                    f"'runtime' must be one of {sorted(_VALID_RUNTIMES)}, "
                    f"got: {manifest.get('runtime')!r}"
                )

            # Rule 26: author — required non-empty string
            if not isinstance(manifest.get('author'), str) or not manifest.get('author', '').strip():
                errors.append("'author' must be a non-empty string")

            # Rule 27: signed_by — required non-empty string
            if not isinstance(manifest.get('signed_by'), str) or not manifest.get('signed_by', '').strip():
                errors.append("'signed_by' must be a non-empty string")

            # Rule 28: signature_algorithm — must be "Ed25519"
            if manifest.get('signature_algorithm') != 'Ed25519':
                errors.append(
                    f"'signature_algorithm' must be 'Ed25519', "
                    f"got: {manifest.get('signature_algorithm')!r}"
                )

            # Rule 29: key_id — non-empty string matching ^[a-z][a-z0-9-]+$
            key_id = manifest.get('key_id')
            if not isinstance(key_id, str) or not re.match(r'^[a-z][a-z0-9-]+$', key_id):
                errors.append(
                    f"'key_id' must be a non-empty string matching ^[a-z][a-z0-9-]+$, "
                    f"got: {key_id!r}"
                )

            # Rule 30: package_digest — must match ^sha256:[a-f0-9]{64}$
            pkg_digest = manifest.get('package_digest')
            if not isinstance(pkg_digest, str) or not re.match(
                r'^sha256:[a-f0-9]{64}$', pkg_digest
            ):
                errors.append(
                    f"'package_digest' must match sha256:<64 hex chars>, "
                    f"got: {pkg_digest!r}"
                )

            # Rule 31: files — list of dicts with valid path and sha256
            files = manifest.get('files')
            if not isinstance(files, list):
                errors.append("'files' must be a list")
            else:
                for i, entry in enumerate(files):
                    if not isinstance(entry, dict):
                        errors.append(f"'files[{i}]' must be a dict")
                        continue
                    path = entry.get('path')
                    sha = entry.get('sha256')
                    if not isinstance(path, str) or not path.strip():
                        errors.append(f"'files[{i}].path' must be a non-empty string")
                    elif '..' in path or path.startswith('/'):
                        errors.append(
                            f"'files[{i}].path' must be a relative POSIX path, got: {path!r}"
                        )
                    if not isinstance(sha, str) or not re.match(r'^[a-f0-9]{64}$', sha):
                        errors.append(
                            f"'files[{i}].sha256' must be 64-char lowercase hex, "
                            f"got: {sha!r}"
                        )
                    size = entry.get('size_bytes')
                    if size is not None and (not isinstance(size, int) or size < 0):
                        errors.append(
                            f"'files[{i}].size_bytes' must be a non-negative integer"
                        )

        # File existence checks — only when base_path is provided
        if base_path is not None:
            base = Path(base_path)

            # Rule 19: mobile.schema file exists
            if isinstance(manifest.get("mobile"), dict):
                schema_rel = manifest["mobile"].get("schema", "")
                if schema_rel:
                    p = base / schema_rel
                    if not p.exists():
                        errors.append(f"mobile.schema file not found: {p}")

            # Rule 20: prism.schema file exists
            if isinstance(manifest.get("prism"), dict):
                schema_rel = manifest["prism"].get("schema", "")
                if schema_rel:
                    p = base / schema_rel
                    if not p.exists():
                        errors.append(f"prism.schema file not found: {p}")

            # Rule 21: each workflow file exists
            if isinstance(manifest.get("workflows"), dict):
                for wf_id, wf_rel in manifest["workflows"].items():
                    p = base / wf_rel
                    if not p.exists():
                        errors.append(
                            f"workflow file not found: {p} (id: {wf_id!r})"
                        )

            # NOTE: data/schema.sql is NOT checked — spec explicitly excludes it

        return errors

    def is_valid(self, manifest: dict, base_path: str = None) -> bool:
        """Return True if validate() returns an empty list."""
        return len(self.validate(manifest, base_path)) == 0
