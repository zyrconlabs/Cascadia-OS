#!/usr/bin/env python3
"""
Recon Worker Dashboard — Zyrcon Labs
Serves the live control dashboard on localhost:7700
"""

import csv
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Generator

from flask import Flask, Response, jsonify, request, send_from_directory

# ─── Paths ───────────────────────────────────────────────────────────────────
BASE_DIR      = Path.home() / ".zyrcon" / "recon-worker"
OUTPUT_DIR    = BASE_DIR / "output"
LOGS_DIR      = BASE_DIR / "logs"
STATE_FILE    = BASE_DIR / "state.json"
THOUGHTS_FILE = BASE_DIR / "thoughts.json"
CURRENT_DIR   = BASE_DIR / "tasks" / "current"
STATIC_DIR    = Path(__file__).parent / "static"

LLM_MODELS = ["zyrcon-3b", "zyrcon-7b", "zyrcon-fast"]

app = Flask(__name__, static_folder=str(STATIC_DIR))

# ─── CORS (allows Prism Dashboard at any localhost origin to poll) ────────────
@app.after_request
def add_cors(response):
    origin = request.headers.get("Origin", "")
    if "localhost" in origin or "127.0.0.1" in origin or not origin:
        response.headers["Access-Control-Allow-Origin"]  = origin or "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

@app.route("/api/thoughts")
def api_thoughts():
    """Return the thought ring buffer written by recon_worker.py."""
    if THOUGHTS_FILE.exists():
        try:
            return jsonify(json.loads(THOUGHTS_FILE.read_text()))
        except Exception:
            pass
    return jsonify([])


# ─── Helpers ─────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"status": "idle", "cycle": 0, "total_rows": 0}

def save_state(patch: dict):
    state = load_state()
    state.update(patch)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))

def load_task_meta() -> dict:
    task_file = CURRENT_DIR / "task.md"
    if not task_file.exists():
        return {}
    text = task_file.read_text(encoding="utf-8")
    meta = {}
    for line in text.splitlines():
        if line.strip() == "---":
            continue
        if ":" in line and not line.startswith(" ") and not line.startswith("-"):
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip("\"'")
    return meta

def write_task_stop():
    task_file = CURRENT_DIR / "task.md"
    if not task_file.exists():
        return
    content = task_file.read_text(encoding="utf-8")
    content = re.sub(r"^status:\s*\S+", "status: stop", content, flags=re.MULTILINE)
    task_file.write_text(content, encoding="utf-8")

def get_output_tree() -> list:
    """Return list of {task, day, parts:[{file, rows, size}]}."""
    tree = []
    if not OUTPUT_DIR.exists():
        return tree
    for task_dir in sorted(OUTPUT_DIR.iterdir()):
        if not task_dir.is_dir():
            continue
        days = []
        for day_dir in sorted(task_dir.iterdir()):
            if not day_dir.is_dir():
                continue
            parts = []
            for part in sorted(day_dir.glob("part-*.csv")):
                try:
                    with open(part, newline="", encoding="utf-8") as f:
                        rows = sum(1 for _ in csv.reader(f)) - 1
                except Exception:
                    rows = 0
                parts.append({
                    "file": part.name,
                    "rows": max(rows, 0),
                    "size_kb": round(part.stat().st_size / 1024, 1),
                    "path": str(part),
                })
            summary = (day_dir / "summary.md").exists()
            days.append({"day": day_dir.name, "parts": parts, "summary": summary})
        tree.append({"task": task_dir.name, "days": days})
    return tree

