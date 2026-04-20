"""
DEBRIEF — Post-Call Intelligence Logger
Extracts action items, commitments, appointments, follow-up drafts from call notes.
Standard operator contract: /api/health, /api/chat, /api/status, /api/task
"""
import json, uuid, re
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DEBRIEFS = DATA_DIR / "debriefs.json"

def load_debriefs():
    return json.loads(DEBRIEFS.read_text()) if DEBRIEFS.exists() else []

def extract_items(notes):
    actions   = []
    followups = []
    dates     = []
    for line in notes.splitlines():
        l = line.strip()
        if not l:
            continue
        if re.search(r'\b(will|need to|must|should|going to|action:)\b', l, re.I):
            actions.append(l)
        if re.search(r'\b(follow.?up|send|email|call back|schedule|book)\b', l, re.I):
            followups.append(l)
        if re.search(r'\b(monday|tuesday|wednesday|thursday|friday|next week|tomorrow|\d{1,2}/\d{1,2})\b', l, re.I):
            dates.append(l)
    return actions[:10], followups[:5], dates[:5]

def process_notes(title, notes):
    actions, followups, dates = extract_items(notes)
    debrief = {
        "id":              str(uuid.uuid4())[:8].upper(),
        "title":           title,
        "processed":       datetime.now().isoformat(),
        "raw_notes":       notes[:500],
        "action_items":    actions or ["No explicit action items detected"],
        "follow_ups":      followups or ["No follow-up items detected"],
        "dates_mentioned": dates or [],
        "word_count":      len(notes.split())
    }
    debriefs = load_debriefs()
    debriefs.append(debrief)
    DEBRIEFS.write_text(json.dumps(debriefs, indent=2))
    return debrief

@app.route("/api/health")
def health():
    return jsonify({"status": "online", "service": "debrief", "version": "1.0.0", "port": 8008})

@app.route("/api/status")
def api_status():
    debriefs = load_debriefs()
    return jsonify({
        "status":             "ready",
        "debriefs_processed": len(debriefs),
        "last_processed":     debriefs[-1]["processed"] if debriefs else None
    })

@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    msg  = (data.get("message") or "").strip()
    low  = msg.lower()

    if any(w in low for w in ["status", "how many", "count"]):
        debriefs = load_debriefs()
        reply = f"DEBRIEF has processed {len(debriefs)} call notes. Ready for more."

    elif any(w in low for w in ["list", "recent", "last"]):
        debriefs = load_debriefs()[-5:]
        if debriefs:
            lines = [f"• {d['id']} — {d['title']} ({d['processed'][:10]})" for d in debriefs]
            reply = "Recent debriefs:\n" + "\n".join(lines)
        else:
            reply = "No debriefs yet. Format: debrief: [Title] | [notes]"

    elif "|" in msg or low.startswith("debrief:") or low.startswith("process:"):
        parts = msg.split("|", 1)
        if len(parts) == 2:
            title = parts[0].replace("debrief:", "").replace("process:", "").strip()
            notes = parts[1].strip()
            d = process_notes(title, notes)
            reply = (f"✓ Debrief {d['id']} — {d['title']}\n"
                     f"Action items ({len(d['action_items'])}): {d['action_items'][0]}\n"
                     f"Follow-ups: {len(d['follow_ups'])}")
        else:
            reply = "Format: debrief: [Call Title] | [paste notes here]"

    else:
        reply = ("DEBRIEF here — post-call intelligence extractor.\n"
                 "Commands: status, list, debrief: [title] | [notes]")

    return jsonify({"reply": reply, "operator": "debrief", "status": "online"})

@app.route("/api/task", methods=["POST"])
def api_task():
    data     = request.get_json(silent=True) or {}
    task_id  = data.get("task_id", str(uuid.uuid4()))
    context  = data.get("context", {})
    callback = data.get("callback_url")
    title    = context.get("title", "Call Notes")
    notes    = context.get("notes", data.get("instruction", ""))
    d = process_notes(title, notes)
    result = {"task_id": task_id, "status": "complete", "operator": "debrief", "result": d}
    if callback:
        try:
            import urllib.request as ur
            ur.urlopen(ur.Request(callback, json.dumps(result).encode(), {"Content-Type": "application/json"}), timeout=3)
        except Exception:
            pass
    return jsonify(result)

@app.route("/")
def index():
    return "<h2>DEBRIEF — Call Intelligence</h2><p>POST /api/chat | GET /api/status | POST /api/task</p>"

if __name__ == "__main__":
    print("DEBRIEF → http://localhost:8008")
    app.run(host="0.0.0.0", port=8008, debug=False, threaded=True)
