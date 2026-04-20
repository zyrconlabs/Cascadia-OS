import json
import os
import uuid
import requests
from datetime import datetime
from flask import Flask, request, jsonify, send_file, Response, stream_with_context
from flask_cors import CORS
from scout_worker import ScoutWorker

app = Flask(__name__)
CORS(app)

import pathlib as _pl
CONFIG_FILE = str(_pl.Path(__file__).parent / "scout.config.json")
LEADS_FILE  = os.environ.get("SCOUT_VAULT", "../../../Vault/operators/scout") + "/leads.json"
sessions: dict = {}


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)

def load_leads():
    if not os.path.exists(LEADS_FILE):
        return []
    with open(LEADS_FILE) as f:
        return json.load(f)

def save_lead(lead):
    leads = load_leads()
    leads.insert(0, lead)
    os.makedirs("data", exist_ok=True)
    with open(LEADS_FILE, "w") as f:
        json.dump(leads, f, indent=2)

def sse(data):
    return f"data: {json.dumps(data)}\n\n"


@app.route("/api/stream", methods=["POST"])
def stream_chat():
    data       = request.get_json(force=True)
    session_id = data.get("session_id") or str(uuid.uuid4())
    message    = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Empty message"}), 400

    if session_id not in sessions:
        sessions[session_id] = {"history": [], "lead_extracted": False,
                                 "started_at": datetime.now().isoformat()}
    session = sessions[session_id]
    config  = load_config()
    worker  = ScoutWorker(config)

    def generate():
        full_reply = ""
        yield sse({"type": "session", "session_id": session_id})

        try:
            system_prompt = worker._build_system_prompt()
            trimmed = session["history"][-16:]
            messages = [{"role": "system", "content": system_prompt}]
            messages += trimmed
            messages.append({"role": "user", "content": message})

            bridge    = config.get("bridge_url", "http://localhost:11434")
            stream_ok = False

            try:
                resp = requests.post(
                    f"{bridge}/v1/chat/completions",
                    json={"model": config.get("model","qwen2.5:3b"),
                          "messages": messages, "stream": True,
                          "temperature": 0.7, "max_tokens": 400},
                    stream=True, timeout=30
                )
                if resp.status_code == 200:
                    stream_ok = True
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        line = line.decode("utf-8")
                        if line.startswith("data: "):
                            raw = line[6:]
                            if raw.strip() == "[DONE]":
                                break
                            try:
                                chunk = json.loads(raw)
                                token = chunk["choices"][0]["delta"].get("content","")
                                if token:
                                    full_reply += token
                                    yield sse({"type": "token", "token": token})
                            except Exception:
                                continue
            except Exception:
                stream_ok = False

            # Groq streaming fallback
            if not stream_ok:
                groq_key = config.get("groq_api_key","")
                if groq_key:
                    try:
                        yield sse({"type": "thinking", "msg": "Switching to fast mode..."})
                        r = requests.post(
                            "https://api.groq.com/openai/v1/chat/completions",
                            headers={"Authorization": f"Bearer {groq_key}",
                                     "Content-Type": "application/json"},
                            json={"model": config.get("groq_model","llama-3.1-8b-instant"),
                                  "messages": messages, "stream": True,
                                  "max_tokens": 400, "temperature": 0.7},
                            stream=True, timeout=15
                        )
                        stream_ok = True
                        for line in r.iter_lines():
                            if not line: continue
                            line = line.decode("utf-8")
                            if line.startswith("data: "):
                                raw = line[6:]
                                if raw.strip() == "[DONE]": break
                                try:
                                    token = json.loads(raw)["choices"][0]["delta"].get("content","")
                                    if token:
                                        full_reply += token
                                        yield sse({"type": "token", "token": token})
                                except Exception:
                                    continue
                    except Exception:
                        stream_ok = False

            if not stream_ok or not full_reply:
                try:
                    full_reply = worker.chat(message, session["history"])
                except Exception:
                    full_reply = "I'm having a brief technical issue — please try again in a moment."
                yield sse({"type": "token", "token": full_reply})

        except Exception:
            full_reply = "I'm having a brief technical issue — please try again in a moment."
            yield sse({"type": "token", "token": full_reply})

        session["history"].append({"role": "user", "content": message})
        session["history"].append({"role": "assistant", "content": full_reply})

        lead_data = None
        msg_count = len(session["history"])
        if msg_count >= 8 and not session["lead_extracted"]:
            try:
                lead_data = worker.extract_lead(session["history"])
                if lead_data:
                    lead_data.update({"session_id": session_id,
                                      "timestamp": datetime.now().isoformat(),
                                      "conversation": session["history"]})
                    save_lead(lead_data)
                    session["lead_extracted"] = True
            except Exception:
                pass

        yield sse({"type": "done", "session_id": session_id,
                   "lead_captured": lead_data is not None,
                   "lead_data": lead_data, "msg_count": msg_count})

    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no",
                             "Access-Control-Allow-Origin": "*"})


