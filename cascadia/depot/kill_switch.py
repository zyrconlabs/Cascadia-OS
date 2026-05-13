"""Package revocation abstraction for mission package installs.

Sprint 2B provides:
  KillSwitchProvider — abstract protocol
  NoopKillSwitchProvider — always returns False (not revoked); production default
  InMemoryKillSwitchProvider — for tests; simulates revoked packages

Cloud/Supabase-backed revocation is deferred to Sprint 5/6.
No SQLite table is created in Sprint 2B.
"""
from __future__ import annotations

from typing import Protocol


class KillSwitchProvider(Protocol):
    """Abstract interface for package revocation checks."""

    def is_revoked(self, package_id: str, version: str) -> bool:
        """Return True if the package version has been revoked."""
        ...


class NoopKillSwitchProvider:
    """Production default — always returns False (no packages revoked)."""

    def is_revoked(self, package_id: str, version: str) -> bool:
        return False


class InMemoryKillSwitchProvider:
    """Test implementation — allows simulating revoked packages."""

    def __init__(self) -> None:
        self._revoked: set[tuple[str, str]] = set()

    def revoke(self, package_id: str, version: str) -> None:
        """Mark a specific package version as revoked."""
        self._revoked.add((package_id, version))

    def revoke_all_versions(self, package_id: str) -> None:
        """Mark all versions of a package as revoked (wildcard)."""
        self._revoked.add((package_id, "*"))

    def is_revoked(self, package_id: str, version: str) -> bool:
        return (package_id, version) in self._revoked or (package_id, "*") in self._revoked
