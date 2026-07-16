"""
Adobe Learning Manager (ALM) MCP Server
========================================

Wraps the ALM Prime API v2 (https://learningmanager.adobe.com/docs/primeapi/v2/)
as an MCP server: user management, learning-object lookup, enrollments,
badges, and async jobs (e.g. certificate generation, bulk export).

AUTH
----
ALM's Prime API uses OAuth 2.0. This server expects you to already have
a valid access token (obtained via ALM's OAuth flow / your integration's
client credentials) and pass it in as ALM_ACCESS_TOKEN. Token refresh is
NOT handled here — for long-running use, wrap this with your own
refresh logic or re-issue the env var when the token expires.

Requests use ALM's JSON:API content type
(application/vnd.api+json) and the "Authorization: oauth <token>" header
format documented by ALM (not a standard Bearer header).

WHAT THIS COVERS
----------------
Users:
  - list_users, get_user, create_user, update_user, delete_user
Learning objects:
  - list_learning_objects, get_learning_object, list_catalogs
Enrollments:
  - list_enrollments, enroll_user, get_enrollment, unenroll_user
Badges:
  - list_user_badges
Jobs (async operations like CSV export / certificate PDFs):
  - create_job, get_job_status

NOTE ON SCOPE
-------------
ALM's write API is oriented around users and their relationship to
learning content (enroll/unenroll, progress) rather than authoring
course content itself — course/module creation typically happens in
the ALM UI, not via this API. This server reflects that: it's read-heavy
for learning objects, and read/write for users + enrollments.
"""

import os
import sys
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Use the region-appropriate host, e.g.:
#   https://learningmanager.adobe.com
#   https://learningmanagereu.adobe.com
BASE_URL = os.environ.get("ALM_BASE_URL", "https://learningmanager.adobe.com")
ACCESS_TOKEN = os.environ.get("ALM_ACCESS_TOKEN")

if not ACCESS_TOKEN:
    print(
        "ERROR: ALM_ACCESS_TOKEN environment variable is not set. "
        "Configure it in your MCP client config (see README.md).",
        file=sys.stderr,
    )

API_ROOT = "/primeapi/v2"
REQUEST_TIMEOUT = 30.0

JSON_API_CONTENT_TYPE = "application/vnd.api+json;charset=UTF-8"

mcp = FastMCP("adobe_learning_manager")


def _headers() -> dict:
    return {
        "Authorization": f"oauth {ACCESS_TOKEN}",
        "Accept": "application/vnd.api+json",
        "Content-Type": JSON_API_CONTENT_TYPE,
    }


@mcp.tool()
async def set_access_token(new_token: str) -> Any:
    """
    Update the ALM OAuth access token in memory, without restarting the
    MCP server or the client. Use this when a token expires (401
    "Token expired") or you've regenerated one with new scopes — the
    very next tool call will use the new token.

    Note: this only updates the running server's memory. It does NOT
    update ALM_ACCESS_TOKEN in claude_desktop_config.json, so the next
    time the client restarts the server (e.g. app relaunch), it will
    go back to reading the old value from the config file. Update the
    config file too if the new token should persist across restarts.

    Args:
        new_token: The new OAuth access token string (just the raw
            token — do not include the "oauth " prefix).
    """
    global ACCESS_TOKEN
    ACCESS_TOKEN = new_token.strip()
    return {
        "status": "updated",
        "note": "New token is now active for subsequent calls. Remember to also update ALM_ACCESS_TOKEN in your MCP client config if you want this to survive a restart.",
    }


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

