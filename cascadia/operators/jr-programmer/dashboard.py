#!/usr/bin/env python3
"""Jr. Programmer Operator -- Zyrcon OS -- Software development assistant"""
import json as _json, os, time, logging
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import requests as _req

app = Flask(__name__)
CORS(app)

PORT          = int(os.environ.get('ZYRCON_PORT', '8004'))
OPERATOR_ID   = 'jr-programmer'
OPERATOR_NAME = 'Jr. Programmer'
VERSION       = '1.0.0'
LLM_URL       = os.environ.get('ZYRCON_LLM_URL', 'http://127.0.0.1:8080')
VAULT_DIR     = Path(os.environ.get('CASCADIA_VAULT',
                     os.path.expanduser('~/cascadia-os/data/vault'))) / OPERATOR_ID
VAULT_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s | Jr. Programmer | %(message)s')
log = logging.getLogger(__name__)

SYSTEM_PROMPT = 'You are Jr. Programmer, a software development assistant on Zyrcon OS.\nYou write code, debug issues, break down architecture, and improve codebases.\n\nCapabilities:\n- Write clean working code in Python, JavaScript, bash, and other languages\n- Debug errors: read tracebacks and identify root causes\n- Break down software architecture and explain design decisions\n- Review and improve existing code\n- Write tests and documentation\n\nWorking style:\n- Always show complete runnable code -- never truncate\n- When debugging, identify root cause before proposing a fix\n- Ask clarifying questions if requirements are ambiguous\n- Prefer simple readable solutions over clever ones\n- Flag potential security issues or edge cases proactively'

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
