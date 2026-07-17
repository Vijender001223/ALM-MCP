"""
Adobe Learning Manager (ALM) MCP Server — multi-user remote variant
=====================================================================

Wraps the ALM Prime API v2 (https://learningmanager.adobe.com/docs/primeapi/v2/)
as an MCP server: user management, learning-object lookup, enrollments,
badges, and async jobs (e.g. certificate generation, bulk export).

This is the REMOTE (Streamable HTTP) variant, designed to be used by
MULTIPLE people connecting to one hosted server — e.g. several Adobe
employees, each with their own ALM identity, all talking to the same
deployed instance.

WHY THIS ISN'T A SINGLE SHARED TOKEN
-------------------------------------
An earlier version of this server stored ALM_ACCESS_TOKEN as one global
variable. That's fine for a single person running it locally, but it is
NOT safe for a shared remote deployment: if two people are connected at
once, whoever last called set_access_token would silently start
affecting *everyone else's* requests too — a real cross-user data leak,
not just a rough edge.

This version instead keys tokens per MCP session, using the identity of
the current request's ServerSession object (via the injected Context
parameter every tool receives) as the dictionary key. Each connected
client gets its own isolated token; nobody can see or affect another
session's ALM data.

WORKFLOW FOR EACH PERSON CONNECTING
------------------------------------
1. Connect to this server (e.g. as a Claude custom connector).
2. Call set_access_token with YOUR OWN ALM OAuth access token before
   calling anything else. Every other tool call in your session will
   then use your token specifically.
3. If you don't call set_access_token, tools fall back to the server's
   default ALM_ACCESS_TOKEN env var (if set) — useful for solo testing,
   but that shared default should NOT be relied on once multiple real
   people are using this server; each person should set their own.

NOTE: This is still a simpler interim measure, not full OAuth. A person
could, in principle, paste in someone else's token. The next real step
up from here is a proper OAuth Resource Server implementation backed by
Adobe IMS (TokenVerifier + AuthSettings, from mcp.server.auth), where
each user authenticates via a real login flow instead of pasting a
token manually. That's a bigger lift requiring an Adobe IMS OAuth
client registration and is out of scope for this version.

AUTH (per-token details, same as before)
------------------------------------------
ALM's Prime API uses OAuth 2.0, sent as "Authorization: oauth <token>"
(not a standard Bearer header), with JSON:API content types.

WHAT THIS COVERS
----------------
Users:
  - list_users, get_user, create_user, update_user, delete_user, list_user_groups
Learning objects:
  - list_learning_objects, get_learning_object, list_catalogs
Enrollments:
  - list_enrollments, enroll_user, get_enrollment, unenroll_user
Badges:
  - list_user_badges
Jobs (async operations like CSV export / certificate PDFs):
  - create_job, get_job_status
Auth maintenance:
  - set_access_token (per-session, see above)
"""

import os
import sys
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import Context, FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Use the region-appropriate host, e.g.:
#   https://learningmanager.adobe.com
#   https://learningmanagereu.adobe.com
BASE_URL = os.environ.get("ALM_BASE_URL", "https://learningmanager.adobe.com")

# This is a FALLBACK ONLY — used if a given session hasn't called
# set_access_token yet. Fine for solo testing; once multiple real
# people use this server, each should set their own token instead of
# relying on this shared default.
DEFAULT_ACCESS_TOKEN = os.environ.get("ALM_ACCESS_TOKEN")

if not DEFAULT_ACCESS_TOKEN:
    print(
        "NOTE: ALM_ACCESS_TOKEN environment variable is not set. "
        "This is fine as long as every connecting user calls "
        "set_access_token with their own token before using other tools.",
        file=sys.stderr,
    )

API_ROOT = "/primeapi/v2"
REQUEST_TIMEOUT = 30.0
JSON_API_CONTENT_TYPE = "application/vnd.api+json;charset=UTF-8"

_port = int(os.environ.get("PORT", 8000))
mcp = FastMCP("adobe_learning_manager", host="0.0.0.0", port=_port)

# Per-session token storage: keyed by id() of the current request's
# ServerSession object, which is stable for the lifetime of one
# connected client's session and unique among concurrently-live sessions.
_session_tokens: dict[int, str] = {}


