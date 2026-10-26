# Adobe Coworker marketplace setup

The marketplace root is `.claude-plugin/marketplace.json`. The `alm-mcp` plugin lives in `plugins/alm-mcp` and connects to `https://alm-mcp.onrender.com/mcp` through its `.mcp.json`.

## Install
1. Push this repository structure to GitHub (preserving hidden `.claude-plugin` directories).
2. In Adobe Coworker, add `https://github.com/Vijender001223/ALM-MCP` as a marketplace.
3. Install the `alm-mcp` plugin.
4. Check MCP server connection and tool discovery.

## Render
`render.yaml` now declares `MCP_TRANSPORT=streamable-http` and `MCP_HOST=0.0.0.0`. If the Render service is managed outside a Blueprint, update its environment variables in the Render dashboard; pushing this YAML alone may not apply them. The current endpoint has already returned a successful MCP initialize handshake.

## Security and validation
- This configuration adds **no MCP authentication**. Before sharing with multiple users, require authenticated MCP requests and enforce per-user authorization for ALM tools, particularly write operations.
- Do not commit ALM tokens, client secrets, or privileged credentials.
- Validate `tools/list` and a read-only tool call before enabling any write actions.
- Confirm Coworker supports this Claude-style marketplace manifest and HTTP MCP transport in your tenant.
- A successful MCP initialize handshake alone does not prove plugin installation or ALM authorization.
