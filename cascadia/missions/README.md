# Missions Layer — Data Foundation

The Missions layer provides the persistent data model for multi-step autonomous
business workflows in Cascadia OS.  This module contains only the data layer;
the runner, manager, and API endpoints are added in subsequent sessions.

## Running the migration

```bash
# From the cascadia-os root:
python -m cascadia.missions.migrate
```

The migration is **idempotent** — safe to run multiple times against a live or
empty database.  It never drops tables or modifies existing columns.

## Tables created (15 total)

| Table | Purpose |
|---|---|
| `organizations` | Top-level tenant record; default row inserted automatically |
| `missions` | Mission definitions (trigger config, schedule, status) |
| `mission_runs` | Execution instances of a mission |
| `mission_run_steps` | Individual step results within a run |
| `leads` | Prospective customers captured during a run |
| `lead_enrichments` | Third-party or AI-enriched data attached to a lead |
| `quotes` | Price quotes generated during a run |
| `purchase_orders` | Vendor purchase orders associated with a run |
| `invoices` | Customer invoices generated during a run |
| `campaigns` | Outreach campaigns (email, SMS, etc.) |
| `campaign_items` | Individual send units within a campaign |
| `review_requests` | Post-service review solicitations |
| `tasks` | Human action items surfaced by a run |
| `blockers` | Impediments that pause or fail a run |
| `briefs` | Structured documents (proposals, summaries) produced by a run |

The migration also extends the existing `approvals` table with two nullable
columns: `mission_id` and `mission_run_id`.

## Constants

`cascadia/missions/constants.py` defines the canonical status values for
`mission_runs.status`, retry policy limits, the default organization UUID, and
the Mission Manager service port.

## What comes next

- **Session 2** — Mission runner (execution engine, step dispatch)
- **Session 3** — Mission manager service (API, scheduling, PRISM integration)
- **Session 4** — PRISM dashboard panels for Missions
