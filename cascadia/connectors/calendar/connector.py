#!/usr/bin/env python3
"""
Calendar Connector — B3
Cascadia OS DEPOT packaging

Unified calendar connector for listing, creating, and managing events
across Google Calendar, Microsoft Outlook (Graph API), and iCal feeds.

Port: 9031
NATS subject: cascadia.connectors.calendar-connector.>
Auth: oauth2 (access_token per payload); iCal uses a public URL
"""

import asyncio
import json
import logging
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NAME = "calendar-connector"
VERSION = "1.0.0"
PORT = 9031
NATS_URL = "nats://localhost:4222"
APPROVAL_SUBJECT = "cascadia.approvals.request"
RESPONSE_SUBJECT = f"cascadia.connectors.{NAME}.response"
ACTIONS_REQUIRING_APPROVAL = {"create_event", "update_event", "delete_event"}

GCAL_API_BASE = "https://www.googleapis.com/calendar/v3"
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(NAME)


# ---------------------------------------------------------------------------
# Generic HTTP helpers (stdlib only)
# ---------------------------------------------------------------------------

def _http_get(url: str, headers: dict | None = None, params: dict | None = None) -> dict:
    """Perform an authenticated GET and return parsed JSON."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_post(url: str, body: dict, headers: dict | None = None) -> dict:
    """Perform an authenticated POST with a JSON body and return parsed JSON."""
    data = json.dumps(body).encode("utf-8")
    base_headers = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        base_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=base_headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_patch(url: str, body: dict, headers: dict | None = None) -> dict:
    """Perform an authenticated PATCH with a JSON body and return parsed JSON."""
    data = json.dumps(body).encode("utf-8")
    base_headers = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        base_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=base_headers, method="PATCH")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_delete(url: str, headers: dict | None = None) -> dict:
    """Perform an authenticated DELETE and return an ok dict."""
    req = urllib.request.Request(url, headers=headers or {}, method="DELETE")
    with urllib.request.urlopen(req, timeout=20) as resp:
        status = resp.status
    return {"ok": status in (200, 204), "http_status": status}


def _auth_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


# ---------------------------------------------------------------------------
# iCal parser (no third-party libraries)
# ---------------------------------------------------------------------------

def _fetch_ical_text(url: str) -> str:
    """Download an iCal feed and return its text."""
    with urllib.request.urlopen(url, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_ical_events(ical_text: str) -> list[dict]:
    """Parse VEVENT blocks from iCal text using a simple line-by-line approach.

    Handles CRLF and LF line endings. Supports line folding (continuation
    lines that start with a space or tab are joined to the previous line).

    Returns a list of dicts with keys: uid, summary, dtstart, dtend.
    """
    # Normalise line endings and unfold continued lines
    lines = ical_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    unfolded: list[str] = []
    for line in lines:
        if line and line[0] in (" ", "\t") and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)

    events: list[dict] = []
    inside_vevent = False
    current: dict = {}

    for line in unfolded:
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            inside_vevent = True
            current = {}
            continue
        if upper == "END:VEVENT":
            inside_vevent = False
            events.append(current)
            current = {}
            continue
        if not inside_vevent:
            continue

        # Split property name / params from value
        if ":" not in line:
            continue
        prop_part, _, value = line.partition(":")
        # Strip parameters (e.g. DTSTART;TZID=America/New_York)
        prop_name = prop_part.split(";")[0].upper()

        if prop_name == "SUMMARY":
            current["summary"] = value
        elif prop_name == "DTSTART":
            current["dtstart"] = value
        elif prop_name == "DTEND":
            current["dtend"] = value
        elif prop_name == "UID":
            current["uid"] = value

    return events


# ---------------------------------------------------------------------------
# Google Calendar provider
# ---------------------------------------------------------------------------

def _gcal_list_events(
    access_token: str,
    calendar_id: str,
    time_min: str | None,
    time_max: str | None,
    max_results: int,
) -> dict:
    params: dict = {"maxResults": max_results, "singleEvents": "true", "orderBy": "startTime"}
    if time_min:
        params["timeMin"] = time_min
    if time_max:
        params["timeMax"] = time_max
    result = _http_get(
        f"{GCAL_API_BASE}/calendars/{urllib.parse.quote(calendar_id)}/events",
        headers=_auth_headers(access_token),
        params=params,
    )
    return {"ok": True, "events": result.get("items", []), "provider": "google"}


def _gcal_get_event(access_token: str, calendar_id: str, event_id: str) -> dict:
    result = _http_get(
        f"{GCAL_API_BASE}/calendars/{urllib.parse.quote(calendar_id)}/events/{urllib.parse.quote(event_id)}",
        headers=_auth_headers(access_token),
    )
    return {"ok": True, "event": result, "provider": "google"}


def _gcal_create_event(
    access_token: str,
    calendar_id: str,
    summary: str,
    start: str,
    end: str,
    description: str,
) -> dict:
    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }
    result = _http_post(
        f"{GCAL_API_BASE}/calendars/{urllib.parse.quote(calendar_id)}/events",
        body=body,
        headers=_auth_headers(access_token),
    )
    return {"ok": True, "event": result, "provider": "google"}


def _gcal_update_event(
    access_token: str,
    calendar_id: str,
    event_id: str,
    updates: dict,
) -> dict:
    result = _http_patch(
        f"{GCAL_API_BASE}/calendars/{urllib.parse.quote(calendar_id)}/events/{urllib.parse.quote(event_id)}",
        body=updates,
        headers=_auth_headers(access_token),
    )
    return {"ok": True, "event": result, "provider": "google"}


def _gcal_delete_event(access_token: str, calendar_id: str, event_id: str) -> dict:
    result = _http_delete(
        f"{GCAL_API_BASE}/calendars/{urllib.parse.quote(calendar_id)}/events/{urllib.parse.quote(event_id)}",
        headers=_auth_headers(access_token),
    )
    result["provider"] = "google"
    return result


# ---------------------------------------------------------------------------
# Outlook (Microsoft Graph) provider
# ---------------------------------------------------------------------------

def _outlook_list_events(
    access_token: str,
    calendar_id: str,
    time_min: str | None,
    time_max: str | None,
    max_results: int,
) -> dict:
    # calendar_id "me" means the default calendar
    base = (
        f"{GRAPH_API_BASE}/me/calendars/{urllib.parse.quote(calendar_id)}/events"
        if calendar_id and calendar_id.lower() != "me"
        else f"{GRAPH_API_BASE}/me/events"
    )
    params: dict = {"$top": max_results, "$orderby": "start/dateTime asc"}
    filters = []
    if time_min:
        filters.append(f"start/dateTime ge '{time_min}'")
    if time_max:
        filters.append(f"end/dateTime le '{time_max}'")
    if filters:
        params["$filter"] = " and ".join(filters)
    result = _http_get(base, headers=_auth_headers(access_token), params=params)
    return {"ok": True, "events": result.get("value", []), "provider": "outlook"}


def _outlook_get_event(access_token: str, calendar_id: str, event_id: str) -> dict:
    base = (
        f"{GRAPH_API_BASE}/me/calendars/{urllib.parse.quote(calendar_id)}/events/{urllib.parse.quote(event_id)}"
        if calendar_id and calendar_id.lower() != "me"
        else f"{GRAPH_API_BASE}/me/events/{urllib.parse.quote(event_id)}"
    )
    result = _http_get(base, headers=_auth_headers(access_token))
    return {"ok": True, "event": result, "provider": "outlook"}


def _outlook_create_event(
    access_token: str,
    calendar_id: str,
    summary: str,
    start: str,
    end: str,
    description: str,
) -> dict:
    base = (
        f"{GRAPH_API_BASE}/me/calendars/{urllib.parse.quote(calendar_id)}/events"
        if calendar_id and calendar_id.lower() != "me"
        else f"{GRAPH_API_BASE}/me/events"
    )
    body = {
        "subject": summary,
        "body": {"contentType": "text", "content": description},
        "start": {"dateTime": start, "timeZone": "UTC"},
        "end": {"dateTime": end, "timeZone": "UTC"},
    }
    result = _http_post(base, body=body, headers=_auth_headers(access_token))
    return {"ok": True, "event": result, "provider": "outlook"}


def _outlook_update_event(
    access_token: str,
    calendar_id: str,
    event_id: str,
    updates: dict,
) -> dict:
    base = (
        f"{GRAPH_API_BASE}/me/calendars/{urllib.parse.quote(calendar_id)}/events/{urllib.parse.quote(event_id)}"
        if calendar_id and calendar_id.lower() != "me"
        else f"{GRAPH_API_BASE}/me/events/{urllib.parse.quote(event_id)}"
    )
    result = _http_patch(base, body=updates, headers=_auth_headers(access_token))
    return {"ok": True, "event": result, "provider": "outlook"}


def _outlook_delete_event(access_token: str, calendar_id: str, event_id: str) -> dict:
    base = (
        f"{GRAPH_API_BASE}/me/calendars/{urllib.parse.quote(calendar_id)}/events/{urllib.parse.quote(event_id)}"
        if calendar_id and calendar_id.lower() != "me"
        else f"{GRAPH_API_BASE}/me/events/{urllib.parse.quote(event_id)}"
    )
    result = _http_delete(base, headers=_auth_headers(access_token))
    result["provider"] = "outlook"
    return result


# ---------------------------------------------------------------------------
# iCal provider (read-only)
# ---------------------------------------------------------------------------

def _ical_list_events(ical_url: str, max_results: int) -> dict:
    """Fetch and parse an iCal feed; return up to max_results VEVENT records."""
    text = _fetch_ical_text(ical_url)
    events = _parse_ical_events(text)
    return {"ok": True, "events": events[:max_results], "provider": "ical"}


# ---------------------------------------------------------------------------
# Provider-dispatched action functions
# ---------------------------------------------------------------------------

def list_events(
    provider: str,
    credentials: dict,
    calendar_id: str,
    time_min: str | None = None,
    time_max: str | None = None,
    max_results: int = 10,
) -> dict:
    log.info("list_events provider=%s calendar=%s", provider, calendar_id)
    try:
        if provider == "google":
            return _gcal_list_events(
                credentials["access_token"], calendar_id, time_min, time_max, max_results
            )
        elif provider == "outlook":
            return _outlook_list_events(
                credentials["access_token"], calendar_id, time_min, time_max, max_results
            )
        elif provider == "ical":
            return _ical_list_events(credentials["ical_url"], max_results)
        else:
            return {"ok": False, "error": f"unsupported provider: {provider}"}
    except Exception as exc:  # noqa: BLE001
        log.error("list_events failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def get_event(
    provider: str,
    credentials: dict,
    calendar_id: str,
    event_id: str,
) -> dict:
    log.info("get_event provider=%s event_id=%s", provider, event_id)
    try:
        if provider == "google":
            return _gcal_get_event(credentials["access_token"], calendar_id, event_id)
        elif provider == "outlook":
            return _outlook_get_event(credentials["access_token"], calendar_id, event_id)
        elif provider == "ical":
            # iCal feeds are read-only lists; get_event fetches all and finds by uid
            text = _fetch_ical_text(credentials["ical_url"])
            events = _parse_ical_events(text)
            matched = [e for e in events if e.get("uid") == event_id]
            if matched:
                return {"ok": True, "event": matched[0], "provider": "ical"}
            return {"ok": False, "error": f"event uid={event_id} not found in iCal feed"}
        else:
            return {"ok": False, "error": f"unsupported provider: {provider}"}
    except Exception as exc:  # noqa: BLE001
        log.error("get_event failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def create_event(
    provider: str,
    credentials: dict,
    calendar_id: str,
    summary: str,
    start: str,
    end: str,
    description: str = "",
) -> dict:
    log.info("create_event provider=%s summary=%r", provider, summary)
    try:
        if provider == "google":
            return _gcal_create_event(
                credentials["access_token"], calendar_id, summary, start, end, description
            )
        elif provider == "outlook":
            return _outlook_create_event(
                credentials["access_token"], calendar_id, summary, start, end, description
            )
        elif provider == "ical":
            return {"ok": False, "error": "iCal provider is read-only; cannot create events"}
        else:
            return {"ok": False, "error": f"unsupported provider: {provider}"}
    except Exception as exc:  # noqa: BLE001
        log.error("create_event failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def update_event(
    provider: str,
    credentials: dict,
    calendar_id: str,
    event_id: str,
    updates: dict,
) -> dict:
    log.info("update_event provider=%s event_id=%s", provider, event_id)
    try:
        if provider == "google":
            return _gcal_update_event(
                credentials["access_token"], calendar_id, event_id, updates
            )
        elif provider == "outlook":
            return _outlook_update_event(
                credentials["access_token"], calendar_id, event_id, updates
            )
        elif provider == "ical":
            return {"ok": False, "error": "iCal provider is read-only; cannot update events"}
        else:
            return {"ok": False, "error": f"unsupported provider: {provider}"}
    except Exception as exc:  # noqa: BLE001
        log.error("update_event failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def delete_event(
    provider: str,
    credentials: dict,
    calendar_id: str,
    event_id: str,
) -> dict:
    log.info("delete_event provider=%s event_id=%s", provider, event_id)
    try:
        if provider == "google":
            return _gcal_delete_event(credentials["access_token"], calendar_id, event_id)
        elif provider == "outlook":
            return _outlook_delete_event(credentials["access_token"], calendar_id, event_id)
        elif provider == "ical":
            return {"ok": False, "error": "iCal provider is read-only; cannot delete events"}
        else:
            return {"ok": False, "error": f"unsupported provider: {provider}"}
    except Exception as exc:  # noqa: BLE001
        log.error("delete_event failed: %s", exc)
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# execute_call dispatcher
# ---------------------------------------------------------------------------

def execute_call(payload: dict) -> dict:
    """Dispatch to the appropriate function based on payload['action']."""
    action = payload.get("action")
    provider = payload.get("provider", "")
    credentials = payload.get("credentials", {})
    calendar_id = payload.get("calendar_id", "me")

    if action == "list_events":
        return list_events(
            provider=provider,
            credentials=credentials,
            calendar_id=calendar_id,
            time_min=payload.get("time_min"),
            time_max=payload.get("time_max"),
            max_results=payload.get("max_results", 10),
        )
    elif action == "get_event":
        return get_event(
            provider=provider,
            credentials=credentials,
            calendar_id=calendar_id,
            event_id=payload["event_id"],
        )
    elif action == "create_event":
        return create_event(
            provider=provider,
            credentials=credentials,
            calendar_id=calendar_id,
            summary=payload["summary"],
            start=payload["start"],
            end=payload["end"],
            description=payload.get("description", ""),
        )
    elif action == "update_event":
        return update_event(
            provider=provider,
            credentials=credentials,
            calendar_id=calendar_id,
            event_id=payload["event_id"],
            updates=payload.get("updates", {}),
        )
    elif action == "delete_event":
        return delete_event(
            provider=provider,
            credentials=credentials,
            calendar_id=calendar_id,
            event_id=payload["event_id"],
        )
    else:
        return {"ok": False, "error": f"unknown action: {action}"}


# ---------------------------------------------------------------------------
# NATS event handler
# ---------------------------------------------------------------------------

async def handle_event(nc, subject: str, raw: bytes) -> None:
    """Handle an inbound NATS message on the calendar-connector subject tree.

    Flow:
      1. Parse JSON from raw bytes.
      2. If the action requires approval, publish to cascadia.approvals.request
         and return {"ok": True, "status": "pending_approval"} — do NOT execute.
      3. Otherwise call execute_call and publish result to the response subject.
    """
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.error("Failed to parse inbound message: %s", exc)
        return

    action = payload.get("action", "")
    log.info("handle_event subject=%s action=%s", subject, action)

    if action in ACTIONS_REQUIRING_APPROVAL:
        approval_request = {
            "connector": NAME,
            "subject": subject,
            "action": action,
            "payload": payload,
            "reason": f"Action '{action}' requires human approval before execution.",
        }
        await nc.publish(
            APPROVAL_SUBJECT,
            json.dumps(approval_request).encode("utf-8"),
        )
        log.info("Published approval request for action=%s", action)
        pending = {"ok": True, "status": "pending_approval"}
        await nc.publish(
            RESPONSE_SUBJECT,
            json.dumps({"connector": NAME, "action": action, "result": pending}).encode("utf-8"),
        )
        return

    try:
        result = execute_call(payload)
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error": str(exc)}

    response = {"connector": NAME, "action": action, "result": result}
    await nc.publish(
        RESPONSE_SUBJECT,
        json.dumps(response).encode("utf-8"),
    )
    log.info("Published response for action=%s ok=%s", action, result.get("ok"))


# ---------------------------------------------------------------------------
# Health HTTP server
# ---------------------------------------------------------------------------

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"status": "ok", "connector": NAME}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # suppress default access log noise
        pass


def _start_health_server() -> threading.Thread:
    server = HTTPServer(("0.0.0.0", PORT), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("Health server listening on port %d at /health", PORT)
    return thread


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _nats_main() -> None:
    try:
        import nats  # type: ignore
    except ImportError:
        log.warning("nats-py not installed — NATS subscribe disabled")
        await asyncio.sleep(float("inf"))
        return

    nc = await nats.connect(NATS_URL)
    log.info("Connected to NATS at %s", NATS_URL)

    subject = f"cascadia.connectors.{NAME}.>"

    async def _cb(msg):
        await handle_event(nc, msg.subject, msg.data)

    await nc.subscribe(subject, cb=_cb)
    log.info("Subscribed to %s", subject)

    try:
        await asyncio.sleep(float("inf"))
    finally:
        await nc.drain()


def main() -> None:
    _start_health_server()
    asyncio.run(_nats_main())


if __name__ == "__main__":
    main()
