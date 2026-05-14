"""Tests for cascadia.depot.kill_switch — KillSwitchProvider protocol,
NoopKillSwitchProvider, and InMemoryKillSwitchProvider."""
from __future__ import annotations

import unittest

from cascadia.depot.kill_switch import (
    InMemoryKillSwitchProvider,
    KillSwitchProvider,
    NoopKillSwitchProvider,
)


class TestNoopKillSwitchProvider(unittest.TestCase):

    def setUp(self):
        self.provider = NoopKillSwitchProvider()

    def test_never_revoked(self):
        self.assertFalse(self.provider.is_revoked("any-package", "1.0.0"))

    def test_never_revoked_any_version(self):
        for v in ("0.0.1", "1.0.0", "999.999.999"):
            self.assertFalse(self.provider.is_revoked("lead-qualification", v))

    def test_satisfies_protocol(self):
        # Structural subtyping check — NoopKillSwitchProvider satisfies KillSwitchProvider
        provider: KillSwitchProvider = NoopKillSwitchProvider()
        self.assertFalse(provider.is_revoked("pkg", "1.0.0"))


class TestInMemoryKillSwitchProvider(unittest.TestCase):

    def setUp(self):
        self.provider = InMemoryKillSwitchProvider()

    def test_not_revoked_by_default(self):
        self.assertFalse(self.provider.is_revoked("lead-qualification", "1.0.0"))

    def test_revoke_specific_version(self):
        self.provider.revoke("lead-qualification", "1.0.0")
        self.assertTrue(self.provider.is_revoked("lead-qualification", "1.0.0"))

    def test_revoked_version_does_not_affect_others(self):
        self.provider.revoke("lead-qualification", "1.0.0")
        self.assertFalse(self.provider.is_revoked("lead-qualification", "1.1.0"))
        self.assertFalse(self.provider.is_revoked("other-package", "1.0.0"))

    def test_revoke_all_versions(self):
        self.provider.revoke_all_versions("compromised-package")
        self.assertTrue(self.provider.is_revoked("compromised-package", "1.0.0"))
        self.assertTrue(self.provider.is_revoked("compromised-package", "2.0.0"))
        self.assertTrue(self.provider.is_revoked("compromised-package", "0.0.1"))

    def test_all_versions_wildcard_does_not_affect_other_packages(self):
        self.provider.revoke_all_versions("compromised-package")
        self.assertFalse(self.provider.is_revoked("safe-package", "1.0.0"))

    def test_multiple_packages_independently_revoked(self):
        self.provider.revoke("pkg-a", "1.0.0")
        self.provider.revoke("pkg-b", "2.0.0")
        self.assertTrue(self.provider.is_revoked("pkg-a", "1.0.0"))
        self.assertFalse(self.provider.is_revoked("pkg-a", "2.0.0"))
        self.assertFalse(self.provider.is_revoked("pkg-b", "1.0.0"))
        self.assertTrue(self.provider.is_revoked("pkg-b", "2.0.0"))

    def test_satisfies_protocol(self):
        provider: KillSwitchProvider = InMemoryKillSwitchProvider()
        self.assertFalse(provider.is_revoked("pkg", "1.0.0"))
        provider.revoke("pkg", "1.0.0")  # type: ignore[attr-defined]
        self.assertTrue(provider.is_revoked("pkg", "1.0.0"))


if __name__ == "__main__":
    unittest.main()
