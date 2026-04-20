"""
QUOTE — Proposal & RFQ Generator
Parses scope documents, generates branded proposals, includes pricing engine.
Standard operator contract: /api/health, /api/chat, /api/status, /api/task
"""
import json, uuid
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

BASE_DIR  = Path(__file__).parent
DATA_DIR  = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
PROPOSALS = DATA_DIR / "proposals.json"

def load_proposals():
    return json.loads(PROPOSALS.read_text()) if PROPOSALS.exists() else []

def save_proposal(p):
    proposals = load_proposals()
    proposals.append(p)
    PROPOSALS.write_text(json.dumps(proposals, indent=2))

def generate_proposal(client, scope, budget=None):
    pid = str(uuid.uuid4())[:8].upper()
    proposal = {
        "id":         pid,
        "client":     client,
        "scope":      scope,
        "budget":     budget,
        "status":     "draft",
        "created_at": datetime.now().isoformat(),
        "sections": {
            "executive_summary": f"We propose a comprehensive solution for {client} addressing: {scope}",
            "approach":          "Our team will deliver this engagement in structured phases with clear milestones and deliverables.",
            "timeline":          "Phase 1: Discovery (1 week) → Phase 2: Delivery (2-4 weeks) → Phase 3: Review (1 week)",
            "investment":        budget or "To be discussed based on final scope",
            "next_steps":        "Schedule a 30-minute call to review this proposal and align on timeline."
        }
    }
    save_proposal(proposal)
    return proposal

@app.route("/api/health")
def health():
    return jsonify({"status": "online", "service": "quote", "version": "1.0.0", "port": 8007})

@app.route("/api/status")
def api_status():
    proposals = load_proposals()
    return jsonify({
        "status":               "ready",
        "proposals_generated":  len(proposals),
        "recent":               proposals[-3:] if proposals else [],
        "last_run":             proposals[-1]["created_at"] if proposals else None
    })

@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    msg  = (data.get("message") or "").strip()
    low  = msg.lower()

    if any(w in low for w in ["status", "how many", "count"]):
        proposals = load_proposals()
        reply = f"QUOTE has generated {len(proposals)} proposals. Ready to create new ones."

    elif any(w in low for w in ["list", "recent", "show"]):
        proposals = load_proposals()[-5:]
        if proposals:
            lines = [f"• {p['id']} — {p['client']} ({p['status']})" for p in proposals]
            reply = "Recent proposals:\n" + "\n".join(lines)
        else:
            reply = "No proposals yet. Send: generate proposal for [Client] — [scope]"

    elif any(w in low for w in ["generate", "create", "proposal"]):
        parts = msg.split("—") if "—" in msg else msg.split("-", 1)
        if len(parts) >= 2:
            client = parts[0].replace("generate proposal for","").replace("create proposal for","").strip()
            scope  = parts[1].strip()
            p = generate_proposal(client, scope)
            reply = (f"✓ Proposal {p['id']} created for {client}.\n"
                     f"Scope: {scope}\n"
                     f"Retrieve via GET /api/proposal/{p['id']}")
        else:
            reply = "Format: generate proposal for [Client Name] — [scope description]"

    else:
        reply = ("QUOTE here — proposal and RFQ generator.\n"
                 "Commands: status, list, generate proposal for [Client] — [scope]")

    return jsonify({"reply": reply, "operator": "quote", "status": "online"})

@app.route("/api/task", methods=["POST"])
def api_task():
    data     = request.get_json(silent=True) or {}
    task_id  = data.get("task_id", str(uuid.uuid4()))
    context  = data.get("context", {})
    callback = data.get("callback_url")
    client   = context.get("client", "Unknown Client")
    scope    = context.get("scope", data.get("instruction", ""))
    p = generate_proposal(client, scope)
    result = {"task_id": task_id, "status": "complete", "operator": "quote", "result": p}
    if callback:
        try:
            import urllib.request as ur
            ur.urlopen(ur.Request(callback, json.dumps(result).encode(), {"Content-Type": "application/json"}), timeout=3)
        except Exception:
            pass
    return jsonify(result)

@app.route("/api/proposal/<pid>")
def get_proposal(pid):
    p = next((x for x in load_proposals() if x["id"] == pid), None)
    return jsonify(p) if p else (jsonify({"error": "not found"}), 404)

@app.route("/")
def index():
    return "<h2>QUOTE — Proposal Generator</h2><p>POST /api/chat | GET /api/status | POST /api/task</p>"

if __name__ == "__main__":
    print("QUOTE → http://localhost:8007")
    app.run(host="0.0.0.0", port=8007, debug=False, threaded=True)
