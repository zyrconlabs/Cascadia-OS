# Cascadia Kernel Stack v2.1 Manual

## Purpose
Cascadia v2.1 is the first build that turns the latest design direction into a practical stack:
- the **kernel remains small and supervisory**
- durable execution is strengthened with **queryable side effects** and **queryable approvals**
- operator-assets are described by a common **manifest schema**
- missing dependencies and gated actions become visible runtime states instead of hidden failure modes

## Architecture in one view
### Kernel layer
Owns:
- process lifecycle
- dependency-tier startup
- health polling
- restart with backoff
- graceful draining and shutdown
- top-level system status

Does not own:
- workflow planning
- task scheduling
- approval UI
- installer/store logic
- merge/workspace logic

### Core runtime services
- `queue.py` — priority queue service
- `town.py` — operator registry / message hub
- `router.py` — capability-aware routing stub
- `sentinel.py` — resource arbitration stub
- `vault_api.py` — memory access service

### Durability layer
- `run_store.py`
- `migration.py`
- `step_journal.py`
- `idempotency.py`
- `resume_manager.py`

### Policy and gating
- `runtime_policy.py`
- `approval_store.py`
- `dependency_manager.py`

### Observability
- `run_trace.py`

### Operator assets
Starter manifests included:
- `main_operator.json`
- `gmail_operator.json`
- `calendar_operator.json`

## State model
### ProcessState
- `starting`
- `ready`
- `degraded`
- `draining`
- `offline`

### RunState
- `pending`
- `running`
- `blocked`
- `retrying`
- `waiting_human`
- `poisoned`
- `complete`
- `failed`
- `abandoned`

v2.1 deliberately uses `retrying` instead of a separate `resuming` state.

## Database schema
Tables:
- `meta`
- `runs`
- `steps`
- `side_effects`
- `approvals`
- `run_trace`

### runs
Key columns:
- `run_id`
- `operator_id`
- `tenant_id`
- `goal`
- `current_step`
- `input_snapshot`
- `state_snapshot`
- `retry_count`
- `last_checkpoint`
- `process_state`
- `run_state`
- `blocked_reason`
- `blocking_entity`
- `dependency_request`
- `created_at`
- `updated_at`

### steps
Append-only step ledger.
`step_index` is **0-based**.

### side_effects
One row per external action.
Statuses: `planned`, `committed`, `failed`, `compensated`.

### approvals
One row per approval request/decision.
Decisions: `pending`, `approved`, `denied`.

## Runtime flows
### Resume flow
1. load run
2. scan completed steps in ascending order
3. stop at the first incomplete step or a step whose side effects are not fully committed
4. restore state from the last fully committed step
5. resume from `last_committed + 1`

### Approval-aware resume
If a run is `waiting_human` and approval is still pending, it does not auto-resume.
If approved, `approval_store.py` moves the run to `retrying`.
If denied, the run becomes `failed`.

### Dependency blocking
`dependency_manager.py` checks:
- required operators installed and healthy
- requested permissions granted

If something is missing, it writes:
- `run_state = blocked`
- `blocked_reason`
- `blocking_entity`
- `dependency_request`

It does **not** install, fix, or retry dependencies.

## Operator-asset manifest
Fields:
- `id`
- `name`
- `version`
- `type` (`system`, `service`, `skill`, `composite`)
- `capabilities`
- `required_dependencies`
- `requested_permissions`
- `autonomy_level` (`manual_only`, `assistive`, `semi_autonomous`, `autonomous`)
- `health_hook`
- `description`

Important: `autonomy_level` is metadata only in v2.1.

## Runbook
### Run tests
```bash
python -m unittest discover -s tests -v
```

### Start stack
```bash
python -m cascadia.watchdog --config config.json
```

### Query kernel status
```bash
curl http://127.0.0.1:18791/api/kernel/status
```

### Troubleshooting
- If the kernel restarts repeatedly, check heartbeat paths, ports, and component logs in `data/logs`.
- If a run resumes from the wrong step, inspect `steps` and `side_effects`.
- If approval never wakes a run, inspect `approvals` and the run’s `run_state`.
- If a dependency block is unclear, inspect `blocked_reason`, `blocking_entity`, and `dependency_request`.

## Not in v2.1
Deliberately excluded:
- workflow planner
- scheduler
- trigger manager
- handoff policy
- operator store / installer / updater
- workspace manager / merge manager
- permission broker
- autonomy enforcement engine
- multi-node HA
- microVM isolation

## Bottom line
This stack is designed to be **trustworthy before clever**.
It locks the data model, proves the resume path, adds operator metadata, and makes dependency and approval gating explicit.