def _session_key(ctx: Context) -> int:
    return id(ctx.request_context.session)


def _get_token(ctx: Context) -> Optional[str]:
    return _session_tokens.get(_session_key(ctx), DEFAULT_ACCESS_TOKEN)


def _headers(token: Optional[str]) -> dict:
    return {
        "Authorization": f"oauth {token}",
        "Accept": "application/vnd.api+json",
        "Content-Type": JSON_API_CONTENT_TYPE,
    }


@mcp.tool()
async def set_access_token(new_token: str, ctx: Context) -> Any:
    """
    Set YOUR OWN ALM OAuth access token for this session, without
    restarting the server. Call this once before using any other tool —
    every subsequent tool call you make will use this token.

    This is per-session: your token is isolated from any other person
    connected to this same server at the same time. Setting your token
    does not affect anyone else's session, and nobody else's token
    affects yours.

    Use this when you first connect, or again later if you hit a 401
    "Token expired" error and have regenerated a fresh token.

    Args:
        new_token: Your OAuth access token string (just the raw token —
            do not include the "oauth " prefix).
    """
    _session_tokens[_session_key(ctx)] = new_token.strip()
    return {
        "status": "updated",
        "note": "Your token is now active for this session only. Other users of this server are unaffected, and you are unaffected by them.",
    }


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

