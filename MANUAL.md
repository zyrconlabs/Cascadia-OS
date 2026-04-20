# Cascadia OS v0.43 — Manual

## What changed in v0.31

Two fully implemented operators added — SCOUT and RECON. Both run as standalone Flask services in the `7xxx` operator band, supervised by FLINT via manifest.json discovery. The persona folder architecture (job_description / company_policy / current_task) lets you change operator behavior by editing markdown files, no code changes needed.

---

## Architecture

### Startup tiers

| Tier | Components | Depends on |
|---|---|---|
| 1 | CREW, VAULT, SENTINEL, CURTAIN | — |
| 2 | BEACON, STITCH, VANGUARD, HANDSHAKE, BELL, ALMANAC | CREW (BEACON only) |
| 3 | PRISM | CREW, SENTINEL, BEACON |
| Operators | SCOUT (7000), RECON (7001) | Started independently or via FLINT |

### Kernel layer

Owns: process lifecycle, tier startup, health polling, restart/backoff, graceful shutdown, watchdog liveness, system status at `localhost:4011`.

Does not own: workflow planning, scheduling, approval UI, operator business logic.

### Operator layer

Operators are independent services. Each has:
- A `manifest.json` describing its id, port, capabilities, start command, and health path
- A `requirements.txt` with its own dependencies
- A `config.json` pointing to the LLM endpoint and vault directory
- A persona folder structure for configurable behavior

---

## SCOUT operator

### What it does
Inbound lead capture. Runs a streaming chat session with website visitors, qualifies them, extracts contact details using AI + regex double-pass, scores leads hot/warm/cold, and estimates deal value by project type and square footage.

### Start
```bash
cd cascadia/operators/scout
pip install -r requirements.txt
python scout_server.py
```

### Endpoints
```
GET  /bell                    — chat widget UI
GET  /doorbell                — embeddable iframe version
POST /api/stream              — SSE streaming chat
POST /api/chat                — sync chat
GET  /api/leads               — all captured leads
GET  /api/lead/<session_id>   — single lead with full conversation
GET  /api/stats               — totals, hot/warm counts, today's leads
GET  /api/health              — health check for FLINT
POST /api/clear               — wipe session and lead data (dev only)
```

### Persona system
Scout's behavior is driven entirely by three markdown folders:

```
scouts/lead-engine/
  job_description/role.md      — who Scout is, its personality, what it knows
  company_policy/policy.md     — rules, escalation language, what it cannot say
  current_task/task.md         — current focus, priority project types
```

Edit these files to change Scout's behavior. No code changes, no restart required — the system prompt rebuilds on each new session.

### Lead scoring
Scout automatically scores every lead:
- **Hot** — contact info provided + timeline under 60 days or urgent flag
- **Warm** — contact info provided, longer timeline
- **Cold** — no contact info

### Deal value estimation
Estimated deal value is calculated per project type and square footage:

| Project type | Rate range per sqft |
|---|---|
| Warehouse new | $8–$22 |
| Warehouse retrofit | $5–$16 |
| Industrial drafting | $6–$18 |
| Facility layout | $4–$14 |
| Dock design | $3–$10 |
| Rack layout | $3–$9 |

### Groq fallback
If the local LLM is unavailable, Scout falls back to Groq cloud inference automatically. Set `groq_api_key` in `scout.config.json` to enable.

---

## RECON operator

### What it does
Outbound research agent. Reads a task from `tasks/current/task.md`, runs search queries via DuckDuckGo, scores and deduplicates results, writes structured CSV output, and streams live progress to a dashboard.

### Start
```bash
cd cascadia/operators/recon
pip install -r requirements.txt
python recon_worker.py &    # research worker
python dashboard.py         # live dashboard
open http://localhost:7001/
```

### Task configuration
Create or edit `tasks/current/task.md` with YAML frontmatter:

```markdown
---
title: "Houston warehouse operators"
queries:
  - "warehouse operator Houston TX"
  - "3PL logistics Houston"
max_results: 50
cycles: 3
---

## Goal
Find industrial and warehouse operators in the Greater Houston area
who may need facility design or layout services.
```

### Output
Results are written to CSV in `data/vault/operators/recon/` with columns: title, url, description, score, source, timestamp.

### Known issues (fix before production use)
- Two simultaneous worker processes can cause state conflicts — ensure only one instance runs
- Inline comments in task.md YAML frontmatter break the parser — keep frontmatter values clean
- `state.json` model name must match your actual running model name exactly

---

## AI configuration

### Supported backends

| Provider | config value |
|---|---|
| Local llama.cpp | `llama-cpp` with `base_url: http://localhost:4011` |
| Ollama | `ollama` with `base_url: http://localhost:11434` |
| OpenAI | `openai` |
| Anthropic | `anthropic` |
| Groq | `groq` |
| Any OpenAI-compatible | `custom` |

### Setup wizard
```bash
python -m cascadia.installer.once           # browser wizard at :4010
python -m cascadia.installer.once --no-browser  # terminal prompts
```

---

## Runbook

### First-time install
```bash
python -m cascadia.installer.once
```

### Start the OS
```bash
python -m cascadia.kernel.watchdog --config config.json
```

### Start operators
```bash
cd cascadia/operators/scout && python scout_server.py &
cd cascadia/operators/recon && python recon_worker.py &
```

### Check system status
```bash
curl http://127.0.0.1:4011/api/flint/status
```

### Open PRISM dashboard
```bash
open http://127.0.0.1:6300/
```

### Run tests
```bash
python -m unittest discover -s tests -v
python tests/test_crash_recovery.py
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| PRISM shows blank page | Confirm `cascadia/dashboard/prism.html` exists |
| Setup wizard 500 error | Confirm `cascadia/installer/setup.html` exists next to `once.py` |
| Scout can't reach LLM | Check `bridge_url` in `scout.config.json` — should be `http://127.0.0.1:4011` |
| Recon YAML parse error | Remove inline comments from task.md frontmatter |
| Recon double-process conflict | `pkill -f recon_worker` then restart single instance |
| Component restarts repeatedly | Check heartbeat paths and ports in `config.json`, inspect `data/logs/` |
| Run resumes from wrong step | Inspect `steps` and `side_effects` tables in the database |
| Approval never wakes a run | Inspect `approvals` table and run's `run_state` |

---

## Port reference

| Port | Band | Component |
|---|---|---|
| 4010 | 4xxx — kernel | ONCE (install time only) |
| 4011 | 4xxx — kernel | FLINT status API |
| 5100–5103 | 5xxx — foundation | CREW, VAULT, SENTINEL, CURTAIN |
| 6200–6205 | 6xxx — runtime | BEACON, STITCH, VANGUARD, HANDSHAKE, BELL, ALMANAC |
| 6300 | 6xxx — visibility | PRISM |
| 7000 | 7xxx — operators | SCOUT |
| 7001 | 7xxx — operators | RECON |
| 7002+ | 7xxx — operators | future operators |
| 8200+ | 8xxx — expansion | GRID, DEPOT (roadmap) |

---

## Not in v0.31

- VANGUARD dynamic operator discovery (v0.35)
- SENTINEL enforcement end-to-end (v0.35)
- CURTAIN asymmetric key exchange (v0.35)
- HANDSHAKE HTTP execution to external APIs (v0.35)
- PRISM WebSocket real-time push (v0.35)
- BELL persona routing (v0.35)
- Model switcher in PRISM (v0.35)
