# PRISM Dashboard — Manual

PRISM is the browser-based visibility layer for Cascadia OS. It is developed as a separate project from the kernel and can be swapped, extended, or replaced independently. The kernel exposes a stable JSON API that PRISM reads; PRISM owns nothing that the kernel depends on.

---

## Architecture

```
Cascadia OS kernel (port 6300)
  └── /api/prism/overview      ← PRISM reads this
  └── /api/prism/system
  └── /api/prism/crew
  └── /api/prism/runs
  └── /api/prism/approvals
  └── /api/prism/blocked
  └── /api/prism/workflows

PRISM dashboard (prism.html)
  └── single-file, zero dependencies
  └── opens in any browser
  └── polls every 5 seconds
  └── stores session state in IndexedDB (Vault v3)
```

PRISM is a single HTML file. There is no build step, no server to run, no npm install. Open it in a browser while Cascadia OS is running and it works.

---

## Surfaces

PRISM has six surfaces, selectable from the nav rail on the left.

### Ops
The main working surface. The sidebar lists Beacon (system orchestrator) and all installed operators (RECON, SCOUT, QUOTE, CHIEF, Aurelia, Debrief, and others) with live online/offline status. Select any operator to open a chat panel. Kernel components (CREW, VAULT, SENTINEL, etc.) are visible in the Health tab only — they are infrastructure, not operators.

**Beacon commands:**
- `/status` — queries `/api/prism/overview` and renders a live component health grid with healthy count, run count, and pending approvals
- `/health` — runs a full component check and prints a line-by-line readout of all 11 services
- `/costs` — tallies session run costs from local vault

**Component commands (any non-Beacon component):**
- `/detailed-status` — shows completion, task count, model, and session run cost
- `/recent-tasks` — lists the last 5 runs from session vault
- `/logs` — prints live log tail if worker panel is connected

### Runs
Full run history. Every message sent to a component creates a run entry in the local IndexedDB vault. Runs show status (running/completed/failed), duration, token count, cost, and tools used. Click any run to expand its event timeline. Use the filters to narrow by status or component.

**Run detail shows:**
- Event timeline (created → queued → started → tool calls → completed)
- Token in/out counts
- Per-run cost in USD
- Replay button (re-sends the same task)

### Observability
Real metrics from the local vault, plus live data from the PRISM API when Cascadia is connected. Shows completed/failed run counts, total cost, average run duration, tokens used, and cost-by-component bar charts. The system log at the bottom shows live connection state.

### Approvals
When a Cascadia run reaches a `waiting_human` state, PRISM surfaces the approval here. Each card shows the action type, risk level (high/medium/low), the payload the operator wants to execute, and Approve/Reject buttons. Resolving an approval writes to the audit log in the local vault and calls the PRISM approvals API.

### Studio
A drag-and-drop flow builder for designing operator workflows visually. Nodes represent workers, models, tools, control points, and outputs. Connect them with edges. Select a node to edit its properties in the inspector. Use "Run step" to test a single node against the live system.

Studio drafts are saved locally. Export is planned for v0.3.

### Admin
User management, API key storage (persisted in IndexedDB), vault statistics, audit log, and the Gateway Contract health check. The contract check pings all PRISM API endpoints and shows which ones are responding.

---

## Connection

PRISM connects to Cascadia OS at `http://127.0.0.1:6300`. The green/red dot in the sidebar header shows live connection state. PRISM polls every 5 seconds. If Cascadia stops, components go red and the dot turns red. When Cascadia restarts, PRISM reconnects automatically on the next poll cycle.

To open PRISM manually:
1. Start Cascadia: `cascadia`
2. Open `cascadia/dashboard/prism.html` in a browser
3. Or navigate to `http://127.0.0.1:6300` (if PRISM is served directly by the PRISM component)

---

## Local Vault (IndexedDB)

PRISM stores session data locally in the browser's IndexedDB under the key `prism_vault_v3`. This includes:

- **messages** — chat history per component
- **runs** — all run records with events, tokens, cost
- **files** — ingested documents and their chunk counts
- **apiKeys** — saved API keys (stored in browser only, never sent to kernel)
- **auditLog** — approval decisions, file ingests, vault clears

The vault persists across page reloads. Use Admin → Vault → Clear All to reset it. Export All dumps the full vault as JSON.

---

## Color Palette

