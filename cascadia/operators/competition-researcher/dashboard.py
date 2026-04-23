#!/usr/bin/env python3
"""Competition Researcher Operator -- Zyrcon OS -- Competitive intelligence agent"""
import json as _json, os, time, logging
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import requests as _req

app = Flask(__name__)
CORS(app)

PORT          = int(os.environ.get('ZYRCON_PORT', '8005'))
OPERATOR_ID   = 'competition-researcher'
OPERATOR_NAME = 'Competition Researcher'
VERSION       = '1.0.0'
LLM_URL       = os.environ.get('ZYRCON_LLM_URL', 'http://127.0.0.1:8080')
VAULT_DIR     = Path(os.environ.get('CASCADIA_VAULT',
                     os.path.expanduser('~/cascadia-os/data/vault'))) / OPERATOR_ID
VAULT_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s | Competition Researcher | %(message)s')
log = logging.getLogger(__name__)

SYSTEM_PROMPT = 'You are Competition Researcher, a competitive intelligence agent for Zyrcon OS.\nYou research competitors and produce actionable intelligence reports.\n\nCapabilities:\n- Research competitors by name, market, or product category\n- Identify pricing, positioning, strengths, weaknesses, and recent moves\n- Compare competitors against Zyrcon Labs offerings\n- Produce structured intelligence reports with strategic recommendations\n\nWhen researching a competitor cover: Overview, Pricing, Strengths, Weaknesses, Recent moves, Strategic implications.\nBe factual and specific. Flag what is uncertain. Focus on actionable insights.'

_stats = dict(started_at=datetime.now().isoformat(),
              messages_handled=0, last_message_at=None, status='ready')

@app.route('/api/health')
def health():
    return jsonify(dict(service=OPERATOR_ID, name=OPERATOR_NAME,
                        status='online', version=VERSION, port=PORT))

@app.route('/api/status')
def status():
    return jsonify(dict(operator=OPERATOR_ID, name=OPERATOR_NAME,
                        state=_stats['status'], started_at=_stats['started_at'],
                        messages_handled=_stats['messages_handled'],
                        last_message_at=_stats['last_message_at']))

@app.route('/api/chat', methods=['POST'])
def chat():
    data    = request.get_json() or {}
    message = data.get('message', '').strip()
    history = data.get('history', [])
    if not message:
        return jsonify(dict(error='message required')), 400
    _stats['messages_handled'] += 1
    _stats['last_message_at']   = datetime.now().isoformat()
    _stats['status']             = 'working'
    messages = [dict(role='system', content=SYSTEM_PROMPT)]
    for h in history[-10:]:
        messages.append(dict(role=h.get('role','user'), content=h.get('content','')))
    messages.append(dict(role='user', content=message))
    def generate():
        try:
            resp = _req.post(
                LLM_URL.rstrip('/') + '/v1/chat/completions',
                json=dict(model=os.environ.get('ZYRCON_MODEL','default'),
                          messages=messages, stream=True,
                          temperature=0.7, max_tokens=1024),
                stream=True, timeout=60)
            for line in resp.iter_lines():
                if not line or not line.startswith(b'data: '):
                    continue
                chunk = line[6:]
                if chunk == b'[DONE]':
                    break
                try:
                    d = _json.loads(chunk)
                    delta = d['choices'][0]['delta'].get('content','')
                    if delta:
                        yield 'data: ' + _json.dumps(dict(content=delta)) + '\n\n'
                except Exception:
                    pass
        except Exception as exc:
            yield 'data: ' + _json.dumps(dict(content='Could not reach AI model: ' + str(exc))) + '\n\n'
        finally:
            _stats['status'] = 'ready'
        yield 'data: [DONE]\n\n'
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route('/api/task', methods=['POST'])
def task():
    data = request.get_json() or {}
    task_id = data.get('task_id', 'task_' + str(int(time.time())))
    log.info('Task: %s -- %s', task_id, data.get('instruction','')[:80])
    return jsonify(dict(task_id=task_id, status='accepted', operator=OPERATOR_ID))

if __name__ == '__main__':
    log.info('%s starting on port %s', OPERATOR_NAME, PORT)
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
