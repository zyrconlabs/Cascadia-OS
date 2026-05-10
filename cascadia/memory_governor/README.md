# Cascadia Memory Governor

RAM-first, value-aware persistence for Cascadia.

## Status

Phase 2 — skeleton. All capabilities disabled by default.
Cascadia behaves identically to before this module existed
when all flags are at their defaults.

## Feature Flags

| Flag                          | Default | Purpose |
|-------------------------------|---------|---------|
| `MEMORY_GOVERNOR_ENABLED`     | false   | Master enable |
| `OUTBOX_ENABLED`              | false   | Idempotent external actions |
| `RAM_LOG_BUFFER_ENABLED`      | false   | RAM ring buffer for logs |
| `MISSION_COMPACTION_ENABLED`  | false   | Mission summary compaction |

Flags live in `flags.py` to avoid circular import risk
between submodules.

## Architectural Rules

1. Nothing in Cascadia may grow forever without a policy.
2. Nothing external may happen twice because of a crash.
3. RAM is for work. Disk is for truth. Memory is for
   value. Logs are for exceptions.

## Build Phases

- **Phase 2 (this commit)** — module skeleton, all flags off
- **Phase 3** — outbox implementation (highest value)
- **Phase 4** — RAM ring buffer + logger integration
- **Phase 5** — mission summary compaction
- **Phase 6** — retention enforcement

## Module Structure

| File              | Purpose |
|-------------------|---------|
| `__init__.py`     | Public API + flag re-exports |
| `flags.py`        | Feature flag env var reads |
| `classifier.py`   | Event type → category mapping |
| `ring_buffer.py`  | RAM log buffer (deque + lock) |
| `outbox.py`       | Idempotent external action queue |
| `compactor.py`    | Mission trace → summary |
| `policy.py`       | should_persist() decision |
| `retention.py`    | Disk usage enforcement |
| `schemas.py`      | SQLite DDL for durable tables |

## Testing

14 skeleton tests verify:
  - All public functions importable
  - All flags default to false (verified post-reload)
  - Flag-off behavior is no-op
  - Ring buffer respects capacity
  - Classifier returns valid categories
