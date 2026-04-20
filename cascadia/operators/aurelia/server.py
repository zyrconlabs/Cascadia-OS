"""
AURELIA — Personal Executive Assistant
Tracks commitments, surfaces priority stack, preps meeting packets, manages delegation ledger.
Standard operator contract: /api/health, /api/chat, /api/status, /api/task
"""
import json, uuid
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

BASE_DIR    = Path(__file__).parent
DATA_DIR    = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
COMMITMENTS = DATA_DIR / "commitments.json"
PRIORITIES  = DATA_DIR / "priorities.json"

def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default

def save(path, data):
    path.write_text(json.dumps(data, indent=2))

def add_commitment(text, due=None, owner=None):
    items = load(COMMITMENTS, [])
    item  = {"id": str(uuid.uuid4())[:8], "text": text, "due": due,
             "owner": owner or "me", "done": False, "created": datetime.now().isoformat()}
    items.append(item)
    save(COMMITMENTS, items)
    return item

def add_priority(text, rank=None):
    items = load(PRIORITIES, [])
    item  = {"id": str(uuid.uuid4())[:8], "text": text,
             "rank": rank or len(items) + 1, "done": False, "created": datetime.now().isoformat()}
    items.append(item)
    items.sort(key=lambda x: x["rank"])
    save(PRIORITIES, items)
    return item

@app.route("/api/health")
def health():
    return jsonify({"status": "online", "service": "aurelia", "version": "1.0.0", "port": 8009})

@app.route("/api/status")
def api_status():
    commitments = load(COMMITMENTS, [])
    priorities  = load(PRIORITIES, [])
    open_c = [c for c in commitments if not c["done"]]
    open_p = [p for p in priorities  if not p["done"]]
    return jsonify({
        "status":           "ready",
        "open_commitments": len(open_c),
        "open_priorities":  len(open_p),
        "top_priority":     open_p[0]["text"] if open_p else None
    })

@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    msg  = (data.get("message") or "").strip()
    low  = msg.lower()

    if any(w in low for w in ["priorities", "stack", "what's next", "top"]):
        items = [p for p in load(PRIORITIES, []) if not p["done"]]
        if items:
            lines = [f"{i+1}. {p['text']}" for i, p in enumerate(items[:5])]
            reply = "Your priority stack:\n" + "\n".join(lines)
        else:
            reply = "Priority stack is clear. Add: priority: [task]"

    elif any(w in low for w in ["commitments", "open items", "follow"]):
        items = [c for c in load(COMMITMENTS, []) if not c["done"]]
        if items:
            lines = [f"• {c['text']}" + (f" (due {c['due']})" if c.get("due") else "") for c in items[:5]]
            reply = "Open commitments:\n" + "\n".join(lines)
        else:
            reply = "No open commitments. Add: commit: [text]"

    elif low.startswith("priority:"):
        text = msg.split(":", 1)[-1].strip()
        add_priority(text)
        reply = f"✓ Added to priority stack: {text}"

    elif low.startswith("commit:"):
        text = msg.split(":", 1)[-1].strip()
        add_commitment(text)
        reply = f"✓ Commitment logged: {text}"

    elif any(w in low for w in ["status", "summary"]):
        open_c = [c for c in load(COMMITMENTS, []) if not c["done"]]
        open_p = [p for p in load(PRIORITIES,  []) if not p["done"]]
        reply  = (f"Aurelia: {len(open_c)} open commitments, {len(open_p)} priorities. "
                  f"Top: {open_p[0]['text'] if open_p else 'none'}")
    else:
        reply = ("Aurelia here — your executive assistant.\n"
                 "Commands: priorities, commitments, priority: [task], commit: [item], status")

    return jsonify({"reply": reply, "operator": "aurelia", "status": "online"})

@app.route("/api/task", methods=["POST"])
def api_task():
    data     = request.get_json(silent=True) or {}
    task_id  = data.get("task_id", str(uuid.uuid4()))
    instr    = data.get("instruction", "")
    callback = data.get("callback_url")
    c = add_commitment(instr)
    result = {"task_id": task_id, "status": "complete", "operator": "aurelia", "result": c}
    if callback:
        try:
            import urllib.request as ur
            ur.urlopen(ur.Request(callback, json.dumps(result).encode(), {"Content-Type": "application/json"}), timeout=3)
        except Exception:
            pass
    return jsonify(result)

@app.route("/")
def index():
    return "<h2>Aurelia — Executive Assistant</h2><p>POST /api/chat | GET /api/status | POST /api/task</p>"

if __name__ == "__main__":
    print("Aurelia → http://localhost:8009")
    app.run(host="0.0.0.0", port=8009, debug=False, threaded=True)