PRISM uses the Cascadia OS design palette:

| Variable | Value | Usage |
|---|---|---|
| `--prism-a` | `#4facfe` | Primary accent, active states |
| `--prism-b` | `#a78bfa` | Secondary accent, gradients |
| `--prism-c` | `#34d399` | Success, healthy, ready |
| `--prism-d` | `#f472b6` | Highlight, special states |
| `--gold` | `#D4AF37` | Warnings, pending approvals |
| `--text` | `#0a0a0a` | Primary text |
| `--text-dim` | `#4a5568` | Secondary text |
| `--text-faint` | `#94a3b8` | Placeholder, metadata |

Font: `Inter` (body), `JetBrains Mono` (code, metrics, timestamps).

---

## PRISM API Reference

All endpoints are served by the PRISM component at `http://127.0.0.1:6300`.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/prism/overview` | Full system snapshot — component states, runs, attention required |
| GET | `/api/prism/system` | FLINT component states only |
| GET | `/api/prism/crew` | Active operators from CREW |
| GET | `/api/prism/runs` | Recent run states from run store |
| POST | `/api/prism/run` | `{run_id}` → full run detail |
| GET | `/api/prism/approvals` | Pending human decisions |
| GET | `/api/prism/blocked` | Blocked runs |
| GET | `/api/prism/workflows` | STITCH workflows |
| GET | `/health` | Component health check |

`/api/prism/overview` is the primary endpoint PRISM polls. It returns:

```json
{
  "cascadia_os": "v0.43",
  "generated_at": "...",
  "system": {
    "flint_state": "ready",
    "components_healthy": "11/11",
    "component_states": {
      "crew": "ready",
      "vault": "ready",
      ...
    }
  },
  "crew": { "operator_count": 0, "operators": [] },
  "runs": [],
  "attention_required": {
    "pending_approvals": 0,
    "blocked_runs": 0,
    "approvals": [],
    "blocked": []
  }
}
```

---

## Development Notes

PRISM is intentionally a single HTML file with no build toolchain. This is a deliberate choice for the v0.x series — it keeps deployment trivial (copy one file, open in browser) and makes it easy to inspect and modify. The tradeoff is that the file is large. This will be revisited if complexity warrants it.

The GatewayContract object at the top of the script defines all backend calls. To connect PRISM to a different backend, replace the contract implementations — the UI code above it does not change.

PRISM should be treated as a separate project from the Cascadia kernel. The kernel exposes a stable API; PRISM reads it. Changes to PRISM should not require changes to the kernel, and kernel changes should not break PRISM as long as the API contract is honored.

---

## Changelog

**v2.2 — Cascadia OS v0.43**
- Sidebar now shows operators only — Beacon at top, installed operator agents below
- Kernel components moved to Health tab exclusively — never in chat sidebar
- `cell` → `operator` rename throughout (CSS classes, JS variables, HTML ids)
- Settings surface — hardware detection, AI mode picker, model size selector
- Health surface — kernel components + operator agents with live status
- Terminal surface — dark terminal UI (WebSocket backend on roadmap v0.5)
- Almanac overlay — bottom-right floating chat assistant
- SwiftBar branding updated to Z·AI — kernel and AI model status only
- Beacon restored as first sidebar item — system orchestrator with routing tag
- `refreshOperators` reads `/api/prism/operators` — live from registry.json
- `demoDB` fallback shows registered operators in offline state

**v2.1 — Cascadia OS edition**
- Wired to Cascadia OS PRISM endpoint at `:6300` instead of Zyrcon Gateway at `:7001`
- Replaced navy dark theme with Cascadia light palette (Inter + JetBrains Mono)
- `refreshCells` reads `/api/prism/overview` and maps installed operators to sidebar
- Beacon `/status` and `/health` commands now query live PRISM data
- Approvals surface reads from `attention_required` in the overview response
- `demoDB` fallback shows all 11 Cascadia components in offline state
- Nav logo updated from Z → C

**v2.1 — original**
- Runs surface with full event timeline and replay
- Observability surface with real vault metrics
- Approvals surface with resolve flow
- Studio drag-and-drop flow builder
- Admin surface with audit log and vault export
- IndexedDB Vault v3 with persistent runs, files, API keys
- Gateway Contract pattern for swappable backends
- Thought stream polling
- Worker panel with live status and log tail
