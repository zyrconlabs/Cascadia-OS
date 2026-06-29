"""Mockable Apple adapter boundary.

Phase 1 deliberately avoids EventKit, AppleScript, osascript, and any real
Apple app access. Tests and future phases can replace these adapters without
changing the dispatcher surface.
"""
from __future__ import annotations

import platform
from dataclasses import dataclass, field
from typing import Any

try:
    from .schemas import ok_response, unavailable_response
except ImportError:  # pragma: no cover - supports direct script execution
    from schemas import ok_response, unavailable_response


UNAVAILABLE_REASON = "Real Apple app access is not implemented in Phase 1."


@dataclass
class CalendarAdapter:
    available: bool = False
    reason: str = UNAVAILABLE_REASON

    def readiness(self) -> dict[str, Any]:
        return {"available": self.available, "reason": self.reason}

    def list_calendars(self) -> dict[str, Any]:
        if not self.available:
            return unavailable_response("calendar", self.reason)
        return ok_response(calendars=[])

    def list_events(self, **_filters: Any) -> dict[str, Any]:
        if not self.available:
            return unavailable_response("calendar", self.reason)
        return ok_response(events=[])

    def get_event(self, **_filters: Any) -> dict[str, Any]:
        if not self.available:
            return unavailable_response("calendar", self.reason)
        return ok_response(event=None)


@dataclass
class RemindersAdapter:
    available: bool = False
    reason: str = UNAVAILABLE_REASON

    def readiness(self) -> dict[str, Any]:
        return {"available": self.available, "reason": self.reason}

    def list_lists(self) -> dict[str, Any]:
        if not self.available:
            return unavailable_response("reminders", self.reason)
        return ok_response(lists=[])

    def list_items(self, **_filters: Any) -> dict[str, Any]:
        if not self.available:
            return unavailable_response("reminders", self.reason)
        return ok_response(items=[])

    def get_item(self, **_filters: Any) -> dict[str, Any]:
        if not self.available:
            return unavailable_response("reminders", self.reason)
        return ok_response(item=None)


@dataclass
class NotesAdapter:
    available: bool = False
    reason: str = UNAVAILABLE_REASON

    def readiness(self) -> dict[str, Any]:
        return {"available": self.available, "reason": self.reason}

    def list_folders(self) -> dict[str, Any]:
        if not self.available:
            return unavailable_response("notes", self.reason)
        return ok_response(folders=[])

    def search(self, **_filters: Any) -> dict[str, Any]:
        if not self.available:
            return unavailable_response("notes", self.reason)
        return ok_response(notes=[])

    def get_note(self, **_filters: Any) -> dict[str, Any]:
        if not self.available:
            return unavailable_response("notes", self.reason)
        return ok_response(note=None)


@dataclass
class AppleBridge:
    platform_name: str = field(default_factory=platform.system)
    calendar: CalendarAdapter | None = None
    reminders: RemindersAdapter | None = None
    notes: NotesAdapter | None = None

    def __post_init__(self) -> None:
        reason = (
            UNAVAILABLE_REASON
            if self.is_macos
            else f"Apple local connector is only available on macOS; current platform is {self.platform_name}."
        )
        if self.calendar is None:
            self.calendar = CalendarAdapter(available=False, reason=reason)
        if self.reminders is None:
            self.reminders = RemindersAdapter(available=False, reason=reason)
        if self.notes is None:
            self.notes = NotesAdapter(available=False, reason=reason)

    @property
    def is_macos(self) -> bool:
        return self.platform_name == "Darwin"

    def readiness(self) -> dict[str, Any]:
        return {
            "platform": self.platform_name,
            "is_macos": self.is_macos,
            "calendar": self.calendar.readiness() if self.calendar else {},
            "reminders": self.reminders.readiness() if self.reminders else {},
            "notes": self.notes.readiness() if self.notes else {},
        }
