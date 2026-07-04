#!/usr/bin/env bash
# Apple Local Connector — Stage 2 install / permission preflight.
#
# No packages are installed here (pyobjc-framework-EventKit is declared in
# pyproject.toml and installed with the core deps). This hook instead reports
# the macOS TCC grant state for each domain the connector touches:
#   Calendar / Reminders  -> EventKit authorizationStatus (never prompts)
#   Notes                 -> lightweight osascript probe (short timeout)
# Output is tri-state per domain: granted / denied / not_yet_asked.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"
# Prefer the Air venv interpreter; fall back to whatever python3 is on PATH.
PY="$REPO_ROOT/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3 || true)"
fi
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

echo "Apple Local Connector — permission preflight"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "  platform: non-macOS — Apple adapters are unavailable here."
  echo "Apple Local Connector installed (no packages installed)."
  exit 0
fi

if [[ -z "$PY" ]]; then
  echo "  WARNING: no python3 found; cannot probe TCC grants."
  echo "Apple Local Connector installed (no packages installed)."
  exit 0
fi

"$PY" - <<'PYEOF' || echo "  WARNING: preflight probe failed (EventKit/pyobjc may be missing)."
try:
    from cascadia.connectors.apple_local.eventkit_adapters import permission_state
    from cascadia.connectors.apple_local.applescript_notes import notes_permission_state
    import EventKit
except Exception as exc:  # noqa: BLE001
    print(f"  probe unavailable: {exc}")
    raise SystemExit(0)

cal = permission_state(EventKit.EKEntityTypeEvent)
rem = permission_state(EventKit.EKEntityTypeReminder)
notes = notes_permission_state()

def line(name, state):
    hint = {
        "granted": "OK",
        "denied": "grant in System Settings > Privacy & Security",
        "not_yet_asked": "will prompt on first use",
        "write_only": "partial — full access recommended",
    }.get(state, "unknown")
    print(f"  {name:<10} {state:<14} ({hint})")

line("calendar", cal)
line("reminders", rem)
line("notes", notes)
PYEOF

echo "Apple Local Connector installed (no packages installed)."
