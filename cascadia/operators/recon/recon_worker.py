#!/usr/bin/env python3
"""
Recon Worker — Zyrcon Labs
Lean, local-first research agent.
"""

import csv
import json
import logging
import os
import re
import signal
import sys
import time
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

# ─── Paths ───────────────────────────────────────────────────────────────────
BASE_DIR    = Path("./data/vault/operators/recon")
POLICY_DIR  = BASE_DIR / "policy"
JOB_DIR     = BASE_DIR / "job"
CURRENT_DIR = BASE_DIR / "tasks" / "current"
ARCHIVE_DIR = BASE_DIR / "tasks" / "archive"
OUTPUT_DIR  = BASE_DIR / "output"
LOGS_DIR    = BASE_DIR / "logs"
STATE_FILE    = BASE_DIR / "state.json"
THOUGHTS_FILE = BASE_DIR / "thoughts.json"
THOUGHTS_MAX  = 40   # ring buffer size shown in Prism

# ─── Limits ──────────────────────────────────────────────────────────────────
MAX_ROWS_PER_FILE = 500
MAX_FILE_MB       = 2
SEARCH_RESULTS    = 8   # results per query per cycle

# ─── LLM endpoints ───────────────────────────────────────────────────────────
LLM_ENDPOINTS = {
    "zyrcon-3b":  "http://127.0.0.1:4011/v1/chat/completions",
    "zyrcon-7b":  "http://127.0.0.1:4011/v1/chat/completions",
    "zyrcon-fast": "http://127.0.0.1:4011/v1/chat/completions",
}
DEFAULT_MODEL    = "zyrcon-3b"
ANTHROPIC_MODEL  = "qwen2.5-3b-instruct-q4_k_m.gguf"   # local llama.cpp model via FLINT proxy

# ─── Logging ─────────────────────────────────────────────────────────────────
LOGS_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOGS_DIR / "worker.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("recon")

# ─── Graceful shutdown ────────────────────────────────────────────────────────
_shutdown = False
def _handle_signal(sig, frame):
    global _shutdown
    log.info("Signal received — finishing current cycle then stopping.")
    _shutdown = True

signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ══════════════════════════════════════════════════════════════════════════════
# YAML-ish frontmatter parser
# ══════════════════════════════════════════════════════════════════════════════

def _parse_yaml_value(val: str):
    val = val.strip()
    if val.lower() in ("true", "yes"):   return True
    if val.lower() in ("false", "no"):   return False
    if val.lower() in ("null", "~", ""): return None
    try:    return int(val)
    except: pass
    try:    return float(val)
    except: pass
    return val.strip("\"'")

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML-ish frontmatter and body from a markdown file."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    end = next((i for i, l in enumerate(lines[1:], 1) if l.strip() == "---"), None)
    if end is None:
        return {}, text

    fm_lines = lines[1:end]
    body     = "\n".join(lines[end + 1:])
    result   = {}
    stack    = [(result, -1)]   # (current_dict, indent_level)
    list_owner: tuple | None = None   # (owner_dict, key) — where list items belong

    for line in fm_lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)

        # ── List item ────────────────────────────────────────────────────
        if stripped.startswith("- "):
            content = stripped[2:].strip()
            if ":" in content:
                k, _, v = content.partition(":")
                item = {k.strip(): _parse_yaml_value(v)}
            else:
                item = _parse_yaml_value(content)

            if list_owner:
                owner_dict, owner_key = list_owner
                if not isinstance(owner_dict.get(owner_key), list):
                    owner_dict[owner_key] = []
                owner_dict[owner_key].append(item)
            continue

        # ── Key: value ───────────────────────────────────────────────────
        if ":" in stripped:
            k, _, v = stripped.partition(":")
            k = k.strip()
            v = v.strip()

            # Pop stack to correct indent
            while len(stack) > 1 and stack[-1][1] >= indent:
                stack.pop()

            current_dict = stack[-1][0]

            if v == "":
                # Nested mapping or list incoming — keep current_dict as owner
                new_dict = {}
                current_dict[k] = new_dict
                stack.append((new_dict, indent))
                list_owner = (current_dict, k)   # list items go into current_dict[k]
            else:
                current_dict[k] = _parse_yaml_value(v)
                list_owner = None

    return result, body


