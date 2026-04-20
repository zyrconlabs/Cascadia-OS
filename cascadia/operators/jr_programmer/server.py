"""
JR. PROGRAMMER — Software Development Assistant
Writes code, breaks down architecture, tests iteratively, improves existing code.
Standard operator contract: /api/health, /api/chat, /api/status, /api/task
"""
import json, uuid
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

BASE_DIR   = Path(__file__).parent
DATA_DIR   = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
TASKS_FILE = DATA_DIR / "tasks.json"

def load_tasks():
    return json.loads(TASKS_FILE.read_text()) if TASKS_FILE.exists() else []

def save_task(t):
    tasks = load_tasks()
    tasks.append(t)
    TASKS_FILE.write_text(json.dumps(tasks, indent=2))

def handle_request(text):
    low  = text.lower()
    task = {
        "id":      str(uuid.uuid4())[:8].upper(),
        "request": text,
        "created": datetime.now().isoformat(),
        "status":  "queued"
    }
    if any(w in low for w in ["write", "create", "build", "function", "code", "script"]):
        task["type"]  = "code_generation"
        task["reply"] = (f"Code generation queued: '{text[:80]}'\n"
                         "Approach: understand requirements → break into components → write → test → integrate.")
    elif any(w in low for w in ["review", "fix", "debug", "improve", "refactor"]):
        task["type"]  = "code_review"
        task["reply"] = (f"Code review queued: '{text[:80]}'\n"
                         "Paste the code with a description of the issue and I'll analyze structure and suggest fixes.")
    elif any(w in low for w in ["architecture", "design", "plan", "structure"]):
        task["type"]  = "architecture"
        task["reply"] = (f"Architecture task queued: '{text[:80]}'\n"
                         "I'll break this into components, define interfaces, and outline the build sequence.")
    else:
        task["type"]  = "general"
        task["reply"] = (f"Task logged: '{text[:80]}'\n"
                         "Commands: write [request], review [code], plan [architecture]")
    save_task(task)
    return task

@app.route("/api/health")
def health():
    return jsonify({"status": "online", "service": "jr-programmer", "version": "1.0.0", "port": 8004})

@app.route("/api/status")
def api_status():
    tasks = load_tasks()
    return jsonify({
        "status":        "ready",
        "tasks_handled": len(tasks),
        "last_task":     tasks[-1]["created"] if tasks else None,
        "task_types":    list({t.get("type", "general") for t in tasks})
    })

@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    msg  = (data.get("message") or "").strip()
    low  = msg.lower()

    if any(w in low for w in ["status", "how many", "count"]):
        tasks = load_tasks()
        reply = f"Jr. Programmer has handled {len(tasks)} tasks. Ready for more."

    elif any(w in low for w in ["list", "recent", "history"]):
        tasks = load_tasks()[-5:]
        if tasks:
            lines = [f"• {t['id']} — {t.get('type','general')}: {t['request'][:50]}" for t in tasks]
            reply = "Recent tasks:\n" + "\n".join(lines)
        else:
            reply = "No tasks yet. Ask me to write code, review code, or plan an architecture."

    elif msg:
        t     = handle_request(msg)
        reply = t["reply"]

    else:
        reply = ("Jr. Programmer here — software development assistant.\n"
                 "Commands: write [request], review [code], plan [architecture], status, list")

    return jsonify({"reply": reply, "operator": "jr-programmer", "status": "online"})

@app.route("/api/task", methods=["POST"])
def api_task():
    data     = request.get_json(silent=True) or {}
    task_id  = data.get("task_id", str(uuid.uuid4()))
    instr    = data.get("instruction", "")
    callback = data.get("callback_url")
    t = handle_request(instr)
    result = {"task_id": task_id, "status": "complete", "operator": "jr-programmer", "result": t}
    if callback:
        try:
            import urllib.request as ur
            ur.urlopen(ur.Request(callback, json.dumps(result).encode(), {"Content-Type": "application/json"}), timeout=3)
        except Exception:
            pass
    return jsonify(result)

@app.route("/")
def index():
    return "<h2>Jr. Programmer</h2><p>POST /api/chat | GET /api/status | POST /api/task</p>"

if __name__ == "__main__":
    print("Jr. Programmer → http://localhost:8004")
    app.run(host="0.0.0.0", port=8004, debug=False, threaded=True)