def get_recent_rows(n: int = 20) -> list:
    """Return the last N rows from the most recent part file."""
    state = load_state()
    task_name = state.get("task_name")
    if not task_name:
        return []
    task_dir = OUTPUT_DIR / task_name
    if not task_dir.exists():
        return []
    all_parts = sorted(task_dir.rglob("part-*.csv"))
    if not all_parts:
        return []
    latest = all_parts[-1]
    try:
        with open(latest, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        return rows[-n:]
    except Exception:
        return []

def tail_log(n: int = 60) -> list[str]:
    log_file = LOGS_DIR / "worker.log"
    if not log_file.exists():
        return []
    try:
        lines = log_file.read_text(encoding="utf-8").splitlines()
        return lines[-n:]
    except Exception:
        return []

def stop_condition_progress(state: dict, task: dict) -> dict:
    """Return progress info for the stop condition."""
    stop = {}
    for line in CURRENT_DIR.joinpath("task.md").read_text().splitlines() if (CURRENT_DIR / "task.md").exists() else []:
        if ":" in line and not line.startswith(" ") and not line.startswith("-"):
            k, _, v = line.partition(":")
            stop[k.strip()] = v.strip()

    mode = stop.get("mode", "status")
    if mode == "quantity":
        target = int(stop.get("quantity", 0))
        current = state.get("total_rows", 0)
        return {"mode": "quantity", "current": current, "target": target, "pct": round(current / target * 100, 1) if target else 0}
    elif mode == "time":
        raw = stop.get("time", "24h")
        start = state.get("start_time")
        if start:
            from datetime import timedelta
            def parse_dur(s):
                s = str(s).lower()
                if s.endswith("m"): return timedelta(minutes=int(s[:-1]))
                if s.endswith("h"): return timedelta(hours=int(s[:-1]))
                if s.endswith("d"): return timedelta(days=int(s[:-1]))
                return timedelta(hours=24)
            dur = parse_dur(raw)
            elapsed = datetime.now() - datetime.fromisoformat(start)
            pct = round(elapsed.total_seconds() / dur.total_seconds() * 100, 1)
            remaining = dur - elapsed
            return {"mode": "time", "limit": raw, "elapsed": str(elapsed).split(".")[0], "remaining": str(remaining).split(".")[0], "pct": min(pct, 100)}
    return {"mode": "status", "pct": None}


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(str(Path(__file__).parent), "dashboard.html")

@app.route("/api/status")
def api_status():
    state = load_state()
    task  = load_task_meta()
    progress = {}
    try:
        progress = stop_condition_progress(state, task)
    except Exception:
        pass
    return jsonify({
        "state":    state,
        "task":     task,
        "progress": progress,
        "tree":     get_output_tree(),
    })

@app.route("/api/rows")
def api_rows():
    n = int(request.args.get("n", 20))
    return jsonify(get_recent_rows(n))

@app.route("/api/log")
def api_log():
    return jsonify(tail_log(80))

@app.route("/api/stop", methods=["POST"])
def api_stop():
    write_task_stop()
    save_state({"status": "stop"})
    return jsonify({"ok": True, "message": "Stop signal sent."})

@app.route("/api/model", methods=["POST"])
def api_set_model():
    data  = request.get_json(force=True)
    model = data.get("model", "zyrcon-3b")
    if model not in LLM_MODELS:
        return jsonify({"ok": False, "message": "Unknown model."}), 400
    save_state({"model": model})
    return jsonify({"ok": True, "model": model})

@app.route("/api/stream")
def api_stream():
    """SSE endpoint — pushes status update every 3 seconds."""
    def generate() -> Generator:
        while True:
            state = load_state()
            task  = load_task_meta()
            progress = {}
            try:
                progress = stop_condition_progress(state, task)
            except Exception:
                pass
            payload = json.dumps({
                "state":    state,
                "task":     task,
                "progress": progress,
                "log_tail": tail_log(5),
            })
            yield f"data: {payload}\n\n"
            time.sleep(3)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})



@app.route("/api/health")
def health():
    return jsonify({"status": "online", "service": "recon", "version": "1.0.0", "port": 8002})




@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Standard operator chat contract — lets Beacon and PRISM talk to RECON."""
    data = request.get_json(silent=True) or {}
    msg  = (data.get("message") or "").strip().lower()

    if any(w in msg for w in ["status", "how are you", "what are you doing"]):
        state = load_state()
        reply = f"RECON is {state.get('status', 'idle')}. Cycle {state.get('cycle', 0)}, {state.get('total_rows', 0)} rows collected."

    elif any(w in msg for w in ["stop", "pause", "halt"]):
        write_task_stop()
        reply = "RECON stop signal sent."

    elif any(w in msg for w in ["results", "leads", "found", "rows"]):
        rows = get_recent_rows(5)
        reply = f"Last {len(rows)} leads: " + "; ".join(str(r) for r in rows) if rows else "No results yet."

    elif any(w in msg for w in ["log", "latest", "last"]):
        lines = tail_log(10)
        reply = "Last log entries:\n" + "\n".join(lines) if lines else "No log entries yet."

    else:
        state = load_state()
        reply = (
            f"RECON here — {state.get('status', 'idle')}, {state.get('total_rows', 0)} rows collected. "
            "Ask me: status, results, log, or stop."
        )

    return jsonify({"reply": reply, "operator": "recon", "status": "online"})


if __name__ == "__main__":
    print("Recon Worker Dashboard → http://localhost:8002")
    app.run(host="0.0.0.0", port=8002, debug=False, threaded=True)

