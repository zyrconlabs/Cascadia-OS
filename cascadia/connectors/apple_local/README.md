# Apple Local Connector

Phase 1 is a safe skeleton for future local Apple Calendar, Reminders, and Notes integration.

This connector is local-only. Its HTTP health server binds to `127.0.0.1:9601`, declares no network access, and does not use OAuth.

## What Phase 1 Does

- Provides a DEPOT manifest for `apple-local-connector`.
- Exposes `GET /health` and `GET /api/health` with the same JSON shape.
- Adds a mockable adapter boundary for Calendar, Reminders, and Notes.
- Gracefully reports degraded readiness on non-macOS platforms.
- Returns safe placeholder or unavailable responses for read-only actions.

## What Phase 1 Does Not Do

- No real Apple Calendar, Reminders, or Notes access.
- No EventKit usage.
- No `osascript` usage.
- No create, update, complete, archive, or delete mutations.
- No background sync or database migrations.

## Supported Read-Only Stub Actions

- `calendar.list_calendars`
- `calendar.list_events`
- `calendar.get_event`
- `reminders.list_lists`
- `reminders.list_items`
- `reminders.get_item`
- `notes.list_folders`
- `notes.search`
- `notes.get_note`

These actions return either an empty placeholder result from injected test adapters or an unavailable response from the default Phase 1 adapters.

## Write and Delete Safety

The manifest declares write and delete permissions because future phases will support mutations. In Phase 1, mutating actions are blocked. Without approval they return `approval_required`; even with approval they return `phase_1_not_implemented` and do not touch Apple apps.

Hard delete for Notes is disabled by default through the `notes_hard_delete_enabled` setup field.

## Future macOS Permissions

Future real integration will need explicit macOS user permissions for Calendar, Reminders, and Notes automation or native framework access. Those permissions are not requested in Phase 1.