# ══════════════════════════════════════════════════════════════════════════════
# File loaders
# ══════════════════════════════════════════════════════════════════════════════

def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""

def load_task() -> dict:
    task_file = CURRENT_DIR / "task.md"
    if not task_file.exists():
        log.error("No task file found at %s", task_file)
        sys.exit(1)
    fm, body = parse_frontmatter(task_file.read_text(encoding="utf-8"))
    fm["_body"] = body
    fm["_path"] = str(task_file)
    return fm

def load_policy() -> str:
    texts = []
    for md in sorted(POLICY_DIR.glob("*.md")):
        texts.append(f"## {md.stem}\n{md.read_text()}")
    return "\n\n".join(texts)

def load_job_description() -> str:
    jd = JOB_DIR / "job-description.md"
    return jd.read_text(encoding="utf-8") if jd.exists() else ""

def write_task_status(status: str):
    task_file = CURRENT_DIR / "task.md"
    content = task_file.read_text(encoding="utf-8")
    content = re.sub(r"^status:\s*\S+", f"status: {status}", content, flags=re.MULTILINE)
    task_file.write_text(content, encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# State management
# ══════════════════════════════════════════════════════════════════════════════

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))

_thought_counter = 0
def log_thought(text: str, kind: str = "info"):
    """Write a human-readable thought to thoughts.json (read by Prism Dashboard)."""
    global _thought_counter
    _thought_counter += 1
    entry = {
        "id":   _thought_counter,
        "time": datetime.now().strftime("%H:%M:%S"),
        "type": kind,   # search | think | write | cycle | info | stop
        "text": text,
    }
    thoughts = []
    if THOUGHTS_FILE.exists():
        try:
            thoughts = json.loads(THOUGHTS_FILE.read_text())
        except Exception:
            thoughts = []
    thoughts.append(entry)
    thoughts = thoughts[-THOUGHTS_MAX:]   # keep ring buffer
    THOUGHTS_FILE.write_text(json.dumps(thoughts, indent=2))

def init_state(task: dict) -> dict:
    now = datetime.now().isoformat()
    state = {
        "task_name":   task.get("name", "unnamed"),
        "start_time":  now,
        "start_date":  datetime.now().strftime("%Y-%m-%d"),
        "cycle":       0,
        "total_rows":  0,
        "seen_hashes": [],
        "model":       task.get("model", DEFAULT_MODEL),
        "status":      "running",
        "last_cycle":  None,
        "query_index": 0,
    }
    save_state(state)
    return state


# ══════════════════════════════════════════════════════════════════════════════
# Stop condition checker
# ══════════════════════════════════════════════════════════════════════════════

def parse_duration(s: str) -> timedelta:
    """Parse '60m', '24h', '7d' into timedelta."""
    s = str(s).strip().lower()
    if s.endswith("m"): return timedelta(minutes=int(s[:-1]))
    if s.endswith("h"): return timedelta(hours=int(s[:-1]))
    if s.endswith("d"): return timedelta(days=int(s[:-1]))
    return timedelta(hours=int(s))

def should_stop(task: dict, state: dict) -> tuple[bool, str]:
    """Returns (stop, reason)."""
    # File-based stop signal
    fresh = load_task()
    if str(fresh.get("status", "active")).lower() == "stop":
        return True, "status:stop set in task file"

    # Dashboard stop (state file)
    fresh_state = load_state()
    if fresh_state.get("status") == "stop":
        return True, "stop triggered via dashboard"

    stop = task.get("stop", {})
    mode = str(stop.get("mode", "status")).lower()

    if mode == "quantity":
        target = int(stop.get("quantity", 0))
        if state["total_rows"] >= target:
            return True, f"quantity target reached ({state['total_rows']}/{target})"

    elif mode == "time":
        duration = parse_duration(stop.get("time", "24h"))
        start    = datetime.fromisoformat(state["start_time"])
        elapsed  = datetime.now() - start
        if elapsed >= duration:
            return True, f"time limit reached ({str(elapsed).split('.')[0]})"

    if _shutdown:
        return True, "SIGINT/SIGTERM received"

    return False, ""


