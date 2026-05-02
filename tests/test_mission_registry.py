"""Tests for cascadia.missions.registry — MissionRegistry."""
from __future__ import annotations

import unittest
from pathlib import Path

from cascadia.missions.registry import MissionRegistry
import cascadia.missions.events as events_module

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "missions"


class TestMissionRegistry(unittest.TestCase):

    def setUp(self):
        self.reg = MissionRegistry(packages_root=str(FIXTURES_ROOT))

    # ── Discovery ─────────────────────────────────────────────────────────────

    def test_registry_discovers_fixture_missions(self):
        missions = self.reg.discover()
        ids = [m["id"] for m in missions]
        self.assertIn("test_growth_desk", ids)
        # test_invalid_mission has an invalid manifest — must be skipped
        self.assertNotIn("bad_mission", ids)

    def test_registry_catalog_returns_all_discovered(self):
        catalog = self.reg.list_catalog()
        self.assertGreaterEqual(len(catalog), 1)
        ids = [m["id"] for m in catalog]
        self.assertIn("test_growth_desk", ids)

    # ── Installed list ────────────────────────────────────────────────────────

    def test_registry_installed_returns_empty_initially(self):
        installed = self.reg.list_installed()
        self.assertIsInstance(installed, list)
        # The default missions_registry.json ships with an empty installed array
        self.assertEqual(installed, [])

    # ── get_mission / get_manifest ────────────────────────────────────────────

    def test_registry_get_mission_returns_manifest(self):
        m = self.reg.get_mission("test_growth_desk")
        self.assertIsNotNone(m)
        self.assertEqual(m["id"], "test_growth_desk")

    def test_registry_get_mission_unknown_returns_none(self):
        self.assertIsNone(self.reg.get_mission("does_not_exist"))

    # ── Workflow paths ────────────────────────────────────────────────────────

    def test_registry_get_workflow_path_returns_correct_path(self):
        path = self.reg.get_workflow_path("test_growth_desk", "daily_campaign")
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).exists(), f"Workflow file not found: {path}")

    def test_registry_get_workflow_path_unknown_returns_none(self):
        self.assertIsNone(
            self.reg.get_workflow_path("test_growth_desk", "nonexistent_workflow")
        )

    # ── Schema paths ──────────────────────────────────────────────────────────

    def test_registry_get_mobile_schema_path_returns_path(self):
        path = self.reg.get_mobile_schema_path("test_growth_desk")
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).exists(), f"Mobile schema not found: {path}")

    def test_registry_get_prism_schema_path_returns_path(self):
        path = self.reg.get_prism_schema_path("test_growth_desk")
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).exists(), f"PRISM schema not found: {path}")

    # ── Events ───────────────────────────────────────────────────────────────

    def test_registry_get_declared_events_returns_events_block(self):
        ev = self.reg.get_declared_events("test_growth_desk")
        self.assertIsNotNone(ev)
        self.assertIn("produces", ev)
        self.assertIn("consumes", ev)
        self.assertIn("campaign.drafted", ev["produces"])
        self.assertIn("schedule.daily", ev["consumes"])

    # ── Edge cases ────────────────────────────────────────────────────────────

    def test_registry_missing_root_returns_empty_no_crash(self):
        reg = MissionRegistry(packages_root="/path/that/does/not/exist/ever")
        self.assertEqual(reg.discover(), [])
        self.assertEqual(reg.list_catalog(), [])
        self.assertIsNone(reg.get_mission("anything"))

    # ── Event constants ───────────────────────────────────────────────────────

    def test_event_constants_are_strings(self):
        import inspect
        members = inspect.getmembers(events_module)
        constants = [
            (name, val) for name, val in members
            if not name.startswith("_") and isinstance(val, str)
        ]
        self.assertGreater(len(constants), 0, "No string constants found in events module")
        for name, val in constants:
            self.assertIsInstance(val, str, f"{name} is not a string")
            self.assertTrue(val, f"{name} is an empty string")


if __name__ == "__main__":
    unittest.main()
