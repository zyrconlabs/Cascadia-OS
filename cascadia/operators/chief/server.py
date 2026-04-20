"""
CHIEF — Executive Intelligence Briefing Agent
Pulls live data from all active operators, delivers briefs with anomalies and ranked actions.
Standard operator contract: /api/health, /api/chat, /api/status, /api/task
"""
import json, uuid, urllib.request
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
BRIEFS   = DATA_DIR / "briefs.json"

OPERATORS = [
    {"id": "recon",  "port": 8002},
    {"id": "scout",  "port": 7002},
    {"id": "quote",  "port": 8007},
]

def probe(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None

def load_briefs():
    return json.loads(BRIEFS.read_text()) if BRIEFS.exists() else []

def generate_brief():
    now       = datetime.now()
    sections  = []
    anomalies = []
    actions   = []

    for op in OPERATORS:
        status = probe(op["port"])
        if status:
            sections.append(f"• {op['id'].upper()}: online")
        else:
            sections.append(f"• {op['id'].upper()}: OFFLINE")
            anomalies.append(f"{op['id'].upper()} not responding")
            actions.append(f"Restart {op['id'].upper()}")

    brief = {
        "id":               str(uuid.uuid4())[:8].upper(),
        "generated":        now.isoformat(),
        "date":             now.strftime("%A %B %d, %Y"),
        "operator_summary": sections,
        "anomalies":        anomalies or ["No anomalies detected"],
        "actions":          actions or ["Review RECON leads", "Check SCOUT inbound queue"],
    }
    briefs = load_briefs()
    briefs.append(brief)
    BRIEFS.write_text(json.dumps(briefs[-30:], indent=2))
    return brief

@app.route("/api/health")
def health():
    return jsonify({"status": "online", "service": "chief", "version": "1.0.0", "port": 8006})

@app.route("/api/status")
def api_status():
    briefs = load_briefs()
    return jsonify({
        "status":            "ready",
        "briefs_generated":  len(briefs),
        "last_brief":        briefs[-1]["generated"] if briefs else None
    })

@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    msg  = (data.get("message") or "").strip().lower()

    if any(w in msg for w in ["brief", "status", "morning", "report", "summary"]):
        b = generate_brief()
        lines = [f"CHIEF Brief — {b['date']}",
                 "\nOperator Status:"] + b["operator_summary"] + [
                 "\nAnomalies: " + "; ".join(b["anomalies"]),
                 "Actions:   " + "; ".join(b["actions"])]
        reply = "\n".join(lines)

    elif any(w in msg for w in ["history", "last", "previous"]):
        briefs = load_briefs()[-3:]
        reply  = "\n".join([f"• {b['id']} — {b['date']}" for b in briefs]) if briefs else "No briefs yet."

    else:
        reply = "CHIEF here — executive intelligence briefing.\nCommands: brief, history"

    return jsonify({"reply": reply, "operator": "chief", "status": "online"})

@app.route("/api/task", methods=["POST"])
def api_task():
    data     = request.get_json(silent=True) or {}
    task_id  = data.get("task_id", str(uuid.uuid4()))
    callback = data.get("callback_url")
    brief    = generate_brief()
    result   = {"task_id": task_id, "status": "complete", "operator": "chief", "result": brief}
    if callback:
        try:
            import urllib.request as ur
            ur.urlopen(ur.Request(callback, json.dumps(result).encode(), {"Content-Type": "application/json"}), timeout=3)
        except Exception:
            pass
    return jsonify(result)

@app.route("/api/brief")
def latest_brief():
    return jsonify(generate_brief())

@app.route("/")
def index():
    return "<h2>CHIEF — Executive Intelligence</h2><p>GET /api/brief | POST /api/chat | GET /api/status</p>"

if __name__ == "__main__":
    print("CHIEF → http://localhost:8006")
    app.run(host="0.0.0.0", port=8006, debug=False, threaded=True)
