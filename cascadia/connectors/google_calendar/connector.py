#!/usr/bin/env python3
"""
Google Calendar Connector — CON-015
Cascadia OS DEPOT packaging

Read and create Google Calendar events via the Google Calendar API v3.
Approval is required before any event is created.

Port: 9502
NATS subject: cascadia.connectors.google-calendar-connector.>
Auth: OAuth2 Bearer access token
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
NAME = "google-calendar-connector"
VERSION = "1.0.0"
PORT = 9502
GCAL_API_BASE = "https://www.googleapis.com/calendar/v3"
NATS_URL = "nats://localhost:4222"
APPROVAL_SUBJECT = "cascadia.approvals.request"
RESPONSE_SUBJECT = f"cascadia.connectors.{NAME}.response"
ACTIONS_REQUIRING_APPROVAL = {"create_event"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(NAME)


# ---------------------------------------------------------------------------
# Google Calendar API helpers (stdlib only)
# ---------------------------------------------------------------------------

def _gcal_get(path: str, access_token: str, params: dict | None = None) -> dict:
    """GET from the Calendar API and return parsed JSON."""
    url = f"{GCAL_API_BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _gcal_post(path: str, access_token: str, body: dict) -> dict:
    """POST to the Calendar API and return parsed JSON."""
    url = f"{GCAL_API_BASE}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {access_token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------

def list_events(
    calendar_id: str,
    access_token: str,
    time_min: str | None = None,
    time_max: str | None = None,
    max_results: int = 10,
) -> dict:
    """List upcoming events from a calendar.

    Args:
        calendar_id: Calendar identifier (e.g. 'primary').
        access_token: OAuth2 Bearer token.
        time_min: RFC3339 lower bound for event start time (optional).
        time_max: RFC3339 upper bound for event start time (optional).
        max_results: Maximum number of events to return (default 10).

    Returns:
        dict with keys: ok, events (list of {id, summary, start, end, location, status})
    """
    log.info("list_events calendar_id=%s max_results=%d", calendar_id, max_results)
    params: dict = {
        "maxResults": max_results,
        "singleEvents": "true",
        "orderBy": "startTime",
    }
    if time_min:
        params["timeMin"] = time_min
    if time_max:
        params["timeMax"] = time_max

    path = f"/calendars/{urllib.parse.quote(calendar_id, safe='')}/events"
    result = _gcal_get(path, access_token, params)

    events = [
        {
            "id": item.get("id"),
            "summary": item.get("summary", ""),
            "start": item.get("start", {}),
            "end": item.get("end", {}),
            "location": item.get("location", ""),
            "status": item.get("status", ""),
        }
        for item in result.get("items", [])
    ]
    return {"ok": True, "events": events}


def create_event(
    calendar_id: str,
    summary: str,
    start: str,
    end: str,
    access_token: str,
    description: str = "",
    location: str = "",
    attendees: list[str] | None = None,
) -> dict:
    """Create a new event on a Google Calendar.

    Args:
        calendar_id: Calendar identifier (e.g. 'primary').
        summary: Event title.
        start: RFC3339 start datetime string.
        end: RFC3339 end datetime string.
        access_token: OAuth2 Bearer token.
        description: Optional event description.
        location: Optional event location.
        attendees: Optional list of attendee email addresses.

    Returns:
        dict with keys: ok, event_id, html_link
    """
    log.info("create_event calendar_id=%s summary=%s", calendar_id, summary)
    body: dict = {
        "summary": summary,
        "description": description,
        "location": location,
        "start": {"dateTime": start, "timeZone": "UTC"},
        "end": {"dateTime": end, "timeZone": "UTC"},
    }
    if attendees:
        body["attendees"] = [{"email": addr} for addr in attendees]

    path = f"/calendars/{urllib.parse.quote(calendar_id, safe='')}/events"
    result = _gcal_post(path, access_token, body)

    return {
        "ok": True,
        "event_id": result.get("id"),
        "html_link": result.get("htmlLink"),
    }


def get_event(calendar_id: str, event_id: str, access_token: str) -> dict:
    """Fetch a single event by ID.

    Args:
        calendar_id: Calendar identifier.
        event_id: Event identifier.
        access_token: OAuth2 Bearer token.

    Returns:
        dict with keys: ok, id, summary, start, end
    """
    log.info("get_event calendar_id=%s event_id=%s", calendar_id, event_id)
    path = (
        f"/calendars/{urllib.parse.quote(calendar_id, safe='')}"
        f"/events/{urllib.parse.quote(event_id, safe='')}"
    )
    result = _gcal_get(path, access_token)
    return {
        "ok": True,
        "id": result.get("id"),
        "summary": result.get("summary", ""),
        "start": result.get("start", {}),
        "end": result.get("end", {}),
    }


# ---------------------------------------------------------------------------
# execute_call dispatcher
# ---------------------------------------------------------------------------

def execute_call(payload: dict) -> dict:
    """Dispatch to the appropriate function based on payload['action']."""
    action = payload.get("action")
    token = payload.get("access_token", "")

    if action == "list_events":
        return list_events(
            calendar_id=payload["calendar_id"],
            access_token=token,
            time_min=payload.get("time_min"),
            time_max=payload.get("time_max"),
            max_results=int(payload.get("max_results", 10)),
        )
    elif action == "create_event":
        return create_event(
            calendar_id=payload["calendar_id"],
            summary=payload["summary"],
            start=payload["start"],
            end=payload["end"],
            access_token=token,
            description=payload.get("description", ""),
            location=payload.get("location", ""),
            attendees=payload.get("attendees"),
        )
    elif action == "get_event":
        return get_event(
            calendar_id=payload["calendar_id"],
            event_id=payload["event_id"],
            access_token=token,
        )
    else:
        return {"ok": False, "error": f"unknown action: {action}"}


# ---------------------------------------------------------------------------
# NATS event handler
# ---------------------------------------------------------------------------

async def handle_event(nc, subject: str, raw: bytes) -> None:
    """Handle an inbound NATS message on the google-calendar-connector subject tree.

    Flow:
      1. Parse JSON from raw bytes.
      2. If the action requires approval, publish to cascadia.approvals.request
         and return — do NOT execute yet.
      3. Otherwise call execute_call, publish result to the response subject.
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
        body = json.dumps(
            {
                "status": "healthy",
                "connector": NAME,
                "version": VERSION,
                "port": PORT,
            }
        ).encode("utf-8")
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
    log.info("Health server listening on port %d", PORT)
    return thread


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _nats_main() -> None:
    try:
        import nats  # type: ignore
    except ImportError:
        log.warning("nats-py not installed — NATS subscribe disabled")
        # Keep process alive so health endpoint stays up
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