@app.route("/api/chat", methods=["POST"])
def chat():
    data       = request.get_json(force=True)
    session_id = data.get("session_id") or str(uuid.uuid4())
    message    = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Empty message"}), 400
    if session_id not in sessions:
        sessions[session_id] = {"history": [], "lead_extracted": False,
                                 "started_at": datetime.now().isoformat()}
    session = sessions[session_id]
    config  = load_config()
    try:
        worker = ScoutWorker(config)
        reply  = worker.chat(message, session["history"])
    except Exception as e:
        return jsonify({"error": str(e),
                        "reply": "I'm having a brief technical issue — please try again in a moment."}), 200
    session["history"].append({"role": "user", "content": message})
    session["history"].append({"role": "assistant", "content": reply})
    lead_data = None
    msg_count = len(session["history"])
    if msg_count >= 8 and not session["lead_extracted"]:
        try:
            worker2  = ScoutWorker(config)
            lead_data = worker2.extract_lead(session["history"])
            if lead_data:
                lead_data.update({"session_id": session_id,
                                   "timestamp": datetime.now().isoformat(),
                                   "conversation": session["history"]})
                save_lead(lead_data)
                session["lead_extracted"] = True
        except Exception:
            pass
    return jsonify({"session_id": session_id, "reply": reply,
                    "lead_captured": lead_data is not None,
                    "lead_data": lead_data, "msg_count": msg_count})


@app.route("/api/leads")
def get_leads():
    return jsonify([{k:v for k,v in l.items() if k!="conversation"} for l in load_leads()])

@app.route("/api/lead/<lead_id>")
def get_lead(lead_id):
    for l in load_leads():
        if l.get("session_id") == lead_id:
            return jsonify(l)
    return jsonify({"error": "Not found"}), 404

@app.route("/api/stats")
def get_stats():
    leads  = load_leads()
    today  = datetime.now().date().isoformat()
    return jsonify({
        "total_conversations": len(sessions),
        "total_leads":         len(leads),
        "hot_leads":           sum(1 for l in leads if l.get("score")=="hot"),
        "warm_leads":          sum(1 for l in leads if l.get("score")=="warm"),
        "leads_today":         sum(1 for l in leads if l.get("timestamp","").startswith(today)),
    })

@app.route("/api/clear", methods=["POST"])
def clear_data():
    global sessions
    sessions = {}
    if os.path.exists(LEADS_FILE):
        os.remove(LEADS_FILE)
    return jsonify({"ok": True})

@app.route("/bell")
@app.route("/doorbell")
@app.route("/")
def bell():
    return send_file("web/bell.html")

@app.route("/prism")
def prism():
    return send_file("web/prism.html")


# Health endpoint — required by Gateway for operator discovery
@app.route("/api/health")
def health():
    config = load_config()
    return jsonify({
        "status":  "online",
        "service": "scout",
        "version": "1.0.0",
        "port":    config.get("server_port", 8000),
        "model":   config.get("model", "unknown")
    })


if __name__ == "__main__":
    config = load_config()
    port   = config.get("server_port", 5100)
    print(f"\n  SCOUT server running")
    print(f"  BELL   →  http://localhost:{port}/bell")
    print(f"  PRISM  →  http://localhost:{port}/prism")
    print(f"  Bridge →  {config.get('bridge_url')}")
    print(f"  Model  →  {config.get('model')}\n")
    app.run(host="0.0.0.0", port=port, debug=False)

