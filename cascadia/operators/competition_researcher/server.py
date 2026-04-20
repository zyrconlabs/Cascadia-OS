"""
COMPETITION RESEARCHER — Competitive Intelligence Agent
Researches competitors, finds strengths/weaknesses, produces intelligence reports.
Standard operator contract: /api/health, /api/chat, /api/status, /api/task
"""
import json, uuid
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
REPORTS  = DATA_DIR / "reports.json"

def load_reports():
    return json.loads(REPORTS.read_text()) if REPORTS.exists() else []

def research_competitor(name, focus=None):
    report = {
        "id":         str(uuid.uuid4())[:8].upper(),
        "competitor": name,
        "focus":      focus or "general",
        "researched": datetime.now().isoformat(),
        "profile": {
            "name":          name,
            "summary":       f"Competitive profile for {name}. Enrich with RECON live data.",
            "strengths":     ["Established market presence", "Existing customer base", "Brand recognition"],
            "weaknesses":    ["Needs live research — trigger RECON scan for current intel"],
            "opportunities": ["Gaps in their offering", "Underserved segments", "Pricing vulnerabilities"],
            "threats":       ["Market positioning", "Pricing strategy", "Partnership network"],
        },
        "status": "draft"
    }
    reports = load_reports()
    reports.append(report)
    REPORTS.write_text(json.dumps(reports, indent=2))
    return report

@app.route("/api/health")
def health():
    return jsonify({"status": "online", "service": "competition-researcher", "version": "1.0.0", "port": 8005})

@app.route("/api/status")
def api_status():
    reports = load_reports()
    return jsonify({
        "status":              "ready",
        "reports_generated":   len(reports),
        "last_report":         reports[-1]["researched"] if reports else None,
        "competitors_tracked": list({r["competitor"] for r in reports})
    })

@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    msg  = (data.get("message") or "").strip()
    low  = msg.lower()

    if any(w in low for w in ["status", "count", "how many"]):
        reports  = load_reports()
        tracked  = list({r["competitor"] for r in reports})
        reply    = f"Tracking {len(tracked)} competitors, {len(reports)} reports generated."
        if tracked:
            reply += f" Competitors: {', '.join(tracked)}"

    elif any(w in low for w in ["list", "show", "recent"]):
        reports = load_reports()[-5:]
        if reports:
            lines = [f"• {r['id']} — {r['competitor']} ({r['researched'][:10]})" for r in reports]
            reply = "Recent reports:\n" + "\n".join(lines)
        else:
            reply = "No reports yet. Send: research [Company Name]"

    elif any(w in low for w in ["research", "analyze", "profile", "report on"]):
        name = (low.replace("research", "").replace("analyze", "")
                   .replace("profile", "").replace("report on", "").strip())
        if name:
            r = research_competitor(name.title())
            reply = (f"✓ Report {r['id']} — {r['competitor']}\n"
                     f"Strengths: {'; '.join(r['profile']['strengths'][:2])}\n"
                     f"Status: {r['status']}")
        else:
            reply = "Specify a competitor: research [Company Name]"

    else:
        reply = ("Competition Researcher here — competitive intelligence.\n"
                 "Commands: status, list, research [Company Name]")

    return jsonify({"reply": reply, "operator": "competition-researcher", "status": "online"})

@app.route("/api/task", methods=["POST"])
def api_task():
    data     = request.get_json(silent=True) or {}
    task_id  = data.get("task_id", str(uuid.uuid4()))
    context  = data.get("context", {})
    callback = data.get("callback_url")
    name     = context.get("competitor", data.get("instruction", "Unknown"))
    r = research_competitor(name, context.get("focus"))
    result = {"task_id": task_id, "status": "complete", "operator": "competition-researcher", "result": r}
    if callback:
        try:
            import urllib.request as ur
            ur.urlopen(ur.Request(callback, json.dumps(result).encode(), {"Content-Type": "application/json"}), timeout=3)
        except Exception:
            pass
    return jsonify(result)

@app.route("/")
def index():
    return "<h2>Competition Researcher</h2><p>POST /api/chat | GET /api/status | POST /api/task</p>"

if __name__ == "__main__":
    print("Competition Researcher → http://localhost:8005")
    app.run(host="0.0.0.0", port=8005, debug=False, threaded=True)
