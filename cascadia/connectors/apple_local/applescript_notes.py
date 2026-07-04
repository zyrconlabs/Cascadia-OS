"""AppleScript/osascript-backed Notes adapter (Stage 2).

Apple Notes has no EventKit API, so this adapter shells out to osascript.

Injection safety: every user-supplied string (titles, bodies, folder
names, note ids, queries) is passed as an ARGV ITEM to ``on run argv`` —
never interpolated into AppleScript source — so note content cannot
inject script.

Deleting a note moves it to Notes' "Recently Deleted" folder (Apple's
behaviour; there is no scriptable hard delete).
"""
from __future__ import annotations

import subprocess
import threading
from typing import Any

try:
    from .schemas import error_response, ok_response, unavailable_response
except ImportError:  # pragma: no cover - supports direct script execution
    from schemas import error_response, ok_response, unavailable_response

# \x1f (ASCII unit separator) — never appears in real titles/folder names.
_SEP = "\x1f"

_PROBE_TIMEOUT_S = 15.0
_CALL_TIMEOUT_S = 60.0

# osascript error -1743 = "Not authorized to send Apple events to Notes"
_NOT_AUTHORIZED_MARKER = "-1743"

_probe_lock = threading.Lock()
_probe_cache: dict[str, str | None] = {"state": None}


def run_notes_script(
    script: str, args: list[str] | None = None, timeout: float = _CALL_TIMEOUT_S
) -> tuple[str | None, str | None]:
    """Run an AppleScript with argv-passed inputs. Returns (stdout, error)."""
    cmd = ["osascript", "-e", script]
    if args:
        cmd.extend(args)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError as exc:  # osascript missing (non-macOS)
        return None, str(exc)
    if proc.returncode != 0:
        return None, proc.stderr.strip() or f"osascript exited {proc.returncode}"
    return proc.stdout.rstrip("\n"), None


def notes_permission_state(refresh: bool = False) -> str:
    """Tri-state Automation grant for Notes: granted / denied / not_yet_asked.

    Probes with a lightweight `count folders`, cached for the process
    lifetime (health is polled; the probe launches Notes). A timeout means
    the TCC prompt is pending/unanswered → not_yet_asked.
    """
    with _probe_lock:
        cached = _probe_cache["state"]
        if cached in ("granted", "denied") and not refresh:
            return cached
        _out, err = run_notes_script(
            'tell application "Notes" to count folders', timeout=_PROBE_TIMEOUT_S
        )
        if err is None:
            state = "granted"
        elif _NOT_AUTHORIZED_MARKER in err:
            state = "denied"
        elif err == "timeout":
            state = "not_yet_asked"
        else:
            state = "unknown"
        _probe_cache["state"] = state
        return state


_LIST_FOLDERS = """
on run argv
    set us to character id 31
    set out to ""
    tell application "Notes"
        repeat with f in folders
            set out to out & (id of f as text) & us & (name of f as text) & linefeed
        end repeat
    end tell
    return out
end run
"""

_SEARCH_NOTES = """
on run argv
    set q to item 1 of argv
    set folderName to item 2 of argv
    set us to character id 31
    set out to ""
    tell application "Notes"
        if folderName is "" then
            set theNotes to notes
        else
            set theNotes to notes of folder folderName
        end if
        repeat with n in theNotes
            set nm to (name of n as text)
            if q is "" or nm contains q then
                set out to out & (id of n as text) & us & nm & linefeed
            end if
        end repeat
    end tell
    return out
end run
"""

_GET_NOTE = """
on run argv
    tell application "Notes"
        set n to note id (item 1 of argv)
        return (name of n as text) & (character id 31) & (body of n as text)
    end tell
end run
"""

_CREATE_NOTE = """
on run argv
    set t to item 1 of argv
    set b to item 2 of argv
    set folderName to item 3 of argv
    tell application "Notes"
        if folderName is "" then
            set n to make new note with properties {name:t, body:b}
        else
            set n to make new note at folder folderName with properties {name:t, body:b}
        end if
        return id of n as text
    end tell
end run
"""

_DELETE_NOTE = """
on run argv
    tell application "Notes"
        delete note id (item 1 of argv)
    end tell
    return "deleted"
end run
"""


def _parse_rows(raw: str) -> list[dict[str, str]]:
    rows = []
    for line in raw.splitlines():
        if _SEP in line:
            ident, name = line.split(_SEP, 1)
            rows.append({"id": ident, "name": name})
    return rows


class AppleScriptNotesAdapter:
    domain = "notes"

    @property
    def permission(self) -> str:
        return notes_permission_state()

    @property
    def available(self) -> bool:
        return self.permission == "granted"

    @property
    def reason(self) -> str:
        state = self.permission
        if state == "granted":
            return "Notes automation access granted."
        if state == "not_yet_asked":
            return (
                "Notes automation permission not yet requested — the first "
                "osascript call triggers the macOS Automation prompt."
            )
        return (
            f"Notes automation permission is {state} — grant it in System "
            "Settings > Privacy & Security > Automation (allow this app to "
            "control Notes)."
        )

    def readiness(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "permission": self.permission,
        }

    def _unavailable(self) -> dict[str, Any]:
        return unavailable_response(self.domain, self.reason)

    def list_folders(self, **_extra: Any) -> dict[str, Any]:
        if not self.available:
            return self._unavailable()
        out, err = run_notes_script(_LIST_FOLDERS)
        if err is not None:
            return error_response(f"Notes list_folders failed: {err}")
        return ok_response(folders=_parse_rows(out or ""))

    def search(self, query: str = "", folder: str = "", **_extra: Any) -> dict[str, Any]:
        if not self.available:
            return self._unavailable()
        out, err = run_notes_script(_SEARCH_NOTES, [str(query or ""), str(folder or "")])
        if err is not None:
            return error_response(f"Notes search failed: {err}")
        return ok_response(notes=_parse_rows(out or ""))

    def get_note(self, note_id: str | None = None, **_extra: Any) -> dict[str, Any]:
        if not self.available:
            return self._unavailable()
        if not note_id:
            return error_response("note_id is required")
        out, err = run_notes_script(_GET_NOTE, [str(note_id)])
        if err is not None:
            return error_response(f"Notes get_note failed: {err}")
        name, _, body = (out or "").partition(_SEP)
        return ok_response(note={"id": note_id, "name": name, "body": body})

    def create_note(
        self,
        title: str | None = None,
        body: str = "",
        folder: str = "",
        **_extra: Any,
    ) -> dict[str, Any]:
        if not self.available:
            return self._unavailable()
        if not title:
            return error_response("title is required")
        # Notes takes the note name from the `name` property; the body is
        # passed through as-is (Notes renders it as HTML).
        out, err = run_notes_script(_CREATE_NOTE, [str(title), str(body or ""), str(folder or "")])
        if err is not None:
            return error_response(f"Notes create_note failed: {err}")
        return ok_response(note_id=(out or "").strip())

    def delete_note(self, note_id: str | None = None, **_extra: Any) -> dict[str, Any]:
        """Moves the note to Recently Deleted (Apple's scriptable delete)."""
        if not self.available:
            return self._unavailable()
        if not note_id:
            return error_response("note_id is required")
        _out, err = run_notes_script(_DELETE_NOTE, [str(note_id)])
        if err is not None:
            return error_response(f"Notes delete_note failed: {err}")
        return ok_response(deleted=True, note_id=note_id, moved_to="Recently Deleted")
