"""
operators/social/connectors/x_oauth_server.py
-----------------------------------------------
X OAuth 1.0a Authorization Server — port 8012

Handles the full "Connect X account" flow:

  1. GET  /connect      — requests a temp token from X, redirects user to X auth page
  2. GET  /callback     — X redirects here with oauth_verifier
  3. POST /exchange     — exchanges verifier for real access tokens, saves to config
  4. GET  /status       — current connection status
  5. POST /disconnect   — removes tokens, resets to simulated mode
  6. GET  /             — serves the connect UI page

Usage:
  - Start this server alongside the social operators
  - Point users to http://localhost:8012 or embed the connect button in PRISM settings
  - Tokens are stored in operators/social/connectors/x.config.json

Requirements:
  - X developer app with OAuth 1.0a enabled, Read + Write permissions
  - Callback URL set to http://localhost:8012/callback in your X app settings
  - api_key and api_secret pre-configured in x.config.json

Never requires tweepy — uses stdlib urllib only for the OAuth dance.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
import base64
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT     = 8012
BASE_DIR = Path(__file__).parent
CONFIG   = BASE_DIR / 'x.config.json'

# X OAuth endpoints
X_REQUEST_TOKEN_URL = 'https://api.twitter.com/oauth/request_token'
X_AUTHORIZE_URL     = 'https://api.twitter.com/oauth/authorize'
X_ACCESS_TOKEN_URL  = 'https://api.twitter.com/oauth/access_token'
X_VERIFY_URL        = 'https://api.twitter.com/1.1/account/verify_credentials.json'
CALLBACK_URL        = f'http://localhost:{PORT}/callback'

# In-memory temp token store (cleared after exchange)
_temp_tokens: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config() -> Dict[str, Any]:
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text())
        except Exception:
            pass
    return {'mode': 'simulated', 'api_key': '', 'api_secret': ''}


def save_config(updates: Dict[str, Any]) -> None:
    cfg = load_config()
    cfg.update(updates)
    CONFIG.write_text(json.dumps(cfg, indent=2))


# ---------------------------------------------------------------------------
# OAuth 1.0a signature builder (stdlib only — no tweepy needed)
# ---------------------------------------------------------------------------

def _percent_encode(s: str) -> str:
    return urllib.parse.quote(str(s), safe='')


def _oauth_header(
    method: str,
    url: str,
    api_key: str,
    api_secret: str,
    token: str = '',
    token_secret: str = '',
    extra_params: Optional[Dict[str, str]] = None,
) -> str:
    """Build an OAuth 1.0a Authorization header."""
    nonce = uuid.uuid4().hex
    ts    = str(int(time.time()))

    oauth_params = {
        'oauth_consumer_key':     api_key,
        'oauth_nonce':            nonce,
        'oauth_signature_method': 'HMAC-SHA1',
        'oauth_timestamp':        ts,
        'oauth_token':            token,
        'oauth_version':          '1.0',
    }
    if not token:
        del oauth_params['oauth_token']
    if extra_params:
        oauth_params.update(extra_params)

    # Build signature base string
    all_params = {**oauth_params, **(extra_params or {})}
    sorted_params = sorted(all_params.items())
    param_str = '&'.join(f'{_percent_encode(k)}={_percent_encode(v)}' for k, v in sorted_params)
    base_str  = f'{method.upper()}&{_percent_encode(url)}&{_percent_encode(param_str)}'

    # Sign
    signing_key = f'{_percent_encode(api_secret)}&{_percent_encode(token_secret)}'
    sig = base64.b64encode(
        hmac.new(signing_key.encode(), base_str.encode(), hashlib.sha1).digest()
    ).decode()
    oauth_params['oauth_signature'] = sig

    # Build header
    header_parts = ', '.join(
        f'{_percent_encode(k)}="{_percent_encode(v)}"'
        for k, v in sorted(oauth_params.items())
    )
    return f'OAuth {header_parts}'


def _x_request(
    method: str,
    url: str,
    api_key: str,
    api_secret: str,
    token: str = '',
    token_secret: str = '',
    params: Optional[Dict[str, str]] = None,
    body: Optional[bytes] = None,
) -> Tuple[int, str]:
    """Make an authenticated request to X API. Returns (status, body)."""
    auth_header = _oauth_header(method, url, api_key, api_secret, token, token_secret, params)
    full_url    = url
    if params and method.upper() == 'GET':
        full_url = f'{url}?{urllib.parse.urlencode(params)}'

    req = urllib.request.Request(
        full_url,
        data=body,
        method=method.upper(),
        headers={
            'Authorization': auth_header,
            'Content-Type': 'application/x-www-form-urlencoded',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as exc:
        return 500, str(exc)


# ---------------------------------------------------------------------------
# OAuth flow steps
# ---------------------------------------------------------------------------

def step1_get_request_token(api_key: str, api_secret: str) -> Tuple[bool, str, str]:
    """
    Step 1: Get a temporary request token from X.
    Returns (success, oauth_token, authorize_url).
    """
    status, body = _x_request(
        'POST', X_REQUEST_TOKEN_URL, api_key, api_secret,
        params={'oauth_callback': CALLBACK_URL},
        body=b'',
    )
    if status != 200:
        return False, '', f'X returned {status}: {body[:200]}'

    params = dict(urllib.parse.parse_qsl(body))
    token  = params.get('oauth_token', '')
    if not token:
        return False, '', 'No oauth_token in response'

    auth_url = f'{X_AUTHORIZE_URL}?oauth_token={token}'
    return True, token, auth_url


def step2_exchange_verifier(
    api_key: str,
    api_secret: str,
    request_token: str,
    verifier: str,
) -> Tuple[bool, Dict[str, str], str]:
    """
    Step 2: Exchange the verifier for real access tokens.
    Returns (success, token_dict, error_message).
    """
    status, body = _x_request(
        'POST', X_ACCESS_TOKEN_URL, api_key, api_secret,
        token=request_token,
        body=f'oauth_verifier={urllib.parse.quote(verifier)}'.encode(),
    )
    if status != 200:
        return False, {}, f'X returned {status}: {body[:200]}'

    params = dict(urllib.parse.parse_qsl(body))
    required = ['oauth_token', 'oauth_token_secret', 'user_id', 'screen_name']
    if not all(k in params for k in required):
        return False, {}, f'Incomplete token response: {list(params.keys())}'

    return True, params, ''


def verify_credentials(
    api_key: str,
    api_secret: str,
    access_token: str,
    access_token_secret: str,
) -> Tuple[bool, str]:
    """Verify the tokens work by calling verify_credentials."""
    status, body = _x_request(
        'GET', X_VERIFY_URL, api_key, api_secret,
        token=access_token,
        token_secret=access_token_secret,
        params={'skip_status': 'true', 'include_entities': 'false'},
    )
    if status == 200:
        try:
            data = json.loads(body)
            return True, data.get('screen_name', 'unknown')
        except Exception:
            return True, 'connected'
    return False, f'Verification failed ({status})'


# ---------------------------------------------------------------------------
# HTML pages
# ---------------------------------------------------------------------------

CONNECT_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect X — Zyrcon-X</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f4f0;min-height:100vh;display:flex;align-items:center;justify-content:center;color:#1a1a18}
.card{background:white;border:.5px solid #e0ddd6;border-radius:14px;padding:32px;width:100%;max-width:440px}
.logo{font-size:13px;font-weight:500;color:#888780;margin-bottom:24px}
h1{font-size:20px;font-weight:500;margin-bottom:6px}
.sub{font-size:13px;color:#888780;margin-bottom:24px;line-height:1.6}
.status-row{display:flex;align-items:center;gap:10px;padding:12px 16px;border-radius:10px;margin-bottom:20px;font-size:13px}
.status-connected{background:#EAF3DE;color:#3B6D11}
.status-pending{background:#f5f4f0;color:#888780}
.status-error{background:#FCEBEB;color:#A32D2D}
.dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.dot-green{background:#639922}
.dot-gray{background:#bbb}
.dot-red{background:#E24B4A}
.connect-btn{width:100%;padding:12px;border-radius:10px;background:#1a1a18;color:white;border:none;font-size:14px;font-weight:500;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:8px;text-decoration:none;margin-bottom:12px}
.connect-btn:hover{background:#333}
.connect-btn:disabled{background:#bbb;cursor:not-allowed}
.disconnect-btn{width:100%;padding:10px;border-radius:10px;background:white;color:#A32D2D;border:.5px solid #F09595;font-size:13px;font-weight:500;cursor:pointer}
.disconnect-btn:hover{background:#FCEBEB}
.divider{height:.5px;background:#e0ddd6;margin:20px 0}
.setup-note{font-size:12px;color:#888780;line-height:1.6}
.setup-note a{color:#534AB7;text-decoration:none}
.setup-note a:hover{text-decoration:underline}
.field{margin-bottom:12px}
.field label{display:block;font-size:12px;color:#888780;margin-bottom:4px}
.field input{width:100%;padding:8px 10px;border:.5px solid #ddd;border-radius:8px;font-size:13px;font-family:inherit;outline:none;background:white;color:#1a1a18}
.field input:focus{border-color:#534AB7}
.save-btn{width:100%;padding:10px;border-radius:8px;background:#534AB7;color:white;border:none;font-size:13px;font-weight:500;cursor:pointer;margin-bottom:8px}
.save-btn:hover{background:#3C3489}
.error-msg{color:#A32D2D;font-size:12px;margin-top:8px}
</style>
</head>
<body>
<div class="card">
  <div class="logo">Zyrcon-X Social</div>
  <h1>Connect your X account</h1>
  <div class="sub">Authorize Zyrcon-X to post on your behalf. You can disconnect at any time.</div>

  <div id="statusRow" class="status-row status-pending">
    <div class="dot dot-gray" id="statusDot"></div>
    <span id="statusText">Checking connection...</span>
  </div>

  <div id="connectSection">
    <a id="connectBtn" class="connect-btn" href="/connect">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="white"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-4.714-6.231-5.401 6.231H2.744l7.73-8.835L1.254 2.25H8.08l4.253 5.622L18.244 2.25z"/></svg>
      Connect X account
    </a>
    <div id="errorMsg" class="error-msg" style="display:none"></div>
  </div>

  <div id="disconnectSection" style="display:none">
    <button class="disconnect-btn" onclick="disconnect()">Disconnect @<span id="screenName"></span></button>
  </div>

  <div class="divider"></div>

  <div id="setupSection">
    <div class="setup-note" style="margin-bottom:14px">
      First time? You need an X developer app with OAuth 1.0a enabled.<br>
      <a href="https://developer.twitter.com/en/apps" target="_blank">Create your app at developer.twitter.com →</a>
    </div>
    <div class="field">
      <label>API Key (Consumer Key)</label>
      <input type="password" id="apiKey" placeholder="paste your API key" />
    </div>
    <div class="field">
      <label>API Key Secret (Consumer Secret)</label>
      <input type="password" id="apiSecret" placeholder="paste your API key secret" />
    </div>
    <button class="save-btn" onclick="saveKeys()">Save app credentials</button>
    <div style="font-size:11px;color:#888780">These are your app credentials — not your account password. Stored locally only.</div>
  </div>
</div>

<script>
async function checkStatus() {
  try {
    const r = await fetch('/status');
    const d = await r.json();
    const dot  = document.getElementById('statusDot');
    const text = document.getElementById('statusText');
    const conn = document.getElementById('connectSection');
    const disc = document.getElementById('disconnectSection');
    const sn   = document.getElementById('screenName');

    if (d.connected) {
      dot.className  = 'dot dot-green';
      document.getElementById('statusRow').className = 'status-row status-connected';
      text.textContent = `Connected as @${d.screen_name}`;
      conn.style.display = 'none';
      disc.style.display = 'block';
      if (sn) sn.textContent = d.screen_name;
    } else {
      dot.className = 'dot dot-gray';
      text.textContent = d.api_key_configured
        ? 'App credentials saved. Click Connect to authorize.'
        : 'Not connected. Save your app credentials below.';
      conn.style.display = 'block';
      disc.style.display = 'none';
    }
  } catch(e) {
    document.getElementById('statusText').textContent = 'Could not reach OAuth server.';
  }
}

async function saveKeys() {
  const key    = document.getElementById('apiKey').value.trim();
  const secret = document.getElementById('apiSecret').value.trim();
  if (!key || !secret) { alert('Both fields are required.'); return; }
  const r = await fetch('/save-keys', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({api_key: key, api_secret: secret}),
  });
  const d = await r.json();
  if (d.ok) {
    document.getElementById('apiKey').value = '';
    document.getElementById('apiSecret').value = '';
    checkStatus();
  }
}

async function disconnect() {
  if (!confirm('Disconnect your X account?')) return;
  await fetch('/disconnect', {method: 'POST'});
  checkStatus();
}

checkStatus();
</script>
</body>
</html>"""


