# Scheduler Connector (CON-115)

**Port:** 9987  
**Tier:** Lite+  
**Category:** Runtime  

Schedules one-shot (delayed) and recurring (cron-style) jobs. When a job fires, it publishes a NATS event to the configured `target_subject`.

## HTTP Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/jobs` | Create a job |
| `GET` | `/jobs` | List all jobs |
| `GET` | `/jobs/{job_id}` | Get a specific job |
| `DELETE` | `/jobs/{job_id}` | Cancel a job |
| `GET` | `/health` | Health check |

## Creating a Job

### One-shot (fires once at a specific time)

```json
{
  "name": "send-quarterly-report",
  "target_subject": "cascadia.operators.email-outbound.send",
  "schedule": "once",
  "run_at": 1735689600,
  "payload": {"template": "quarterly_report", "recipient": "ceo@example.com"}
}
```

### Recurring (cron expression)

```json
{
  "name": "daily-pipeline-audit",
  "target_subject": "cascadia.operators.pipeline-hygiene.run",
  "schedule": "0 8 * * *",
  "payload": {"org_id": "acme"}
}
```

### Recurring (fixed interval in seconds)

```json
{
  "name": "heartbeat",
  "target_subject": "cascadia.ops.heartbeat",
  "schedule": "interval",
  "interval_seconds": 300,
  "payload": {}
}
```

## NATS

**Subscribe (control):** `cascadia.connectors.scheduler-connector.>`

| Subject | Action |
|---|---|
| `…scheduler-connector.create` | Create a job |
| `…scheduler-connector.cancel` | Cancel a job (`{"job_id": "…"}`) |
| `…scheduler-connector.list` | List all jobs |

**Published when job fires:** the `target_subject` you configured.

### Fire envelope

```json
{
  "connector": "scheduler-connector",
  "job_id": "abc-123",
  "job_name": "daily-pipeline-audit",
  "payload": { ... },
  "fired_at": "2025-01-01T08:00:00+00:00",
  "run_count": 1
}
```

## Cron syntax

5-field standard cron. Supports `*`, `*/n`, and comma-separated values. Examples:

| Expression | Meaning |
|---|---|
| `* * * * *` | Every minute |
| `0 8 * * *` | Daily at 08:00 UTC |
| `*/15 * * * *` | Every 15 minutes |
| `0 9 * * 1` | Every Monday at 09:00 UTC |
