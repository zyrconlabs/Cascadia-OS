"""Real EventKit-backed Calendar and Reminders adapters (Stage 2).

Requires pyobjc-framework-EventKit and macOS TCC grants (Calendars,
Reminders). Every completion-handler API is waited on with
threading.Event + timeout so a missing grant can never deadlock the
connector. When permission is not granted, methods return the same
structured ``unavailable_response`` the Phase 1 stubs used — never a
crash, never a hang.
"""
from __future__ import annotations

import datetime as _dt
import threading
from typing import Any

try:
    from .schemas import error_response, ok_response, unavailable_response
except ImportError:  # pragma: no cover - supports direct script execution
    from schemas import error_response, ok_response, unavailable_response

try:  # pragma: no cover - only importable on macOS with pyobjc installed
    import EventKit  # type: ignore
    from Foundation import NSDateComponents  # type: ignore
    from Foundation import NSDate  # type: ignore

    _IMPORT_ERROR: str | None = None
except Exception as exc:  # noqa: BLE001
    EventKit = None  # type: ignore[assignment]
    NSDate = None  # type: ignore[assignment]
    NSDateComponents = None  # type: ignore[assignment]
    _IMPORT_ERROR = str(exc)

# EKAuthorizationStatus values. macOS 14+ renamed 3 to fullAccess and added
# writeOnly=4; on older systems 3 was plain "authorized" — same meaning here.
_PERMISSION_BY_STATUS = {
    0: "not_yet_asked",
    1: "denied",  # restricted
    2: "denied",
    3: "granted",
    4: "write_only",
}

_FETCH_TIMEOUT_S = 10.0
_REQUEST_ACCESS_TIMEOUT_S = 180.0

_store_lock = threading.Lock()
_shared_store: Any = None


def _event_store() -> Any:
    """Process-wide shared EKEventStore (creation is not free)."""
    global _shared_store
    with _store_lock:
        if _shared_store is None:
            _shared_store = EventKit.EKEventStore.alloc().init()
        return _shared_store


def permission_state(entity_type: int) -> str:
    """Tri-state TCC grant for an EventKit entity type.

    Returns granted / denied / not_yet_asked / write_only / unknown.
    authorizationStatus never triggers a prompt — safe for health polling.
    """
    if EventKit is None:
        return "unknown"
    status = int(EventKit.EKEventStore.authorizationStatusForEntityType_(entity_type))
    return _PERMISSION_BY_STATUS.get(status, "unknown")


def request_access(entity_type: int, timeout_s: float = _REQUEST_ACCESS_TIMEOUT_S) -> dict[str, Any]:
    """Trigger the TCC prompt (macOS 14+ API, legacy fallback via hasattr)."""
    if EventKit is None:
        return {"granted": False, "error": f"EventKit unavailable: {_IMPORT_ERROR}"}

    store = _event_store()
    done = threading.Event()
    result: dict[str, Any] = {"granted": False, "error": None}

    def _completion(granted: bool, error: Any) -> None:
        result["granted"] = bool(granted)
        result["error"] = str(error) if error else None
        done.set()

    if entity_type == EventKit.EKEntityTypeEvent and hasattr(
        store, "requestFullAccessToEventsWithCompletion_"
    ):
        store.requestFullAccessToEventsWithCompletion_(_completion)
    elif entity_type == EventKit.EKEntityTypeReminder and hasattr(
        store, "requestFullAccessToRemindersWithCompletion_"
    ):
        store.requestFullAccessToRemindersWithCompletion_(_completion)
    else:  # pre-macOS-14 fallback
        store.requestAccessToEntityType_completion_(entity_type, _completion)

    if not done.wait(timeout_s):
        return {
            "granted": False,
            "error": f"timed out after {timeout_s}s waiting for the TCC prompt",
        }
    return result


def _to_datetime(value: Any) -> _dt.datetime:
    if isinstance(value, _dt.datetime):
        dt = value
    elif isinstance(value, str):
        dt = _dt.datetime.fromisoformat(value)
    else:
        raise ValueError(f"expected ISO datetime string or datetime, got {type(value).__name__}")
    if dt.tzinfo is None:
        dt = dt.astimezone()  # interpret naive input as local time
    return dt


def _nsdate(value: Any) -> Any:
    return NSDate.dateWithTimeIntervalSince1970_(_to_datetime(value).timestamp())


def _iso(nsdate: Any) -> str | None:
    if nsdate is None:
        return None
    return (
        _dt.datetime.fromtimestamp(nsdate.timeIntervalSince1970())
        .astimezone()
        .isoformat(timespec="seconds")
    )


def _event_dict(event: Any) -> dict[str, Any]:
    calendar = event.calendar()
    return {
        "id": str(event.eventIdentifier()),
        "title": str(event.title() or ""),
        "start": _iso(event.startDate()),
        "end": _iso(event.endDate()),
        "calendar": str(calendar.title()) if calendar else None,
        "all_day": bool(event.isAllDay()),
    }