SUCCESS_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Connected — Zyrcon-X</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f4f0;min-height:100vh;display:flex;align-items:center;justify-content:center;color:#1a1a18}
.card{background:white;border:.5px solid #e0ddd6;border-radius:14px;padding:32px;width:100%;max-width:400px;text-align:center}
.icon{font-size:40px;margin-bottom:16px}
h1{font-size:20px;font-weight:500;margin-bottom:8px}
.sub{font-size:13px;color:#888780;margin-bottom:24px}
.back{padding:10px 24px;border-radius:8px;background:#1a1a18;color:white;text-decoration:none;font-size:13px;font-weight:500}
</style>
</head>
<body>
<div class="card">
  <div class="icon">&#10003;</div>
  <h1>X account connected</h1>
  <div class="sub">__SCREEN_NAME__ is now authorized. Zyrcon-X can post on your behalf.</div>
  <a href="/" class="back">Back to connect page</a>
</div>
</body>
</html>"""

ERROR_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Error — Zyrcon-X</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f4f0;min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:white;border:.5px solid #e0ddd6;border-radius:14px;padding:32px;width:100%;max-width:400px;text-align:center}
h1{font-size:18px;font-weight:500;color:#A32D2D;margin-bottom:8px}
.sub{font-size:13px;color:#888780;margin-bottom:20px}
.back{padding:10px 20px;border-radius:8px;background:white;color:#1a1a18;border:.5px solid #ddd;text-decoration:none;font-size:13px}
</style>
</head>
<body>
<div class="card">
  <h1>Authorization failed</h1>
  <div class="sub">__ERROR__</div>
  <a href="/" class="back">Try again</a>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class OAuthHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass  # suppress default access log

    def _send(self, status: int, content_type: str, body: str) -> None:
        encoded = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', len(encoded))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, status: int, data: dict) -> None:
        self._send(status, 'application/json', json.dumps(data))

    def _redirect(self, url: str) -> None:
        self.send_response(302)
        self.send_header('Location', url)
        self.end_headers()

    def _read_body(self) -> dict:
        length = int(self.headers.get('Content-Length', 0))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except Exception:
            return {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path
        qs     = dict(urllib.parse.parse_qsl(parsed.query))

        if path == '/':
            self._send(200, 'text/html', CONNECT_PAGE)

        elif path == '/connect':
            cfg = load_config()
            api_key    = cfg.get('api_key', '')
            api_secret = cfg.get('api_secret', '')

            if not api_key or not api_secret:
                self._redirect('/?error=missing_keys')
                return

            ok, token, auth_url_or_err = step1_get_request_token(api_key, api_secret)
            if not ok:
                err_page = ERROR_PAGE.replace('__ERROR__', f'Could not get request token: {auth_url_or_err}')
                self._send(400, 'text/html', err_page)
                return

            _temp_tokens['request_token'] = token
            self._redirect(auth_url_or_err)

        elif path == '/callback':
            token    = qs.get('oauth_token', '')
            verifier = qs.get('oauth_verifier', '')

            if not token or not verifier:
                denied = qs.get('denied', '')
                err = 'Authorization was denied.' if denied else 'Missing oauth_token or verifier.'
                self._send(400, 'text/html', ERROR_PAGE.replace('__ERROR__', err))
                return

            cfg        = load_config()
            api_key    = cfg.get('api_key', '')
            api_secret = cfg.get('api_secret', '')
            stored_token = _temp_tokens.get('request_token', token)

            ok, tokens, err = step2_exchange_verifier(api_key, api_secret, stored_token, verifier)
            if not ok:
                self._send(400, 'text/html', ERROR_PAGE.replace('__ERROR__', err))
                return

            screen_name = tokens.get('screen_name', 'unknown')

            # Save tokens and flip to live mode
            save_config({
                'mode':                'live',
                'access_token':        tokens['oauth_token'],
                'access_token_secret': tokens['oauth_token_secret'],
                'x_user_id':           tokens.get('user_id', ''),
                'screen_name':         screen_name,
            })
            _temp_tokens.clear()

            self._send(200, 'text/html', SUCCESS_PAGE.replace('__SCREEN_NAME__', f'@{screen_name}'))

        elif path == '/status':
            cfg = load_config()
            connected = bool(cfg.get('access_token') and cfg.get('access_token_secret') and cfg.get('mode') == 'live')
            self._send_json(200, {
                'connected':          connected,
                'mode':               cfg.get('mode', 'simulated'),
                'screen_name':        cfg.get('screen_name', ''),
                'api_key_configured': bool(cfg.get('api_key')),
            })

        else:
            self._send(404, 'text/plain', 'Not found')

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self._read_body()

        if path == '/save-keys':
            api_key    = (body.get('api_key') or '').strip()
            api_secret = (body.get('api_secret') or '').strip()
            if not api_key or not api_secret:
                self._send_json(400, {'ok': False, 'error': 'Both fields required'})
                return
            save_config({'api_key': api_key, 'api_secret': api_secret})
            self._send_json(200, {'ok': True})

        elif path == '/disconnect':
            save_config({
                'mode':                'simulated',
                'access_token':        '',
                'access_token_secret': '',
                'screen_name':         '',
                'x_user_id':           '',
            })
            self._send_json(200, {'ok': True})

        else:
            self._send(404, 'text/plain', 'Not found')

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run():
    server = HTTPServer(('127.0.0.1', PORT), OAuthHandler)
    print(f'X OAuth server → http://localhost:{PORT}')
    print(f'Open that URL to connect your X account.')
    server.serve_forever()


if __name__ == '__main__':
    run()
