# Deploying alm-mcp-server to Render (remote/HTTP)

This is the **remote-hosted** variant of the ALM MCP server — it runs over
Streamable HTTP instead of stdio, so it can be reached over the network
(e.g. for Claude's org-level Custom Connectors, or any remote MCP client).

⚠️ **Before deploying anywhere**: this server can read/write real Adobe
employee data via ALM (names, emails, enrollments). A free public hosting
tier has no data-handling guarantees and no audit trail. This is fine for
a personal proof-of-concept, but talk to your internal security/platform
team before pointing real colleagues at it. See the note in the main
project's conversation history for more on this.

## What changed from the local (stdio) version

- `server.py`'s entrypoint now runs `mcp.run(transport="streamable-http", host="0.0.0.0", port=...)` instead of `transport="stdio"`
- Added `requirements.txt` (Render's Python runtime installs from this, not `pyproject.toml`)
- Added `render.yaml` (optional — lets Render auto-configure the service from this repo instead of manual dashboard clicks)

## Step 1: Get the code into a Git repo

If it isn't already:

```bash
cd alm-mcp-server-remote
git init
git add .
git commit -m "Initial commit: ALM MCP server (remote HTTP variant)"
```

Then create an empty repo on GitHub (or GitLab/Bitbucket) and push:

```bash
git remote add origin https://github.com/<your-username>/alm-mcp-server-remote.git
git branch -M main
git push -u origin main
```

## Step 2: Create a Web Service on Render (not Static Site)

1. Go to [dashboard.render.com](https://dashboard.render.com)
2. Click **New +** → **Web Service**
3. Connect the GitHub/GitLab/Bitbucket repo you just pushed to
4. Render should auto-detect `render.yaml` and pre-fill settings. If not, set manually:
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python server.py`
5. Under **Environment Variables**, add:
   - `ALM_BASE_URL` = `https://learningmanager.adobe.com`
   - `ALM_ACCESS_TOKEN` = *(your real token — enter this directly in the dashboard, never commit it to the repo)*
6. Click **Create Web Service**

## Step 3: Get your server's URL

Once deployed, Render gives you a URL like:
```
https://alm-mcp-server-remote.onrender.com
```
Your MCP endpoint is that URL + `/mcp`:
```
https://alm-mcp-server-remote.onrender.com/mcp
```
That full URL (with `/mcp`) is what you'd enter when adding this as a custom connector.

## Known limitation: free tier cold starts

Render's free web services spin down after ~15 minutes of inactivity. The
first request after idle can take 30+ seconds to respond while it wakes
back up — this may exceed a client's tool-call timeout. This is one of
the concrete reasons this tier isn't suitable for real day-to-day use by
colleagues; it's fine for testing the remote-HTTP path works at all.

## Testing it

```bash
curl -X POST https://alm-mcp-server-remote.onrender.com/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
```
A response containing `serverInfo` confirms it's up and speaking MCP correctly.