_NS_UNDEFINED = 2**60  # NSDateComponentUndefined is LONG_MAX; anything huge is unset


def _due_iso(reminder: Any) -> str | None:
    comps = reminder.dueDateComponents()
    if comps is None:
        return None
    year, month, day = int(comps.year()), int(comps.month()), int(comps.day())
    if year >= _NS_UNDEFINED:
        return None
    hour, minute = int(comps.hour()), int(comps.minute())
    if hour >= _NS_UNDEFINED:
        return f"{year:04d}-{month:02d}-{day:02d}"
    return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}"


def _reminder_dict(reminder: Any) -> dict[str, Any]:
    calendar = reminder.calendar()
    return {
        "id": str(reminder.calendarItemIdentifier()),
        "title": str(reminder.title() or ""),
        "due": _due_iso(reminder),
        "list": str(calendar.title()) if calendar else None,
        "completed": bool(reminder.isCompleted()),
    }


class _EventKitAdapterBase:
    """Shared availability/readiness logic for the two EventKit domains."""

    domain = ""
    entity_type = -1
    settings_pane = ""

    @property
    def available(self) -> bool:
        return EventKit is not None and permission_state(self.entity_type) == "granted"

    @property
    def permission(self) -> str:
        return permission_state(self.entity_type)

    @property
    def reason(self) -> str:
        if EventKit is None:
            return f"EventKit unavailable: {_IMPORT_ERROR}"
        state = self.permission
        if state == "granted":
            return f"EventKit {self.domain} access granted."
        if state == "not_yet_asked":
            return (
                f"macOS {self.domain} permission not yet requested — "
                "run the connector's request-access flow or install.sh preflight."
            )
        return (
            f"macOS {self.domain} permission is {state} — grant it in "
            f"System Settings > Privacy & Security > {self.settings_pane}."
        )

    def readiness(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "permission": self.permission,
        }

    def _unavailable(self) -> dict[str, Any]:
        return unavailable_response(self.domain, self.reason)


class EventKitCalendarAdapter(_EventKitAdapterBase):
    domain = "calendar"
    settings_pane = "Calendars"

    def __init__(self) -> None:
        self.entity_type = EventKit.EKEntityTypeEvent if EventKit else -1

    def request_access(self, timeout_s: float = _REQUEST_ACCESS_TIMEOUT_S) -> dict[str, Any]:
        return request_access(self.entity_type, timeout_s)

    def list_calendars(self, **_extra: Any) -> dict[str, Any]:
        if not self.available:
            return self._unavailable()
        calendars = _event_store().calendarsForEntityType_(self.entity_type) or []
        return ok_response(
            calendars=[
                {"id": str(c.calendarIdentifier()), "name": str(c.title())} for c in calendars
            ]
        )

    def list_events(self, start: Any = None, end: Any = None, **_extra: Any) -> dict[str, Any]:
        if not self.available:
            return self._unavailable()
        try:
            start_dt = (
                _to_datetime(start)
                if start is not None
                else _dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            )
            end_dt = _to_datetime(end) if end is not None else start_dt + _dt.timedelta(days=1)
        except ValueError as exc:
            return error_response(str(exc))
        store = _event_store()
        predicate = store.predicateForEventsWithStartDate_endDate_calendars_(
            NSDate.dateWithTimeIntervalSince1970_(start_dt.timestamp()),
            NSDate.dateWithTimeIntervalSince1970_(end_dt.timestamp()),
            None,
        )
        events = store.eventsMatchingPredicate_(predicate) or []
        return ok_response(events=[_event_dict(e) for e in events])

    def get_event(self, event_id: str | None = None, **_extra: Any) -> dict[str, Any]:
        if not self.available:
            return self._unavailable()
        if not event_id:
            return error_response("event_id is required")
        event = _event_store().eventWithIdentifier_(event_id)
        return ok_response(event=_event_dict(event) if event else None)

    def create_event(
        self,
        title: str | None = None,
        start: Any = None,
        end: Any = None,
        notes: str | None = None,
        calendar: str | None = None,
        **_extra: Any,
    ) -> dict[str, Any]:
        if not self.available:
            return self._unavailable()
        if not title or start is None or end is None:
            return error_response("title, start and end are required")
        store = _event_store()
        event = EventKit.EKEvent.eventWithEventStore_(store)
        event.setTitle_(title)
        try:
            event.setStartDate_(_nsdate(start))
            event.setEndDate_(_nsdate(end))
        except ValueError as exc:
            return error_response(str(exc))
        if notes:
            event.setNotes_(notes)
        target = store.defaultCalendarForNewEvents()
        if calendar:
            matches = [
                c
                for c in (store.calendarsForEntityType_(self.entity_type) or [])
                if str(c.title()) == calendar
            ]
            if not matches:
                return error_response(f"calendar not found: {calendar}")
            target = matches[0]
        event.setCalendar_(target)
        ok, error = store.saveEvent_span_error_(event, EventKit.EKSpanThisEvent, None)
        if not ok:
            return error_response(f"saveEvent failed: {error}")
        return ok_response(event_id=str(event.eventIdentifier()), event=_event_dict(event))

    def delete_event(self, event_id: str | None = None, **_extra: Any) -> dict[str, Any]:
        if not self.available:
            return self._unavailable()
        if not event_id:
            return error_response("event_id is required")
        store = _event_store()
        event = store.eventWithIdentifier_(event_id)
        if event is None:
            return error_response(f"event not found: {event_id}")
        ok, error = store.removeEvent_span_error_(event, EventKit.EKSpanThisEvent, None)
        if not ok:
            return error_response(f"removeEvent failed: {error}")
        return ok_response(deleted=True, event_id=event_id)


