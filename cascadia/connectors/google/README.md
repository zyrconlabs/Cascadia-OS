# Google Accounts Connector

Handles Google OAuth2 authentication and account identity for Cascadia OS.
This connector is the **shared auth layer** for all Google services — Gmail,
Calendar, Drive, and other Google-backed operators obtain their access tokens
through this connector rather than managing credentials independently.

## What it does

- Generates OAuth2 authorization URLs for user consent
- Handles the OAuth2 redirect callback at `/oauth2/callback`
- Exchanges authorization codes for access and refresh tokens
- Refreshes expired access tokens automatically
- Provides the authenticated user's Google profile (email, name, picture)
- Revokes tokens on request (requires human approval)
- Persists tokens locally at `~/.cascadia/google_tokens.json`

## Port

**9020** — localhost only. The `/oauth2/callback` endpoint must be accessible
from the user's browser, so the browser must be running on the same machine
(or the redirect URI must be updated for your network topology).

## Tier

**Pro** tier and above.

## Prerequisites

- A Google account
- A Google Cloud project with OAuth2 credentials

## Setup

### 1. Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Click **Select a project → New Project**
3. Name it (e.g. `cascadia-os`) and click **Create**

### 2. Create OAuth2 credentials

1. Navigate to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth 2.0 Client ID**
3. Configure the consent screen if prompted (External, add your email)
4. Application type: **Web application**
5. Authorized redirect URIs: add `http://localhost:9020/oauth2/callback`
6. Click **Create** and copy the **Client ID** and **Client Secret**

### 3. Enable required APIs

In **APIs & Services → Library**, enable:
- **Google People API** (for `get_user_info`)
- Any additional APIs your operators need (Gmail API, Calendar API, etc.)

### 4. Set environment variables

```bash
export GOOGLE_CLIENT_ID=your_client_id.apps.googleusercontent.com
export GOOGLE_CLIENT_SECRET=your_client_secret
```

Add these to your shell profile or Cascadia OS `config.json` so they persist
across restarts.

### 5. Authenticate

Once the connector is running, trigger the auth flow from your operator or
directly via NATS:

```bash
# Publish a get_auth_url request
nats pub cascadia.connectors.google-connector.call \
  '{"action":"get_auth_url","scopes":["openid","email","profile"]}'
```

Open the returned URL in a browser. After granting consent, the connector
receives the callback, exchanges the code, and persists the tokens.

## NATS Actions

| Action | Approval | Description |
|---|---|---|
| `get_auth_url` | No | Build an OAuth2 authorization URL |
| `exchange_code` | No | Exchange an auth code for tokens |
| `refresh_access_token` | No | Refresh an expired access token |
| `get_user_info` | No | Fetch the authenticated user's profile |
| `revoke_token` | **Yes** | Revoke stored tokens (cannot be undone without re-auth) |

### Request envelope examples

**get_auth_url**
```json
{
  "action": "get_auth_url",
  "scopes": ["openid", "email", "profile", "https://www.googleapis.com/auth/gmail.send"],
  "state": "optional-csrf-token"
}
```

**get_user_info**
```json
{
  "action": "get_user_info"
}
```

**refresh_access_token**
```json
{
  "action": "refresh_access_token"
}
```

## NATS Subjects

| Subject | Direction | Purpose |
|---|---|---|
| `cascadia.connectors.google-connector.call` | inbound | Trigger an action |
| `cascadia.connectors.google-connector.response` | outbound | Action result |
| `cascadia.connectors.google-connector.status` | outbound | Status events |

## Token storage

Tokens are persisted at `~/.cascadia/google_tokens.json`. The file is
created with standard user permissions. To use a different path:

```bash
export GOOGLE_TOKEN_FILE=/path/to/your/tokens.json
```

## Dependency note

`safe_to_uninstall` is `false` in the manifest. If the Gmail or Google
Calendar connectors are installed, removing this connector will break their
token refresh. Uninstall those connectors first, or use `revoke_token` to
cleanly deauthorize before removing.

## Source

Connector source: `cascadia/connectors/google/`
DEPOT package: `cascadia-os-operators/cascadia/connectors/google/`
