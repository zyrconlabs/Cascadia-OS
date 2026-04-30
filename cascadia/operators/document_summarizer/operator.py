"""
C6: Document Summarizer — Cascadia OS Operator
Port: 8106  Subject prefix: cascadia.operators.document-summarizer
"""

import asyncio
import json
import os
import re
import uuid
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

import nats

NAME = "document-summarizer"
VERSION = "1.0.0"
PORT = 8106
NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")

CALL_SUBJECT = f"cascadia.operators.{NAME}.call"
RESPONSE_SUBJECT = f"cascadia.operators.{NAME}.response"
APPROVALS_SUBJECT = "cascadia.approvals.request"

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _split_sentences(text: str) -> list:
    """Split text into sentences using simple punctuation rules."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    return [s.strip() for s in sentences if s.strip()]


def summarize_text(text: str, max_sentences: int = 5) -> dict:
    """Extractive summarization: score sentences by word-frequency TF."""
    sentences = _split_sentences(text)
    if not sentences:
        return {"summary": "", "sentence_count": 0, "original_sentences": 0}

    # Build word frequency table
    words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
    freq = Counter(words)
    if not freq:
        top = sentences[:max_sentences]
        return {
            "summary": " ".join(top),
            "sentence_count": len(top),
            "original_sentences": len(sentences),
        }

    max_freq = max(freq.values())
    normalized = {w: c / max_freq for w, c in freq.items()}

    # Score each sentence
    scored = []
    for idx, sent in enumerate(sentences):
        sent_words = re.findall(r'\b[a-zA-Z]{3,}\b', sent.lower())
        score = sum(normalized.get(w, 0) for w in sent_words)
        scored.append((score, idx, sent))

    scored.sort(key=lambda x: -x[0])
    top = scored[:max_sentences]
    # Restore original order
    top.sort(key=lambda x: x[1])
    summary_sentences = [t[2] for t in top]

    return {
        "summary": " ".join(summary_sentences),
        "sentence_count": len(summary_sentences),
        "original_sentences": len(sentences),
    }


def summarize_file(file_path: str, max_sentences: int = 5) -> dict:
    """Read a local file (txt, md, csv) and summarize its contents."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in (".txt", ".md", ".csv"):
        return {"error": f"Unsupported file type: {ext}. Supported: txt, md, csv"}
    if not os.path.isfile(file_path):
        return {"error": f"File not found: {file_path}"}
    with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    result = summarize_text(text, max_sentences=max_sentences)
    result["file_path"] = file_path
    return result


def extract_keywords(text: str, top_n: int = 10) -> dict:
    """Return top_n keywords by frequency; skip words under 4 chars."""
    words = re.findall(r'\b[a-zA-Z]{4,}\b', text.lower())
    freq = Counter(words)
    keywords = [{"word": w, "count": c} for w, c in freq.most_common(top_n)]
    return {"keywords": keywords, "total_unique": len(freq)}


# ---------------------------------------------------------------------------
# Approval-gated actions
# ---------------------------------------------------------------------------

async def _request_approval(nc, action: str, payload: dict) -> dict:
    """Publish an approval request and return the envelope."""
    request_id = str(uuid.uuid4())
    envelope = {
        "request_id": request_id,
        "operator": NAME,
        "action": action,
        "payload": payload,
        "requested_at": datetime.utcnow().isoformat(),
    }
    await nc.publish(APPROVALS_SUBJECT, json.dumps(envelope).encode())
    return {"status": "pending_approval", "request_id": request_id, "action": action}


# ---------------------------------------------------------------------------
# Task dispatcher
# ---------------------------------------------------------------------------

def execute_task(payload: dict) -> dict:
    """Sync dispatcher — routes action to the correct function."""
    action = payload.get("action", "")
    params = payload.get("params", {})

    if action == "summarize_text":
        text = params.get("text", "")
        max_s = int(params.get("max_sentences", 5))
        return summarize_text(text, max_sentences=max_s)

    elif action == "summarize_file":
        file_path = params.get("file_path", "")
        max_s = int(params.get("max_sentences", 5))
        return summarize_file(file_path, max_sentences=max_s)

    elif action == "extract_keywords":
        text = params.get("text", "")
        top_n = int(params.get("top_n", 10))
        return extract_keywords(text, top_n=top_n)

    elif action == "export_summary":
        # Approval-gated — handled async
        return {"error": "export_summary requires async context; use handle_event"}

    else:
        return {"error": f"Unknown action: {action}"}


# ---------------------------------------------------------------------------
# NATS handler
# ---------------------------------------------------------------------------

async def handle_event(nc, subject: str, raw: bytes):
    """Async NATS message handler."""
    try:
        payload = json.loads(raw.decode())
    except Exception as exc:
        result = {"error": f"Invalid JSON: {exc}"}
        await nc.publish(RESPONSE_SUBJECT, json.dumps(result).encode())
        return

    action = payload.get("action", "")

    if action == "export_summary":
        result = await _request_approval(nc, action, payload.get("params", {}))
    else:
        result = execute_task(payload)

    result["operator"] = NAME
    result["version"] = VERSION
    await nc.publish(RESPONSE_SUBJECT, json.dumps(result).encode())


# ---------------------------------------------------------------------------
# Health HTTP server
# ---------------------------------------------------------------------------

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            body = json.dumps({"status": "ok", "operator": NAME, "version": VERSION, "port": PORT}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _nats_loop():
    nc = await nats.connect(NATS_URL)
    print(f"[{NAME}] Connected to NATS at {NATS_URL}")

    async def _cb(msg):
        await handle_event(nc, msg.subject, msg.data)

    await nc.subscribe(CALL_SUBJECT, cb=_cb)
    print(f"[{NAME}] Subscribed to {CALL_SUBJECT}")

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await nc.drain()


def main():
    health_server = HTTPServer(("0.0.0.0", PORT), _HealthHandler)
    t = threading.Thread(target=health_server.serve_forever, daemon=True)
    t.start()
    print(f"[{NAME}] Health endpoint running on port {PORT}")

    asyncio.run(_nats_loop())


if __name__ == "__main__":
    main()