# ══════════════════════════════════════════════════════════════════════════════
# Output — daily folder + part-file splitting
# ══════════════════════════════════════════════════════════════════════════════

def get_day_folder(task_name: str, start_date_str: str) -> Path:
    start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    today = datetime.now().date()
    day_num = (today - start).days + 1
    folder  = OUTPUT_DIR / task_name / f"day-{day_num:02d}-{today.isoformat()}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder

def get_current_part(day_folder: Path) -> Path:
    """Return current part file, rolling over if size/row limit hit."""
    parts = sorted(day_folder.glob("part-*.csv"))
    if not parts:
        return day_folder / "part-001.csv"

    latest = parts[-1]
    if latest.stat().st_size >= MAX_FILE_MB * 1024 * 1024:
        part_num = int(latest.stem.split("-")[1]) + 1
        return day_folder / f"part-{part_num:03d}.csv"

    with open(latest, newline="", encoding="utf-8") as f:
        rows = sum(1 for _ in csv.reader(f))
    if rows > MAX_ROWS_PER_FILE:   # header + data rows
        part_num = int(latest.stem.split("-")[1]) + 1
        return day_folder / f"part-{part_num:03d}.csv"

    return latest

def write_rows(rows: list[dict], task: dict, state: dict) -> int:
    """Write validated rows to the correct day/part file. Returns count written."""
    if not rows:
        return 0

    day_folder  = get_day_folder(state["task_name"], state["start_date"])
    part_file   = get_current_part(day_folder)
    file_exists = part_file.exists()

    # Build header from task fields + meta columns
    field_keys  = []
    for f in task.get("fields", []):
        if isinstance(f, dict):
            field_keys.extend(f.keys())
        else:
            field_keys.append(str(f))
    if "confidence" not in field_keys:  field_keys.append("confidence")
    if "source_url"  not in field_keys: field_keys.append("source_url")
    field_keys += ["_cycle", "_timestamp"]

    written = 0
    with open(part_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=field_keys, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        for row in rows:
            row["_cycle"]     = state["cycle"]
            row["_timestamp"] = datetime.now().isoformat(timespec="seconds")
            writer.writerow(row)
            written += 1

    return written

def write_day_summary(task: dict, state: dict, cycle_stats: list):
    """Write / update the daily summary.md."""
    day_folder = get_day_folder(state["task_name"], state["start_date"])
    summary_path = day_folder / "summary.md"
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"# {task.get('name', 'Task')} — {today}",
        f"",
        f"**Goal:** {task.get('goal', '')}",
        f"**Model:** {state.get('model', DEFAULT_MODEL)}",
        f"**Total rows (all time):** {state['total_rows']}",
        f"",
        f"## Cycle log",
    ]
    for s in cycle_stats:
        lines.append(f"- Cycle {s['cycle']:03d} | {s['time']} | found {s['found']} | written {s['written']}")

    summary_path.write_text("\n".join(lines), encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# Search
# ══════════════════════════════════════════════════════════════════════════════

def run_search(queries: list[str], state: dict) -> list[dict]:
    """Run DuckDuckGo searches, rotating through query list."""
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            log.error("Search package not installed. Run: pip install ddgs")
            return []

    results = []
    qi = state.get("query_index", 0)

    # Run up to 3 queries per cycle, rotating through the list
    for i in range(min(3, len(queries))):
        q = queries[(qi + i) % len(queries)]
        log.info("Searching: %s", q)
        try:
            with DDGS() as ddgs:
                hits = list(ddgs.text(q, max_results=SEARCH_RESULTS))
                results.extend(hits)
                time.sleep(1.5)   # polite delay
        except Exception as e:
            log.warning("Search error for '%s': %s", q, e)

    # Advance query rotation
    state["query_index"] = (qi + 3) % len(queries)
    return results


# ══════════════════════════════════════════════════════════════════════════════
# LLM extraction
# ══════════════════════════════════════════════════════════════════════════════

def extract_with_llm(search_results: list, task: dict, state: dict) -> list[dict]:
    """Send search results to local LLM for structured extraction."""
    if not search_results:
        return []

    field_defs = []
    for f in task.get("fields", []):
        if isinstance(f, dict):
            for k, desc in f.items():
                field_defs.append(f"{k}: {desc}")
        else:
            field_defs.append(str(f))

    context_blocks = []
    for r in search_results[:10]:
        title = r.get("title", "")
        href  = r.get("href", r.get("url", ""))
        body  = r.get("body", "")
        context_blocks.append(f"URL: {href}\nTitle: {title}\nSnippet: {body}")
    context = "\n\n---\n\n".join(context_blocks)

    system_prompt = (
        load_policy()[:1500]
        + "\n\n"
        + load_job_description()[:800]
    )

    user_prompt = f"""Task goal: {task.get('goal', '')}

Extract data from the search results below. Return ONLY a valid JSON array.
Each object must have exactly these fields:
{chr(10).join(field_defs)}
confidence: high | medium | low
source_url: the URL this came from

Rules:
- Only include records where you are at least medium confidence.
- Use null for any field you cannot confirm — never guess.
- Do not include duplicate people or companies.
- Return [] if nothing useful was found.
- No preamble, no explanation — ONLY the JSON array.

Search results:
{context}"""

    model_key = state.get("model", DEFAULT_MODEL)
    endpoint  = LLM_ENDPOINTS.get(model_key, LLM_ENDPOINTS[DEFAULT_MODEL])

    try:
        resp = requests.post(
            endpoint,
            json={
                "model": ANTHROPIC_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 2048,
            },
            timeout=90,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()

        # Strip markdown fences if LLM wraps response
        if content.startswith("```"):
            content = re.sub(r"^```[a-z]*\n?", "", content)
            content = re.sub(r"\n?```$", "", content)

        return json.loads(content)

    except json.JSONDecodeError as e:
        log.warning("LLM returned invalid JSON: %s", e)
        return []
    except Exception as e:
        log.error("LLM call failed: %s", e)
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Validation & deduplication
# ══════════════════════════════════════════════════════════════════════════════

def _row_hash(row: dict) -> str:
    """Stable hash for deduplication — based on key identifying fields."""
    key = "|".join([
        str(row.get("email", "")).lower().strip(),
        str(row.get("full_name", "")).lower().strip(),
        str(row.get("linkedin", "")).lower().strip(),
        str(row.get("source_url", "")).lower().strip(),
    ])
    return hashlib.md5(key.encode()).hexdigest()

# ── Hallucination indicators ─────────────────────────────────────────────────
_FAKE_NAMES    = {"john doe", "jane doe", "jane smith", "john smith",
                  "test user", "example user", "first last", "name here"}
_FAKE_PHONES   = {"555-1234", "555-5678", "555-0000", "555-1111",
                  "123-456-7890", "000-000-0000", "(555)"}
_FAKE_EMAIL_PATTERNS = ["john.doe@", "jane.smith@", "john.smith@",
                         "jane.doe@", "test@", "example@", "user@example",
                         "@example.com", "@test.com"]
_FAKE_LINKEDIN = {"https://www.linkedin.com/in/johndoe",
                  "https://www.linkedin.com/in/janesmith",
                  "https://www.linkedin.com/in/johnsmith"}

def _is_hallucinated(row: dict) -> bool:
    """Return True if this record looks like a hallucinated placeholder."""
    name  = str(row.get("full_name", "")).strip().lower()
    phone = str(row.get("phone", "")).strip()
    email = str(row.get("email", "")).strip().lower()
    li    = str(row.get("linkedin", "")).strip()

    if name in _FAKE_NAMES:
        return True
    if any(phone.startswith(fp) or fp in phone for fp in _FAKE_PHONES):
        return True
    if any(pat in email for pat in _FAKE_EMAIL_PATTERNS):
        return True
    if li in _FAKE_LINKEDIN:
        return True
    # Reject records with no real name or company
    if not name or not row.get("company", "").strip():
        return True
    return False


def validate_rows(raw: list, state: dict) -> list[dict]:
    """Deduplicate, filter low-confidence, and reject hallucinated records."""
    seen = set(state.get("seen_hashes", []))
    clean = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        if str(row.get("confidence", "low")).lower() == "low":
            continue
        if _is_hallucinated(row):
            log.debug("Hallucination rejected: %s / %s",
                      row.get("full_name"), row.get("email"))
            continue
        h = _row_hash(row)
        if h in seen:
            log.debug("Duplicate skipped: %s", row.get("full_name") or row.get("company"))
            continue
        seen.add(h)
        clean.append(row)

    state["seen_hashes"] = list(seen)
    return clean


# ══════════════════════════════════════════════════════════════════════════════
# Archive completed task
# ══════════════════════════════════════════════════════════════════════════════

def archive_task(task: dict):
    task_file = CURRENT_DIR / "task.md"
    if not task_file.exists():
        return
    name = task.get("name", "unnamed")
    ts   = datetime.now().strftime("%Y-%m-%d_%H%M")
    dest = ARCHIVE_DIR / f"{name}-{ts}.md"
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    task_file.rename(dest)
    log.info("Task archived → %s", dest)


# ══════════════════════════════════════════════════════════════════════════════
# Main loop
# ══════════════════════════════════════════════════════════════════════════════

def main():
    log.info("=" * 60)
    log.info("Recon Worker — Zyrcon Labs")
    log.info("=" * 60)

    task  = load_task()
    name  = task.get("name", "unnamed")
    log.info("Task loaded: %s", name)
    log.info("Goal: %s", task.get("goal", ""))

    # Check for existing state (resume) or start fresh
    existing = load_state()
    if existing.get("task_name") == name and existing.get("status") == "running":
        log.info("Resuming from cycle %d (%d rows so far)", existing["cycle"], existing["total_rows"])
        state = existing
    else:
        log.info("Starting fresh run.")
        state = init_state(task)

    queries     = task.get("queries", [])
    interval    = int(task.get("interval", 45))
    cycle_stats = []

    if not queries:
        log.error("No queries defined in task file.")
        sys.exit(1)

    while True:
        state["cycle"] += 1
        cycle_num = state["cycle"]
        cycle_time = datetime.now().strftime("%H:%M:%S")
        log.info("─── Cycle %d | %s ───", cycle_num, cycle_time)

        # ── Search ──────────────────────────────────────────────────────
        q_slice = queries[state.get("query_index", 0) % len(queries)]
        log_thought(f"Searching: {q_slice}", "search")
        raw_results = run_search(queries, state)
        log.info("Search returned %d snippets.", len(raw_results))
        log_thought(f"Search returned {len(raw_results)} results", "info")

        # ── Extract ─────────────────────────────────────────────────────
        log_thought(f"Sending to {state.get('model', DEFAULT_MODEL)} for extraction…", "think")
        extracted = extract_with_llm(raw_results, task, state)
        log.info("LLM extracted %d candidate records.", len(extracted))
        log_thought(f"Extracted {len(extracted)} candidate records", "think")

        # ── Validate ─────────────────────────────────────────────────────
        validated = validate_rows(extracted, state)
        log.info("After validation: %d new records.", len(validated))

        # ── Write ────────────────────────────────────────────────────────
        written = write_rows(validated, task, state)
        state["total_rows"] += written
        state["last_cycle"]  = datetime.now().isoformat()
        log.info("Written %d rows | Total: %d", written, state["total_rows"])
        if written:
            log_thought(f"Wrote {written} new records → total {state['total_rows']} rows", "write")

        cycle_stats.append({
            "cycle":   cycle_num,
            "time":    cycle_time,
            "found":   len(extracted),
            "written": written,
        })

        # ── Summary ──────────────────────────────────────────────────────
        write_day_summary(task, state, cycle_stats)
        save_state(state)

        log_thought(f"Cycle {cycle_num} done · {state['total_rows']} total rows · sleeping {interval}s", "cycle")

        # ── Stop check ───────────────────────────────────────────────────
        stop, reason = should_stop(task, state)
        if stop:
            log_thought(f"Stopping — {reason}", "stop")
            log.info("STOP — %s", reason)
            state["status"] = "stopped"
            save_state(state)
            write_task_status("stop")
            archive_task(task)
            log.info("Final: %d rows across %d cycles.", state["total_rows"], state["cycle"])
            break

        # ── Sleep ────────────────────────────────────────────────────────
        log.info("Sleeping %ds before next cycle...", interval)
        time.sleep(interval)

    log.info("Recon Worker stopped cleanly.")


if __name__ == "__main__":
    main()
