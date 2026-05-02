"""Mission package registry — discovers and catalogs installed missions."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from cascadia.missions.manifest import MissionManifest, MissionManifestError

log = logging.getLogger(__name__)


class MissionRegistry:

    def __init__(self, packages_root=None, registry_file=None):
        """
        packages_root resolution order:
          1. packages_root argument if passed
          2. config.json missions.packages_root
          3. CASCADIA_MISSIONS_ROOT environment variable
          4. ./missions relative to working directory
          5. None — empty catalog, no crash ever

        registry_file defaults to:
          cascadia/missions/missions_registry.json
          relative to this file's location
        """
        self._packages_root = self._resolve_root(packages_root)
        self._registry_file = (
            Path(registry_file)
            if registry_file
            else Path(__file__).parent / "missions_registry.json"
        )
        self._catalog = self.discover()

    # ── Discovery ─────────────────────────────────────────────────────────────

    def discover(self) -> list:
        """Scan packages_root for subdirs containing mission.json.
        Load and validate each manifest. Skip invalid ones with a warning.
        Return list of valid manifest dicts with _base_path added.
        Return empty list if packages_root is None or missing."""
        if self._packages_root is None:
            return []
        root = Path(self._packages_root)
        if not root.exists():
            return []

        mm = MissionManifest()
        catalog = []
        for subdir in sorted(root.iterdir()):
            if not subdir.is_dir():
                continue
            mission_file = subdir / "mission.json"
            if not mission_file.exists():
                continue
            try:
                manifest = mm.load(str(mission_file))
            except MissionManifestError as exc:
                log.warning("skipping %s — load error: %s", subdir.name, exc)
                continue
            errors = mm.validate(manifest, base_path=str(subdir))
            if errors:
                log.warning("skipping %s — validation errors: %s", subdir.name, errors)
                continue
            manifest["_base_path"] = str(subdir)
            catalog.append(manifest)

        return catalog

    # ── Catalog ───────────────────────────────────────────────────────────────

    def list_catalog(self) -> list:
        """Return all discovered missions.
        Each entry includes installed: bool from list_installed."""
        installed_ids = {m["id"] for m in self.list_installed() if isinstance(m, dict)}
        result = []
        for m in self._catalog:
            entry = dict(m)
            entry["installed"] = entry.get("id") in installed_ids
            result.append(entry)
        return result

    def list_installed(self) -> list:
        """Read missions_registry.json installed array.
        Return empty list if file missing, empty, or malformed. Never crash."""
        try:
            if not self._registry_file.exists():
                return []
            data = json.loads(self._registry_file.read_text(encoding="utf-8"))
            installed = data.get("installed", [])
            return installed if isinstance(installed, list) else []
        except Exception:
            return []

    # ── Lookup ────────────────────────────────────────────────────────────────

    def get_mission(self, mission_id: str):
        """Return manifest dict for given id. Return None if not found."""
        for m in self._catalog:
            if m.get("id") == mission_id:
                return m
        return None

    def get_manifest(self, mission_id: str):
        """Alias for get_mission."""
        return self.get_mission(mission_id)

    def get_mobile_schema_path(self, mission_id: str):
        """Return absolute path to the mobile schema file.
        Resolve mobile.schema value relative to _base_path.
        Return None if mission not found."""
        mission = self.get_mission(mission_id)
        if not mission:
            return None
        schema_rel = (mission.get("mobile") or {}).get("schema")
        if not schema_rel:
            return None
        return str(Path(mission["_base_path"]) / schema_rel)

    def get_prism_schema_path(self, mission_id: str):
        """Return absolute path to the prism schema file.
        Resolve prism.schema value relative to _base_path.
        Return None if mission not found."""
        mission = self.get_mission(mission_id)
        if not mission:
            return None
        schema_rel = (mission.get("prism") or {}).get("schema")
        if not schema_rel:
            return None
        return str(Path(mission["_base_path"]) / schema_rel)

    def get_workflow_path(self, mission_id: str, workflow_id: str):
        """Return absolute path to workflow JSON file.
        Resolve workflows[workflow_id] relative to _base_path.
        Return None if mission or workflow not found."""
        mission = self.get_mission(mission_id)
        if not mission:
            return None
        rel_path = (mission.get("workflows") or {}).get(workflow_id)
        if rel_path is None:
            return None
        return str(Path(mission["_base_path"]) / rel_path)

    def get_declared_events(self, mission_id: str):
        """Return events block from manifest. None if not found."""
        mission = self.get_mission(mission_id)
        if not mission:
            return None
        return mission.get("events")

    def validate_manifest(self, manifest: dict, base_path=None) -> list:
        """Delegate to MissionManifest.validate()."""
        return MissionManifest().validate(manifest, base_path)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _resolve_root(self, packages_root) -> str | None:
        if packages_root is not None:
            return str(packages_root) if packages_root else None

        # config.json missions.packages_root
        try:
            config_path = Path(__file__).parent.parent.parent / "config.json"
            if config_path.exists():
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
                if isinstance(cfg.get("missions"), dict):
                    root = cfg["missions"].get("packages_root")
                    if root:
                        return str(root)
        except Exception:
            pass

        # CASCADIA_MISSIONS_ROOT environment variable
        env_val = os.environ.get("CASCADIA_MISSIONS_ROOT")
        if env_val:
            return env_val

        # ./missions relative to working directory
        default = Path("./missions")
        if default.exists():
            return str(default)

        return None
