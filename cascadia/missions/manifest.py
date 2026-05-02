"""Mission manifest loader and validator."""
from __future__ import annotations

import json
from pathlib import Path

_VALID_TIERS = {"free", "pro", "business", "enterprise"}


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

        # Rule 6: tier_required — one of: free, pro, business, enterprise
        if manifest.get("tier_required") not in _VALID_TIERS:
            errors.append(
                f"'tier_required' must be one of {sorted(_VALID_TIERS)}, "
                f"got: {manifest.get('tier_required')!r}"
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
