"""Placeholder state types for future Apple local sync support.

Phase 1 does not run migrations, create a database, or start background sync.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AppleLocalState:
    schema_version: int = 1
    sync_enabled: bool = False
    last_sync_by_domain: dict[str, str | None] = field(
        default_factory=lambda: {
            "calendar": None,
            "reminders": None,
            "notes": None,
        }
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "sync_enabled": self.sync_enabled,
            "last_sync_by_domain": dict(self.last_sync_by_domain),
            "metadata": dict(self.metadata),
        }