async def _request(
    ctx: Context,
    method: str,
    path: str,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
) -> Any:
    token = _get_token(ctx)
    if not token:
        raise RuntimeError(
            "No ALM access token set for this session. Call set_access_token "
            "with your own ALM OAuth token first."
        )

    url = f"{BASE_URL.rstrip('/')}{API_ROOT}{path}"
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            resp = await client.request(
                method, url, headers=_headers(token), params=params, json=json_body
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"ALM API request failed: {e.response.status_code} "
                f"{e.response.reason_phrase} — {e.response.text[:500]}"
            ) from e
        except httpx.RequestError as e:
            raise RuntimeError(f"Network error calling ALM: {e}") from e

        if resp.status_code == 204 or not resp.content:
            return {"status": "ok"}
        return resp.json()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_users(
    ctx: Context,
    page_limit: int = 20,
    page_offset: int = 0,
    filter_field: Optional[str] = None,
    filter_value: Optional[str] = None,
) -> Any:
    """
    List learners in the ALM account.

    ALM has no fuzzy/partial name search on this endpoint — filtering
    is field-specific and exact-match (e.g. by email or account ID),
    following the same filter.<field>=<value> pattern used across all
    ALM APIs (e.g. filter.loTypes, filter.ids). There is no bare
    "filter" parameter; passing one will fail with a "Field Type
    incorrect" error.

    For a name that might be partial or misspelled, don't guess a
    filter — call this with no filter args to pull the user list
    (increase page_limit up to ALM's max of 100, and walk pages if
    needed) and match the name client-side instead.

    Args:
        page_limit: Max number of users to return per page.
        page_offset: Offset for pagination.
        filter_field: Field to filter on, e.g. "email" or "ids". Must
            be paired with filter_value. Omit both for an unfiltered list.
        filter_value: Exact value to match for filter_field, e.g. a
            specific email address.
    """
    params = {"page[limit]": page_limit, "page[offset]": page_offset}
    if filter_field and filter_value:
        params[f"filter.{filter_field}"] = filter_value
    try:
        return await _request(ctx, "GET", "/users", params=params)
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def get_user(user_id: str, ctx: Context) -> Any:
    """
    Get a single learner's profile by ALM user ID.

    Args:
        user_id: The ALM numeric/string user ID.
    """
    try:
        return await _request(ctx, "GET", f"/users/{user_id}")
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def create_user(
    name: str,
    email: str,
    ctx: Context,
    user_type: str = "INTERNAL",
    user_unique_id: Optional[str] = None,
) -> Any:
    """
    Create a new learner in ALM.

    Args:
        name: Full display name.
        email: Email address (also commonly used as the unique ID).
        user_type: "INTERNAL" or "EXTERNAL" depending on your account setup.
        user_unique_id: Optional unique identifier; defaults to email if omitted.
    """
    payload = {
        "data": {
            "type": "user",
            "attributes": {
                "name": name,
                "email": email,
                "userType": user_type,
                "userUniqueId": user_unique_id or email,
            },
        }
    }
    try:
        return await _request(ctx, "POST", "/users", json_body=payload)
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def update_user(user_id: str, attributes: dict, ctx: Context) -> Any:
    """
    Update fields on an existing learner (e.g. profile, fields, roles).

    Args:
        user_id: The ALM user ID to update.
        attributes: Dict of attribute name -> new value, matching ALM's
            user attribute schema (e.g. {"bio": "...", "profile": "Engineer"}).
    """
    payload = {"data": {"id": user_id, "type": "user", "attributes": attributes}}
    try:
        return await _request(ctx, "PATCH", f"/users/{user_id}", json_body=payload)
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def delete_user(user_id: str, ctx: Context) -> Any:
    """
    Delete a learner from ALM. Destructive — confirm the ID before calling.

    Args:
        user_id: The ALM user ID to delete.
    """
    try:
        return await _request(ctx, "DELETE", f"/users/{user_id}")
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def list_user_groups(user_id: str, ctx: Context, page_limit: int = 20) -> Any:
    """
    List the user groups a learner belongs to.

    Args:
        user_id: The learner's ALM user ID.
        page_limit: Max number of groups to return.
    """
    try:
        return await _request(
            ctx, "GET", f"/users/{user_id}/userGroups", params={"page[limit]": page_limit}
        )
    except RuntimeError as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Learning objects & catalogs
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_learning_objects(
    ctx: Context,
    lo_types: Optional[str] = None,
    page_limit: int = 20,
    page_cursor: Optional[str] = None,
) -> Any:
    """
    List learning objects (courses, learning programs, certifications, job aids).

    ALM caps page_limit at 100 and paginates further results via a cursor.
    Call this once, and if the response's "links" object has a "next" URL,
    extract the page[cursor] value from it and pass it back in as
    page_cursor to get the next page. Repeat until "next" is absent to
    walk the full list.

    Args:
        lo_types: Optional comma-separated filter, e.g. "course,jobAid".
        page_limit: Max results to return per page (ALM max: 100).
        page_cursor: Cursor value from a previous response's links.next
            URL, used to fetch the next page. Omit for the first page.
    """
    params = {"page[limit]": page_limit}
    if lo_types:
        params["filter.loTypes"] = lo_types
    if page_cursor:
        params["page[cursor]"] = page_cursor
    try:
        return await _request(ctx, "GET", "/learningObjects", params=params)
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def get_learning_object(lo_id: str, ctx: Context, include: Optional[str] = None) -> Any:
    """
    Get details for a single learning object (course, learning program, etc.).

    Args:
        lo_id: The learning object ID, e.g. "course:9756365".
        include: Optional comma-separated related resources to include,
            e.g. "instances,skills" or "instances.loResources.resources"
            (the nested path needed to get actual session date/time,
            instructor, and meeting link for Virtual Classroom courses —
            shallower includes only return instance metadata).
    """
    params = {"include": include} if include else None
    try:
        return await _request(ctx, "GET", f"/learningObjects/{lo_id}", params=params)
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def list_catalogs(ctx: Context, page_limit: int = 20) -> Any:
    """
    List catalogs (curated collections of learning objects) in the account.

    Args:
        page_limit: Max results to return.
    """
    try:
        return await _request(ctx, "GET", "/catalogs", params={"page[limit]": page_limit})
    except RuntimeError as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Enrollments
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_enrollments(
    user_id: str, ctx: Context, lo_types: Optional[str] = None, sort: Optional[str] = None
) -> Any:
    """
    List a user's enrollments (courses, learning programs, certifications, job aids).

    Args:
        user_id: The learner's ALM user ID.
        lo_types: Optional filter, e.g. "course" or "learningProgram".
        sort: Optional sort field, e.g. "dateEnrolled".
    """
    params = {}
    if lo_types:
        params["filter.loTypes"] = lo_types
    if sort:
        params["sort"] = sort
    try:
        return await _request(ctx, "GET", f"/users/{user_id}/enrollments", params=params)
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def enroll_user(
    user_id: str,
    lo_id: str,
    lo_instance_id: str,
    ctx: Context,
    allow_multi_enrollment: bool = False,
) -> Any:
    """
    Enroll a learner into a learning object instance (e.g. a specific
    course run).

    Args:
        user_id: The learner's ALM user ID.
        lo_id: Learning object ID, e.g. "course:14995353".
        lo_instance_id: Specific instance ID, e.g. "course:14995353_15917625".
        allow_multi_enrollment: Whether to allow enrolling again if
            already enrolled in another instance of the same LO.
    """
    params = {
        "loId": lo_id,
        "loInstanceId": lo_instance_id,
        "allowMultiEnrollment": str(allow_multi_enrollment).lower(),
    }
    try:
        return await _request(ctx, "POST", f"/users/{user_id}/enrollments", params=params)
    except RuntimeError as e:
        # Note: ALM can return "object doesn't exist" briefly after user
        # creation due to eventual-consistency indexing delay. If you're
        # enrolling immediately after create_user, consider a short retry.
        return {"error": str(e)}