async def _request(
    method: str,
    path: str,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
) -> Any:
    url = f"{BASE_URL.rstrip('/')}{API_ROOT}{path}"
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            resp = await client.request(
                method, url, headers=_headers(), params=params, json=json_body
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
        return await _request("GET", "/users", params=params)
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def get_user(user_id: str) -> Any:
    """
    Get a single learner's profile by ALM user ID.

    Args:
        user_id: The ALM numeric/string user ID.
    """
    try:
        return await _request("GET", f"/users/{user_id}")
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def create_user(
    name: str,
    email: str,
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
        return await _request("POST", "/users", json_body=payload)
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def update_user(user_id: str, attributes: dict) -> Any:
    """
    Update fields on an existing learner (e.g. profile, fields, roles).

    Args:
        user_id: The ALM user ID to update.
        attributes: Dict of attribute name -> new value, matching ALM's
            user attribute schema (e.g. {"bio": "...", "profile": "Engineer"}).
    """
    payload = {"data": {"id": user_id, "type": "user", "attributes": attributes}}
    try:
        return await _request("PATCH", f"/users/{user_id}", json_body=payload)
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def delete_user(user_id: str) -> Any:
    """
    Delete a learner from ALM. Destructive — confirm the ID before calling.

    Args:
        user_id: The ALM user ID to delete.
    """
    try:
        return await _request("DELETE", f"/users/{user_id}")
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def list_user_groups(user_id: str, page_limit: int = 20) -> Any:
    """
    List the user groups a learner belongs to.

    Args:
        user_id: The learner's ALM user ID.
        page_limit: Max number of groups to return.
    """
    try:
        return await _request(
            "GET", f"/users/{user_id}/userGroups", params={"page[limit]": page_limit}
        )
    except RuntimeError as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Learning objects & catalogs
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_learning_objects(
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
        return await _request("GET", "/learningObjects", params=params)
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def get_learning_object(lo_id: str, include: Optional[str] = None) -> Any:
    """
    Get details for a single learning object (course, learning program, etc.).

    Args:
        lo_id: The learning object ID, e.g. "course:9756365".
        include: Optional comma-separated related resources to include,
            e.g. "instances,skills" or "modules".
    """
    params = {"include": include} if include else None
    try:
        return await _request("GET", f"/learningObjects/{lo_id}", params=params)
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def list_catalogs(page_limit: int = 20) -> Any:
    """
    List catalogs (curated collections of learning objects) in the account.

    Args:
        page_limit: Max results to return.
    """
    try:
        return await _request("GET", "/catalogs", params={"page[limit]": page_limit})
    except RuntimeError as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Enrollments
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_enrollments(
    user_id: str, lo_types: Optional[str] = None, sort: Optional[str] = None
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
        return await _request("GET", f"/users/{user_id}/enrollments", params=params)
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def enroll_user(
    user_id: str, lo_id: str, lo_instance_id: str, allow_multi_enrollment: bool = False
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
        return await _request("POST", f"/users/{user_id}/enrollments", params=params)
    except RuntimeError as e:
        # Note: ALM can return "object doesn't exist" briefly after user
        # creation due to eventual-consistency indexing delay. If you're
        # enrolling immediately after create_user, consider a short retry.
        return {"error": str(e)}


@mcp.tool()
async def get_enrollment(user_id: str, enrollment_id: str, include: Optional[str] = None) -> Any:
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
            "GET", f"/users/{user_id}/enrollments/{enrollment_id}", params=params
        )
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def unenroll_user(user_id: str, enrollment_id: str) -> Any:
    """
    Unenroll a learner from a course, certification, learning program, or job aid.

    Args:
        user_id: The learner's ALM user ID.
        enrollment_id: The enrollment ID to cancel.
    """
    try:
        return await _request("DELETE", f"/users/{user_id}/enrollments/{enrollment_id}")
    except RuntimeError as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_user_badges(user_id: str) -> Any:
    """
    List badges earned by a learner.

    Args:
        user_id: The learner's ALM user ID.
    """
    try:
        return await _request("GET", f"/users/{user_id}/badges")
    except RuntimeError as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Jobs (async operations)
# ---------------------------------------------------------------------------

@mcp.tool()
async def create_job(job_type: str, job_params: Optional[dict] = None) -> Any:
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
        return await _request("POST", "/jobs", json_body=payload)
    except RuntimeError as e:
        return {"error": str(e)}


@mcp.tool()
async def get_job_status(job_id: str) -> Any:
    """
    Check the status/result of a previously created async job.

    Args:
        job_id: The job ID returned by create_job.
    """
    try:
        return await _request("GET", f"/jobs/{job_id}")
    except RuntimeError as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Remote hosting (e.g. Render) requires Streamable HTTP, not stdio —
    # stdio only works when the client launches this as a local subprocess.
    # Render sets $PORT; default to 8000 for local testing.
    import os as _os
    port = int(_os.environ.get("PORT", 8000))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
