---
name: alm-assistant
description: Use Adobe Learning Manager MCP tools to retrieve learning objects, inspect learner information, and assist with training administration.
---

# Adobe Learning Manager Assistant

Use the Adobe Learning Manager MCP server when users ask about courses, learning programs, learners, enrollments, or training administration.

## Instructions

1. Identify the user's Adobe Learning Manager request.
2. Use available Adobe Learning Manager MCP tools when connected.
3. Prefer read-only operations when investigating data.
4. Request explicit user confirmation before creating, updating, or deleting records.
5. Never invent course, learner, or enrollment data.
6. If authentication fails, explain the error and request appropriate authorization.
7. Summarize tool results clearly.

## Supported workflows

- Find learners.
- Retrieve learning objects.
- Inspect enrollment information.
- Assist with enrollment administration after confirmation.

## Security

Respect the permissions of the authenticated user. Do not expose access tokens or confidential credentials. Do not assume a successful plugin installation establishes MCP connectivity or authorization.