@mcp.tool()
async def get_enrollment(
    user_id: str, enrollment_id: str, ctx: Context, include: Optional[str] = None
) -> Any:
    """
    Get progress/status details for a specific enrollment.

    Args:
        user_id: The learner's ALM user ID.
        enrollment_id: The enrollment ID (e.g. returned from list_enrollments).
        include: Optional related data to include, e.g.
            "learningObject,loInstance,loResourceGrades".
    """
    params = {"include": include} if include else None
    try:
        return await _request(
            ctx, "GET", f"/users/{user_id}/enrollments/{enrollment_id}", params=params
        )
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def unenroll_user(user_id: str, enrollment_id: str, ctx: Context) -> Any:
    """
    Unenroll a learner from a course, certification, learning program, or job aid.

    Note: this can return an ERROR_ENROLLMENT_NOT_FOUND error even when
    the removal actually happened — this has been observed near a
    completion/enrollment deadline boundary. Don't trust the error
    message alone; call list_enrollments afterward to check the real
    current state before concluding it failed.

    Args:
        user_id: The learner's ALM user ID.
        enrollment_id: The enrollment ID to cancel.
    """
    try:
        return await _request(ctx, "DELETE", f"/users/{user_id}/enrollments/{enrollment_id}")
    except RuntimeError as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_user_badges(user_id: str, ctx: Context) -> Any:
    """
    List badges earned by a learner.

    Args:
        user_id: The learner's ALM user ID.
    """
    try:
        return await _request(ctx, "GET", f"/users/{user_id}/badges")
    except RuntimeError as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Jobs (async operations)
# ---------------------------------------------------------------------------

@mcp.tool()
async def create_job(job_type: str, ctx: Context, job_params: Optional[dict] = None) -> Any:
    """
    Create an async ALM job, e.g. bulk user export to CSV or certificate
    PDF generation. Returns a job ID to poll with get_job_status.

    Args:
        job_type: The ALM job type string (see ALM job API docs for
            valid values, e.g. "userExport", "certificateGeneration").
        job_params: Additional job-specific parameters, if required.
    """
    payload = {
        "data": {
            "type": "job",
            "attributes": {"jobType": job_type, **(job_params or {})},
        }
    }
    try:
        return await _request(ctx, "POST", "/jobs", json_body=payload)
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def get_job_status(job_id: str, ctx: Context) -> Any:
    """
    Check the status/result of a previously created async job.

    Args:
        job_id: The job ID returned by create_job.
    """
    try:
        return await _request(ctx, "GET", f"/jobs/{job_id}")
    except RuntimeError as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Remote hosting (e.g. Render) requires Streamable HTTP, not stdio —
    # stdio only works when the client launches this as a local subprocess.
    # host/port are set on the FastMCP constructor above (official mcp SDK
    # reads them from there, NOT from run() — unlike the third-party
    # `fastmcp` package, which does accept host/port on run()).
    mcp.run(transport="streamable-http")
