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

## macOS Permissions & TCC (Stage 2/3B)

The live connector requests the three macOS permissions (Calendar, Reminders, Notes automation) proactively at startup, from its own process. On a fresh install this means the connector itself surfaces the permission dialogs and receives the live post-grant status — no separate probe script and no restart are needed for the first-run grant. Domains that are already granted or denied are logged and skipped, so a normal restart never re-opens a dialog.

### Restart after a permission is changed later

If Calendar/Reminders/Notes permission for apple_local is changed **outside of first-run** (e.g. revoked and re-granted via System Settings > Privacy & Security), restart the connector to pick up the new status:

```
launchctl kickstart -k gui/$(id -u)/ai.zyrcon.apple-local
```

This is due to EventKit caching authorization status per-process — not a bug, just how the framework works. The `/health` endpoint reports the accurate per-domain grant status (granted / denied / not_yet_asked) once the connector has restarted.
