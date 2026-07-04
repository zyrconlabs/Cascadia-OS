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
    permission: str = "unavailable"

    def readiness(self) -> dict[str, Any]:
        return {"available": self.available, "reason": self.reason, "permission": self.permission}

    def list_calendars(self, **_filters: Any) -> dict[str, Any]:
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

    def create_event(self, **_filters: Any) -> dict[str, Any]:
        return unavailable_response("calendar", self.reason)

    def delete_event(self, **_filters: Any) -> dict[str, Any]:
        return unavailable_response("calendar", self.reason)


@dataclass
class RemindersAdapter:
    available: bool = False
    reason: str = UNAVAILABLE_REASON
    permission: str = "unavailable"

    def readiness(self) -> dict[str, Any]:
        return {"available": self.available, "reason": self.reason, "permission": self.permission}

    def list_lists(self, **_filters: Any) -> dict[str, Any]:
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

    def create_item(self, **_filters: Any) -> dict[str, Any]:
        return unavailable_response("reminders", self.reason)

    def delete_item(self, **_filters: Any) -> dict[str, Any]:
        return unavailable_response("reminders", self.reason)


@dataclass
class NotesAdapter:
    available: bool = False
    reason: str = UNAVAILABLE_REASON
    permission: str = "unavailable"

    def readiness(self) -> dict[str, Any]:
        return {"available": self.available, "reason": self.reason, "permission": self.permission}

    def list_folders(self, **_filters: Any) -> dict[str, Any]:
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

    def create_note(self, **_filters: Any) -> dict[str, Any]:
        return unavailable_response("notes", self.reason)

    def delete_note(self, **_filters: Any) -> dict[str, Any]:
        return unavailable_response("notes", self.reason)


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


def build_live_bridge() -> AppleBridge:
    """Stage 2 runtime bridge backed by real Apple adapters.

    On macOS with pyobjc-framework-EventKit importable, wires the real
    EventKit (Calendar/Reminders) and osascript (Notes) adapters behind the
    same AppleBridge surface. Off macOS, or if EventKit is unavailable,
    falls back to the Phase 1 stub bridge (every domain unavailable) so the
    connector degrades instead of crashing. The default ``AppleBridge()``
    constructor is left untouched (stubs only) — that is the contract the
    mocked tests depend on; live wiring is opt-in via this factory.
    """
    if platform.system() != "Darwin":
        return AppleBridge()
    try:
        from .eventkit_adapters import EventKitCalendarAdapter, EventKitRemindersAdapter
        from .applescript_notes import AppleScriptNotesAdapter
    except ImportError:  # pragma: no cover - supports direct script execution
        from eventkit_adapters import EventKitCalendarAdapter, EventKitRemindersAdapter
        from applescript_notes import AppleScriptNotesAdapter
    return AppleBridge(
        calendar=EventKitCalendarAdapter(),
        reminders=EventKitRemindersAdapter(),
        notes=AppleScriptNotesAdapter(),
    )
