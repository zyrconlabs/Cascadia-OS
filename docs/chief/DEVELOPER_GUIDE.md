# CHIEF — Developer Guide

CHIEF is the task orchestrator for Cascadia OS. It owns routing decisions and
operator dispatch. It does not own operator execution, channel I/O, or session
management.

---

## Architecture Overview

```
Telegram
  │
  ▼ POST /webhook
Telegram Connector (9000)
  │
  ▼ POST /inbound
VANGUARD (6202)          — sends ACK "⏳ Received…" to Telegram
  │
  ▼ POST /task
CHIEF (6211)             — routing decision + dispatch
  │
  ▼ POST /route
BEACON (6200)            — operator forwarder
  │
  ▼ POST /api/task
Operator (e.g. RECON 8002)
  │
  reply propagates back up the chain
  VANGUARD POSTs final reply to Telegram Connector /send
```

---

## Routing Flow (handle_task)

```
Step 0  Slash command fast-path        /recon, /quote, /help, etc.
                                       100% accuracy, no LLM, no keyword scan
        ↓ (not a slash command)
Step A  Status commands fast-path      /status, /missions via select_target()
                                       Not recorded in history
        ↓
Step B  Keyword fast-path              confidence ≥ 0.90
                                       "run recon" → recon (confidence 1.0)
                                       No LLM call
        ↓ (confidence < 0.90)
Step C  LLM intent classifier          classify_intent(msg, history, chat_id)
                                       Returns RoutingDecision JSON
        ↓
Step D  Validation gates               validate_routing_decision()
                                       Rejects unknown operators
                                       Downgrades in_development → ask_clarification
                                       Enforces required inputs
        ↓
Step E  Confidence thresholds
                                       ≥ 0.80 → dispatch_operator (Step F)
                                       0.55–0.79 → ask_clarification (Step G)
                                       < 0.55 → conversation fallback (Step I)
        ↓
Step F  dispatch_operator              → BEACON → operator
Step G  ask_clarification             → return question to user
Step H  multi_step_plan               → dispatch first target, plan summary
Step I  conversation / fallback       → intelligent_fallback() (3-tier)
```

---

## Confidence Thresholds

```python
CONFIDENCE_DISPATCH = 0.80   # dispatch operator
CONFIDENCE_CLARIFY  = 0.55   # ask clarification
# below 0.55 → conversation fallback
```

---

## Adding a New Operator

1. **Register with CREW** — operator `manifest.json` must have `port` and
   `task_hook: "/api/task"` so BEACON knows where to forward.

2. **Add to OPERATOR_CATALOG** in `cascadia/chief/intent_router.py`:
   ```python
   "my_operator": {
       "display_name": "My Operator",
       "description": "What it does",
       "example_phrases": ["phrase 1", "phrase 2"],
       "required_inputs": ["param_a"],
       "status": "available",   # or "in_development"
   }
   ```

3. **Add keywords** in `cascadia/chief/operator_selector.py` under
   `_KEYWORD_MAP`:
   ```python
   {
       "keywords": ["my trigger phrase", "another phrase"],
       "preferred_operators": ["my_operator"],
       "capabilities": ["my.capability"],
   }
   ```

4. **Add slash command** (optional) in `cascadia/chief/commands.py`:
   ```python
   "/myop": {"operator": "my_operator", "description": "Run My Operator"},
   ```

5. **Register with Telegram** (if slash command added):
   ```bash
   python3 scripts/register_telegram_commands.py
   ```

6. **Write tests** in `tests/test_intent_router.py` and
   `tests/test_chief_messaging.py`.

---

## LLM Context Injection

Every `classify_intent()` call receives:

```
[system prompt]           ← _build_system_prompt(chat_id)
  base routing rules
  OPERATOR_CATALOG
  MISSION_CATALOG
  + CURRENT SESSION CONTEXT (if last_action exists):
      Last action: dispatch_operator
      Last operator used: recon
      Last result summary: ✅ Scan complete. +35 leads…
      Resolve "do it again" → recon
      Resolve "those contacts" → last result above

[history messages]        ← get_history(chat_id), last 6 messages (3 turns)

[new user message]
```

---

## Files

```
cascadia/chief/
  server.py            Main orchestrator — handle_task(), dispatch, reply
  intent_router.py     LLM classifier, RoutingDecision, history store,
                       last_action store, validate_routing_decision()
  operator_selector.py Keyword fast-path — _KEYWORD_MAP, select_target()
  fallback.py          3-tier conversational fallback (tier 2/3)
  commands.py          Slash command parser — parse_command(), build_help_text()
  models.py            TaskRequest, TaskResponse dataclasses

tests/
  test_intent_router.py   29 unit tests (offline, LLM mocked)
  test_chief.py           12 integration tests
  test_chief_messaging.py 20 live end-to-end tests (requires running services)

scripts/
  register_telegram_commands.py  One-time BotFather command registration
```

---

## Port Map

| Port | Service              |
|------|----------------------|
| 6211 | CHIEF                |
| 6200 | BEACON               |
| 6202 | VANGUARD             |
| 5100 | CREW                 |
| 4011 | Local LLM (zyrcon-3b via FLINT) |
| 8002 | RECON dashboard      |
| 9000 | Telegram connector (@ZyrconBot) |

---

## Audit Logging

Every LLM routing decision is logged to `data/logs/chief.log`:

```
INTENT_ROUTER | msg="run recon" | action=dispatch_operator | target=recon |
confidence=0.95 | reason="..." | last_action=dispatch_operator |
validated=pass | final_action=dispatch_operator
```

---

## Adding a New Slash Command (full checklist)

1. Add entry to `COMMANDS` in `cascadia/chief/commands.py`
2. If it needs special server-side handling (not a straight operator dispatch),
   add a branch in the Step 0 block in `cascadia/chief/server.py`
3. Add the command to the `COMMANDS` list in
   `scripts/register_telegram_commands.py`
4. Run `python3 scripts/register_telegram_commands.py` once
5. Add a test case to `tests/test_chief_messaging.py` (Group 2)

---

## History and State Architecture

```
_chat_history: dict[str, deque]    keyed by chat_id, maxlen=6 (3 turns)
_last_action:  dict[str, dict]     keyed by chat_id
                                   {action, target, result_preview}

append_history(chat_id, role, content)   — called after every reply
get_history(chat_id)                     — passed to classify_intent()
set_last_action(chat_id, ...)            — called after every dispatch/reply
get_last_action(chat_id)                 — read by _build_system_prompt()
```

Both stores are **in-memory only** — cleared on CHIEF restart. This is
intentional: conversation context is ephemeral. Persistent state (leads, jobs)
is owned by the operators, not CHIEF.