class EventKitRemindersAdapter(_EventKitAdapterBase):
    domain = "reminders"
    settings_pane = "Reminders"

    def __init__(self) -> None:
        self.entity_type = EventKit.EKEntityTypeReminder if EventKit else -1

    def request_access(self, timeout_s: float = _REQUEST_ACCESS_TIMEOUT_S) -> dict[str, Any]:
        return request_access(self.entity_type, timeout_s)

    def list_lists(self, **_extra: Any) -> dict[str, Any]:
        if not self.available:
            return self._unavailable()
        calendars = _event_store().calendarsForEntityType_(self.entity_type) or []
        return ok_response(
            lists=[{"id": str(c.calendarIdentifier()), "name": str(c.title())} for c in calendars]
        )

    def list_items(self, list_name: str | None = None, **_extra: Any) -> dict[str, Any]:
        """Incomplete reminders, optionally filtered to one list.

        fetchRemindersMatchingPredicate is async — waited with a timeout so a
        wedged EventKit daemon degrades to an error instead of a hang.
        """
        if not self.available:
            return self._unavailable()
        store = _event_store()
        calendars = None
        if list_name:
            calendars = [
                c
                for c in (store.calendarsForEntityType_(self.entity_type) or [])
                if str(c.title()) == list_name
            ]
            if not calendars:
                return error_response(f"reminder list not found: {list_name}")
        predicate = store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(
            None, None, calendars
        )
        done = threading.Event()
        holder: dict[str, Any] = {"reminders": []}

        def _completion(reminders: Any) -> None:
            holder["reminders"] = list(reminders or [])
            done.set()

        store.fetchRemindersMatchingPredicate_completion_(predicate, _completion)
        if not done.wait(_FETCH_TIMEOUT_S):
            return error_response(f"reminder fetch timed out after {_FETCH_TIMEOUT_S}s")
        return ok_response(items=[_reminder_dict(r) for r in holder["reminders"]])

    def get_item(self, item_id: str | None = None, **_extra: Any) -> dict[str, Any]:
        if not self.available:
            return self._unavailable()
        if not item_id:
            return error_response("item_id is required")
        item = _event_store().calendarItemWithIdentifier_(item_id)
        return ok_response(item=_reminder_dict(item) if item else None)

    def create_item(
        self,
        title: str | None = None,
        due: Any = None,
        list_name: str | None = None,
        notes: str | None = None,
        **_extra: Any,
    ) -> dict[str, Any]:
        if not self.available:
            return self._unavailable()
        if not title:
            return error_response("title is required")
        store = _event_store()
        reminder = EventKit.EKReminder.reminderWithEventStore_(store)
        reminder.setTitle_(title)
        if notes:
            reminder.setNotes_(notes)
        target = store.defaultCalendarForNewReminders()
        if list_name:
            matches = [
                c
                for c in (store.calendarsForEntityType_(self.entity_type) or [])
                if str(c.title()) == list_name
            ]
            if not matches:
                return error_response(f"reminder list not found: {list_name}")
            target = matches[0]
        reminder.setCalendar_(target)
        if due is not None:
            try:
                due_dt = _to_datetime(due)
            except ValueError as exc:
                return error_response(str(exc))
            comps = NSDateComponents.alloc().init()
            comps.setYear_(due_dt.year)
            comps.setMonth_(due_dt.month)
            comps.setDay_(due_dt.day)
            comps.setHour_(due_dt.hour)
            comps.setMinute_(due_dt.minute)
            reminder.setDueDateComponents_(comps)
        ok, error = store.saveReminder_commit_error_(reminder, True, None)
        if not ok:
            return error_response(f"saveReminder failed: {error}")
        return ok_response(
            item_id=str(reminder.calendarItemIdentifier()), item=_reminder_dict(reminder)
        )

    def delete_item(self, item_id: str | None = None, **_extra: Any) -> dict[str, Any]:
        if not self.available:
            return self._unavailable()
        if not item_id:
            return error_response("item_id is required")
        store = _event_store()
        item = store.calendarItemWithIdentifier_(item_id)
        if item is None:
            return error_response(f"reminder not found: {item_id}")
        ok, error = store.removeReminder_commit_error_(item, True, None)
        if not ok:
            return error_response(f"removeReminder failed: {error}")
        return ok_response(deleted=True, item_id=item_id)
