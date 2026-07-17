# Adobe Learning Manager (ALM) MCP Server

Wraps the ALM Prime API v2 so an LLM client can look up learners, browse
learning objects, manage enrollments, check badges, and kick off async
jobs (CSV exports, certificate generation).

Reference docs: https://learningmanager.adobe.com/docs/primeapi/v2/

## Auth: you need an OAuth access token first

ALM's Prime API uses OAuth 2.0, not a static API key. Before this server
is useful you need:

1. An ALM integration registered (Admin > Integrations in ALM, or via
   your Adobe admin console) to get a client ID/secret.
2. An OAuth access token obtained through ALM's auth flow using that
   client ID/secret.

This server takes a **already-issued access token** via `ALM_ACCESS_TOKEN`
as a fallback default — but if multiple people connect to this server
(the point of hosting it remotely), each person should call
`set_access_token` with their own token instead of relying on the
shared default. See "Multi-user auth model" below for why. This server
does not handle the OAuth exchange or refresh itself — for long-lived
use, either:
- refresh the token externally and call `set_access_token` again, or
- extend `_get_token()` / add a token-refresh helper if you have refresh
  tokens available.

Requests are sent as `Authorization: oauth <token>` (ALM's documented
header format) with JSON:API content types.

## Multi-user auth model (read this before deploying for a team)

This server is designed to be safely used by multiple people connected
at once — e.g. several Adobe employees, each with their own ALM
identity. Tokens are stored **per MCP session**, not globally:

- Each connecting client gets its own isolated token once they call
  `set_access_token`.
- One person's token can never leak into or affect another person's
  requests, even if both are using the server at the same time.
- If a session hasn't called `set_access_token` yet, it falls back to
  the server's `ALM_ACCESS_TOKEN` env var (if set) — fine for solo
  testing, but every real user should set their own token rather than
  share that default once this is used by more than one person.

This is still simpler than full OAuth: there's no real login flow, and
nothing stops someone from pasting in a token that isn't theirs. The
proper next step is a real OAuth Resource Server backed by Adobe IMS
(`TokenVerifier` + `AuthSettings` from `mcp.server.auth`), where each
person authenticates via an actual login redirect instead of manually
copying a token. That requires registering an OAuth client in Adobe
IMS's console — an Adobe-internal identity/IT action — so it's a
deliberately separate, bigger step from what's implemented here.

## Setup

```bash
cd alm-mcp-server
uv venv
source .venv/bin/activate
uv sync
```

## Tools included

**Users**
- `list_users(page_limit, page_offset, filter_field, filter_value)`
- `get_user(user_id)`
- `create_user(name, email, user_type, user_unique_id)`
- `update_user(user_id, attributes)`
- `delete_user(user_id)`
- `list_user_groups(user_id, page_limit)`

**Learning objects & catalogs**
- `list_learning_objects(lo_types, page_limit)`
- `get_learning_object(lo_id, include)`
- `list_catalogs(page_limit)`

**Enrollments**
- `list_enrollments(user_id, lo_types, sort)`
- `enroll_user(user_id, lo_id, lo_instance_id, allow_multi_enrollment)`
- `get_enrollment(user_id, enrollment_id, include)`
- `unenroll_user(user_id, enrollment_id)`

**Badges**
- `list_user_badges(user_id)`

**Jobs (async)**
- `create_job(job_type, job_params)`
- `get_job_status(job_id)`

**Auth maintenance**
- `set_access_token(new_token)` — set **your own** OAuth token for **your session only**, without restarting the server. Use this when you first connect, or when you hit a 401 "Token expired" error. Isolated per-session — does not affect or get affected by any other connected user.

## Test it

```bash
npx @modelcontextprotocol/inspector python server.py
```

Try `list_users` with a small `page_limit` first to confirm auth works
before attempting writes.

## Connect to Claude Desktop

```json
{
  "mcpServers": {
    "adobe_learning_manager": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/alm-mcp-server",
        "run",
        "server.py"
      ],
      "env": {
        "ALM_BASE_URL": "https://learningmanager.adobe.com",
        "ALM_ACCESS_TOKEN": "your-oauth-access-token"
      }
    }
  }
}
```

Use `https://learningmanagereu.adobe.com` (or your account's assigned
region) instead if your ALM tenant is EU-hosted.

## Known gotcha: eventual consistency on create-then-enroll

If you call `create_user` and immediately call `enroll_user` for that
same user, ALM's enrollment lookup can briefly fail with "object
doesn't exist" — the new user hasn't finished indexing yet. If you hit
this, add a short delay (a few seconds) and retry once or twice with
backoff before treating it as a real error.

## Scope note

This server is read-heavy for learning objects and read/write for
users + enrollments, which matches how the API is actually used in
practice — ALM's write API is oriented around learners and their
relationship to content (enroll, track progress) rather than authoring
course content itself, which is typically done through the ALM UI.

## Before production use

- Scope the OAuth integration's permissions as narrowly as ALM allows
  (e.g. a service account without full admin rights) rather than using
  a personal admin token.
- Treat `delete_user` and `unenroll_user` as destructive — consider
  requiring the caller to have just fetched the record via `get_user`
  / `get_enrollment` before deleting, so the model is acting on a
  confirmed ID rather than a guessed one.
- Add retry/backoff for the eventual-consistency gotcha above if you
  automate create+enroll flows.
