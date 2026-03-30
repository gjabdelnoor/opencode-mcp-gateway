#!/usr/bin/env python3
"""
OpenCode MCP Gateway - Exposes OpenCode as a remote MCP server for Claude Code.
Uses FastMCP with streamable-http transport.
"""

import os
import asyncio
import secrets
import hashlib
import base64
from pathlib import Path
from typing import Optional, cast

from dotenv import load_dotenv
import structlog
from fastmcp import FastMCP
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse, RedirectResponse
from starlette.routing import Route
import uvicorn

from opencode_client import OpenCodeClient
from session_manager import SessionManager
from pty_manager import PtyManager

dotenv_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path)

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger()

AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", secrets.token_hex(32))
MCP_CLIENT_ID = os.environ.get("MCP_CLIENT_ID", "opencode-mcp-gateway")
MCP_ALLOWED_CLIENT_IDS = {
    value.strip()
    for value in os.environ.get("MCP_ALLOWED_CLIENT_IDS", "").split(",")
    if value.strip()
}
MCP_ALLOWED_CLIENT_IDS.add(MCP_CLIENT_ID)
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

OPENCODE_HOST = os.environ.get("OPENCODE_HOST", "localhost")
OPENCODE_PORT = int(os.environ.get("OPENCODE_PORT", "9999"))
GATEWAY_PORT = int(os.environ.get("GATEWAY_PORT", "3001"))
ENABLE_RAW_BASH = os.environ.get("ENABLE_RAW_BASH", "true").lower() != "false"

SERVER_NAME = "opencode-mcp-gateway"

oc_client = cast(OpenCodeClient, None)
session_mgr = cast(SessionManager, None)
pty_mgr = cast(PtyManager, None)

auth_codes = {}


def _resolve_base_url(request: Request) -> str:
    """Resolve externally visible base URL for OAuth metadata.

    If PUBLIC_BASE_URL is provided, use it as a stable override (useful when
    reverse-proxying this gateway behind a path prefix like /desktop).
    Otherwise derive from forwarded headers/host.
    """
    if PUBLIC_BASE_URL:
        base_url = PUBLIC_BASE_URL
    else:
        forwarded_proto = (
            request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
        )
        scheme = forwarded_proto or request.url.scheme
        forwarded_host = (
            request.headers.get("x-forwarded-host", "").split(",")[0].strip()
        )
        host = forwarded_host or request.headers.get("host") or request.url.netloc
        base_url = f"{scheme}://{host}".rstrip("/")

    if request.url.path.endswith("/mcp") and not base_url.endswith("/mcp"):
        return f"{base_url}/mcp"
    return base_url


def _resolve_resource_base(request: Request) -> str:
    base_url = _resolve_base_url(request)
    if base_url.endswith("/mcp"):
        return base_url[:-4]
    return base_url


def _is_allowed_client_id(client_id: str) -> bool:
    return any(
        secrets.compare_digest(client_id, allowed) for allowed in MCP_ALLOWED_CLIENT_IDS
    )


def create_fastmcp() -> FastMCP:
    """Create and configure the FastMCP server."""
    global oc_client, session_mgr, pty_mgr

    mcp = FastMCP(
        name=SERVER_NAME,
        instructions="OpenCode MCP Gateway - Access OpenCode sessions and tools",
    )

    @mcp.tool()
    async def list_sessions(cursor: Optional[str] = None, limit: int = 10) -> dict:
        """List OpenCode sessions with pagination.

        Returns all known sessions (up to limit) with recent message previews.
        Use cursor from previous response to get next page.

        Args:
            cursor: Cursor from previous response to get next page (optional)
            limit: Maximum sessions to return (default 10, max 50)
        """
        limit = min(limit, 50)
        return await session_mgr.list_sessions(cursor=cursor, limit=limit)

    @mcp.tool()
    async def session_create(
        initial_message: str,
        title: Optional[str] = None,
        directory: Optional[str] = None,
        mode: str = "planning",
        auto_accept: bool = False,
    ) -> dict:
        """Create a new OpenCode session with mandatory initial message.

        Creates a new session in planning mode by default, sends the initial message,
        and returns the response. The session will be in planning mode until you
        explicitly switch it to building mode.

        Args:
            initial_message: REQUIRED - First message to send to the session
            title: Optional session title
            directory: Optional working directory
            mode: 'planning' (default) or 'building' - mode for the new session
            auto_accept: If True, sets permissions to allow all (no permission prompts)
        """
        permissions = None
        if auto_accept:
            permissions = [{"permission": "*", "pattern": "*", "action": "allow"}]
        return await session_mgr.create_session(
            initial_message=initial_message,
            title=title,
            directory=directory,
            mode=mode,
            permissions=permissions,
        )

    @mcp.tool()
    async def session_get(session_id: str) -> dict:
        """Get session details."""
        return await session_mgr.get_session(session_id)

    @mcp.tool()
    async def session_delete(session_id: str) -> dict:
        """Delete a session."""
        return await session_mgr.delete_session(session_id)

    @mcp.tool()
    async def session_fork(session_id: str) -> dict:
        """Fork a session."""
        return await session_mgr.fork_session(session_id)

    @mcp.tool()
    async def send_message(session_id: str, prompt: str) -> dict:
        """Send a message to a session with timeout handling.

        Sends the message and waits for OpenCode's response. If the response
        takes too long (approaching 50s timeout), returns partial result with
        still_active=True. Use read_session_logs to get full output.

        Args:
            session_id: The session ID to send the message to
            prompt: The message text
        """
        return await session_mgr.send_message(session_id, prompt)

    @mcp.tool()
    async def message_abort(session_id: str) -> dict:
        """Abort ongoing generation."""
        return await session_mgr.abort_message(session_id)

    @mcp.tool()
    async def bash_create(
        cwd: Optional[str] = None,
        command: Optional[str] = None,
        args: Optional[list[str]] = None,
        title: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
    ) -> dict:
        """Create a PTY terminal.

        Args:
            cwd: Working directory for the terminal
            command: Optional executable (default /bin/bash)
            args: Optional command arguments
            title: Optional terminal title
            env: Optional environment variable overrides
        """
        return await pty_mgr.create_pty(
            cwd=cwd,
            owner="claude",
            command=command,
            args=args,
            title=title,
            env=env,
        )

    @mcp.tool()
    async def bash_list() -> dict:
        """List all PTY sessions known by OpenCode."""
        ptys = await pty_mgr.list_remote_ptys()
        return {"ptys": ptys, "count": len(ptys)}

    @mcp.tool()
    async def bash_get(pty_id: str) -> dict:
        """Get PTY details by ID."""
        return await pty_mgr.get_pty(pty_id)

    @mcp.tool()
    async def bash_read(pty_id: str) -> str:
        """Read PTY output."""
        return await pty_mgr.read_output(pty_id)

    @mcp.tool()
    async def bash_resize(pty_id: str, cols: int, rows: int) -> dict:
        """Resize PTY."""
        return await pty_mgr.resize_pty(pty_id, cols, rows)

    @mcp.tool()
    async def bash_update(
        pty_id: str,
        title: Optional[str] = None,
        cols: Optional[int] = None,
        rows: Optional[int] = None,
    ) -> dict:
        """Update PTY metadata and/or terminal size."""
        return await pty_mgr.update_pty(
            pty_id=pty_id, title=title, cols=cols, rows=rows
        )

    @mcp.tool()
    async def bash_write(pty_id: str, data: str) -> dict:
        """Write raw input bytes/text to a PTY."""
        return await pty_mgr.send_input(pty_id, data)

    @mcp.tool()
    async def bash_close(pty_id: str) -> dict:
        """Close PTY."""
        return await pty_mgr.close_pty(pty_id)

    async def _run_raw_bash(
        command: str,
        timeout: int = 120,
        workdir: Optional[str] = None,
        description: str = "",
        session_id: Optional[str] = None,
    ) -> dict:
        if not ENABLE_RAW_BASH:
            return {
                "success": False,
                "error": "Raw bash execution is disabled. Set ENABLE_RAW_BASH=true to enable.",
            }

        return await session_mgr.run_shell_command(
            command=command,
            session_id=session_id,
            workdir=workdir,
            timeout_seconds=timeout,
            description=description,
        )

    @mcp.tool()
    async def bash(
        command: str,
        timeout: int = 120,
        workdir: Optional[str] = None,
        description: str = "",
        session_id: Optional[str] = None,
    ) -> dict:
        """Execute a raw shell command directly.

        This mirrors OpenCode's bash tool semantics for direct command execution.

        Args:
            command: Shell command to execute
            timeout: Max seconds to allow the command to run
            workdir: Optional working directory
            description: Optional operator note for traceability
            session_id: Optional explicit session ID
        """
        return await _run_raw_bash(
            command=command,
            timeout=timeout,
            workdir=workdir,
            description=description,
            session_id=session_id,
        )

    @mcp.tool()
    async def bash_exec(
        command: str,
        timeout: int = 120,
        workdir: Optional[str] = None,
        description: str = "",
        session_id: Optional[str] = None,
    ) -> dict:
        """Alias of bash() for direct command execution."""
        return await _run_raw_bash(
            command=command,
            timeout=timeout,
            workdir=workdir,
            description=description,
            session_id=session_id,
        )

    @mcp.tool()
    def status() -> dict:
        """Get gateway status."""
        sessions = session_mgr.get_all_session_ids()
        ptys = pty_mgr.get_claude_ptys()
        return {
            "status": "healthy",
            "total_sessions": len(sessions),
            "claude_sessions": len(session_mgr.get_claude_session_ids()),
            "claude_ptys": ptys,
            "raw_bash_enabled": ENABLE_RAW_BASH,
        }

    @mcp.tool()
    async def read_session_logs(session_id: str, mode: str = "summary") -> dict:
        """Read session logs (non-blocking).

        Read a session's message history without waiting. Use this to check
        session output divorced from wait_for_session monitoring.

        Args:
            session_id: The session ID
            mode: "summary" (last 3 messages, default) or "full" (all messages)
        """
        if mode not in ("summary", "full"):
            mode = "summary"
        return await session_mgr.read_session_logs(session_id, mode=mode)

    @mcp.tool()
    def switch_session(session_id: str) -> dict:
        """Switch to a different session."""
        return session_mgr.set_active_session(session_id)

    @mcp.tool()
    def switch_model(session_id: str, model: str) -> dict:
        """Set the model for a session (e.g., 'openai/gpt-4o', 'anthropic/claude-3-5-sonnet')."""
        return session_mgr.set_session_model(session_id, model)

    @mcp.tool()
    async def switch_mode_and_send(session_id: str, mode: str, message: str) -> dict:
        """Switch a session to a different mode AND send a message in one call.

        This is the primary way to transition from planning to building mode.
        Switching mode and sending a message together ensures the agent
        understands the context of why the mode changed.

        Args:
            session_id: The session ID
            mode: Target mode - 'planning' or 'building'
            message: Message to send after switching mode (e.g., "Now build this plan")
        """
        return await session_mgr.switch_mode_and_send(session_id, mode, message)

    @mcp.tool()
    def get_session_mode(session_id: str) -> dict:
        """Get the current mode of a session (planning or building)."""
        mode = session_mgr.get_session_mode(session_id)
        return {"session_id": session_id, "mode": mode}

    @mcp.tool()
    def get_active_session() -> dict:
        """Get the currently active session."""
        active_id = session_mgr.get_active_session()
        return {"active_session_id": active_id}

    @mcp.tool()
    async def set_permissions(session_id: str, permissions: list) -> dict:
        """Set permissions for a session.

        Args:
            session_id: The session ID
            permissions: List of permission dicts, e.g., [{"permission": "*", "pattern": "*", "action": "allow"}]
                       Valid actions: "allow", "deny", "ask"
        """
        return await session_mgr.set_session_permissions(session_id, permissions)

    @mcp.tool()
    async def auto_accept_permissions(session_id: str) -> dict:
        """Enable auto-accept (allow all) permissions for a session.

        This removes all permission prompts for the session.
        """
        permissions = [{"permission": "*", "pattern": "*", "action": "allow"}]
        return await session_mgr.set_session_permissions(session_id, permissions)

    @mcp.tool()
    async def question_list(session_id: Optional[str] = None) -> dict:
        """List pending interactive questions from OpenCode.

        Args:
            session_id: Optional filter to only show questions for one session.
        """
        return await session_mgr.list_pending_questions(session_id=session_id)

    @mcp.tool()
    async def question_reply(request_id: str, answers: list[list[str]]) -> dict:
        """Reply to a pending interactive question.

        Args:
            request_id: Question request ID from question_list
            answers: Answers for each question in order (each answer is list of selected labels)
        """
        return await session_mgr.answer_question(request_id=request_id, answers=answers)

    @mcp.tool()
    async def question_reject(request_id: str) -> dict:
        """Reject a pending interactive question request."""
        return await session_mgr.reject_question(request_id=request_id)

    @mcp.tool()
    async def permission_list(session_id: Optional[str] = None) -> dict:
        """List pending permission requests from OpenCode."""
        return await session_mgr.list_pending_permissions(session_id=session_id)

    @mcp.tool()
    async def permission_reply(
        request_id: str,
        reply: str,
        message: str = "",
    ) -> dict:
        """Reply to a pending permission request.

        Args:
            request_id: Permission request ID from permission_list
            reply: one of 'once', 'always', or 'reject'
            message: Optional explanation/feedback string
        """
        if reply not in ("once", "always", "reject"):
            return {
                "success": False,
                "error": "reply must be one of: once, always, reject",
                "request_id": request_id,
            }

        return await session_mgr.reply_permission(
            request_id=request_id,
            reply=reply,
            message=message,
        )

    @mcp.tool()
    async def wait_for_session(session_id: str, duration: int = 50) -> dict:
        """Wait for a session and collect activity.

        Monitors a session for the specified duration, collecting tool calls,
        outputs, and internal reasoning. Returns a summary of activity suitable
        for deciding if the agent needs steering/correction.

        Minimum duration is 30 seconds. If session still active near timeout,
        returns partial results with still_active=True and flavor text suggesting
        to use read_session_logs.

        Args:
            session_id: The session ID to monitor
            duration: Seconds to wait (minimum 30, default 50)
        """
        return await session_mgr.wait_for_session(session_id, duration)

    return mcp


async def handle_health(request: Request) -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse(
        {"status": "healthy", "server": SERVER_NAME, "auth_required": True}
    )


async def handle_oauth_authorize(request: Request):
    """OAuth authorization - auto-approves known clients and redirects back with code."""
    params = dict(request.query_params)
    client_id = params.get("client_id", "")
    redirect_uri = params.get("redirect_uri", "")
    state = params.get("state", "")
    code_challenge = params.get("code_challenge", "")
    code_challenge_method = params.get("code_challenge_method", "S256")
    scope = params.get("scope", "mcp")

    logger.info(
        "oauth_authorize_request",
        client_id=client_id,
        redirect_uri=redirect_uri,
        pkce_enabled=bool(code_challenge),
        code_challenge_method=code_challenge_method,
    )

    if not _is_allowed_client_id(client_id):
        logger.warning("oauth_authorize_invalid_client", client_id=client_id)
        return HTMLResponse("<h1>Invalid client_id</h1>", status_code=400)

    if not redirect_uri:
        logger.warning("oauth_authorize_missing_redirect_uri", client_id=client_id)
        return HTMLResponse("<h1>Missing redirect_uri</h1>", status_code=400)

    code = secrets.token_hex(32)
    auth_codes[code] = {
        "client_id": client_id,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "scope": scope,
        "expires": asyncio.get_event_loop().time() + 300,
    }

    import urllib.parse

    params_encoded = urllib.parse.urlencode({"code": code, "state": state})
    return RedirectResponse(f"{redirect_uri}?{params_encoded}", status_code=302)


async def handle_oauth_authorize_post(request: Request) -> RedirectResponse:
    """Handle authorization approval/denial."""
    form = await request.form()
    action = form.get("action", "")
    redirect_uri = form.get("redirect_uri", "")
    state = form.get("state", "")

    if action != "approve":
        import urllib.parse

        params = urllib.parse.urlencode({"error": "access_denied", "state": state})
        return RedirectResponse(f"{redirect_uri}?{params}", status_code=302)

    code = form.get("code", "")
    code_challenge = form.get("code_challenge", "")
    code_challenge_method = form.get("code_challenge_method", "S256")
    scope = form.get("scope", "mcp")

    auth_codes[code] = {
        "client_id": str(form.get("client_id", MCP_CLIENT_ID)),
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "scope": scope,
        "expires": asyncio.get_event_loop().time() + 300,
    }

    import urllib.parse

    params = urllib.parse.urlencode({"code": code, "state": state})
    return RedirectResponse(f"{redirect_uri}?{params}", status_code=302)


async def handle_oauth_token(request: Request) -> JSONResponse:
    """OAuth token endpoint."""
    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            body = await request.json()
        elif "application/x-www-form-urlencoded" in content_type:
            form = await request.form()
            body = {k: str(v) for k, v in form.items()}
        else:
            body = await request.json()

        def mask_sensitive(payload: dict) -> dict:
            masked = dict(payload)
            for key in ("client_secret", "code_verifier", "refresh_token", "assertion"):
                if key in masked and masked[key]:
                    masked[key] = "***redacted***"
            if masked.get("code"):
                masked["code"] = f"{str(masked['code'])[:8]}..."
            return masked

        def extract_client_credentials(payload: dict, headers: dict) -> tuple[str, str]:
            client_id = str(payload.get("client_id", ""))
            client_secret = str(payload.get("client_secret", ""))
            auth_header = headers.get("authorization", "")

            if auth_header.startswith("Basic "):
                try:
                    raw = base64.b64decode(auth_header[6:]).decode("utf-8")
                    basic_client_id, basic_client_secret = raw.split(":", 1)
                    if not client_id:
                        client_id = basic_client_id
                    if not client_secret:
                        client_secret = basic_client_secret
                except Exception:
                    logger.warning("oauth_invalid_basic_auth_header")

            return client_id, client_secret

        logger.info(
            "token_request",
            body=mask_sensitive(body),
            headers={
                "user-agent": request.headers.get("user-agent", ""),
                "content-type": request.headers.get("content-type", ""),
                "x-forwarded-for": request.headers.get("x-forwarded-for", ""),
            },
        )

        grant_type = str(body.get("grant_type", ""))
        code = str(body.get("code", ""))
        code_verifier = str(body.get("code_verifier", ""))
        client_id, client_secret = extract_client_credentials(
            body, dict(request.headers)
        )

        if grant_type == "authorization_code" and code:
            auth_info = auth_codes.pop(code, None)
            if not auth_info:
                logger.warning(
                    "oauth_invalid_grant_code_not_found",
                    client_id=client_id,
                    code_prefix=code[:8],
                )
                return JSONResponse({"error": "invalid_grant"}, status_code=400)

            if asyncio.get_event_loop().time() > auth_info["expires"]:
                logger.warning(
                    "oauth_code_expired",
                    client_id=client_id,
                    code_prefix=code[:8],
                )
                return JSONResponse({"error": "code_expired"}, status_code=400)

            auth_client_id = str(auth_info.get("client_id", ""))
            if (
                client_id
                and auth_client_id
                and not secrets.compare_digest(client_id, auth_client_id)
            ):
                logger.warning(
                    "oauth_client_id_mismatch",
                    client_id=client_id,
                    expected_client_id=auth_client_id,
                    code_prefix=code[:8],
                )
                return JSONResponse({"error": "invalid_client"}, status_code=401)

            code_challenge = str(auth_info.get("code_challenge", ""))
            if code_challenge:
                if not code_verifier:
                    logger.warning(
                        "oauth_missing_code_verifier",
                        client_id=client_id,
                        code_prefix=code[:8],
                    )
                    return JSONResponse({"error": "invalid_grant"}, status_code=400)

                if auth_info.get("code_challenge_method") == "S256":
                    verifier_hash = hashlib.sha256(code_verifier.encode()).digest()
                    expected_challenge = (
                        base64.urlsafe_b64encode(verifier_hash).decode().rstrip("=")
                    )
                else:
                    expected_challenge = code_verifier

                if not secrets.compare_digest(expected_challenge, code_challenge):
                    logger.warning(
                        "oauth_pkce_mismatch",
                        client_id=client_id,
                        code_prefix=code[:8],
                    )
                    return JSONResponse({"error": "invalid_grant"}, status_code=400)
            else:
                # Confidential client fallback for authorization_code flows without PKCE.
                if not (
                    _is_allowed_client_id(client_id)
                    and secrets.compare_digest(client_secret, AUTH_TOKEN)
                ):
                    logger.warning(
                        "oauth_invalid_client_for_auth_code",
                        client_id=client_id,
                        code_prefix=code[:8],
                    )
                    return JSONResponse({"error": "invalid_client"}, status_code=401)

            return JSONResponse(
                {
                    "access_token": AUTH_TOKEN,
                    "token_type": "Bearer",
                    "expires_in": 86400,
                }
            )

        if (
            grant_type == "client_credentials"
            and _is_allowed_client_id(client_id)
            and secrets.compare_digest(client_secret, AUTH_TOKEN)
        ):
            return JSONResponse(
                {
                    "access_token": AUTH_TOKEN,
                    "token_type": "Bearer",
                    "expires_in": 86400,
                }
            )

        if grant_type == "client_credentials":
            logger.warning("oauth_invalid_client_credentials", client_id=client_id)
            return JSONResponse({"error": "invalid_client"}, status_code=401)

        logger.warning(
            "oauth_unsupported_grant_type", grant_type=grant_type, client_id=client_id
        )
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    except Exception as e:
        logger.error("oauth_token_error", error=str(e), headers=dict(request.headers))
        return JSONResponse({"error": str(e)}, status_code=500)


class HTTPSRedirectMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                headers = MutableHeaders(scope=message)
                location = headers.get("location", "")
                if location.startswith("http://"):
                    headers["location"] = "https://" + location[7:]
            await send(message)

        await self.app(scope, receive, send_wrapper)


class BearerAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        oauth_paths = [
            "/authorize",
            "/oauth/",
            "/.well-known/",
            "/mcp/authorize",
            "/mcp/oauth/",
            "/mcp/.well-known/",
        ]
        open_paths = ["/health", "/mcp/health"]

        if any(path.startswith(p) for p in oauth_paths) or path in open_paths:
            await self.app(scope, receive, send)
            return

        if path.startswith("/mcp"):
            header_map = {
                k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in scope.get("headers", [])
            }
            auth_header = header_map.get("authorization", "")
            if not auth_header.startswith("Bearer "):
                response = JSONResponse(
                    {
                        "error": "invalid_token",
                        "error_description": "Missing or invalid Authorization header",
                    },
                    status_code=401,
                )
                await response(scope, receive, send)
                return

            token = auth_header[7:]
            if not secrets.compare_digest(token, AUTH_TOKEN):
                response = JSONResponse({"error": "invalid_token"}, status_code=401)
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


async def handle_oauth_discovery(request: Request) -> JSONResponse:
    """OAuth authorization server discovery endpoint."""
    base_url = _resolve_base_url(request)

    return JSONResponse(
        {
            "issuer": base_url,
            "authorization_endpoint": f"{base_url}/authorize",
            "token_endpoint": f"{base_url}/oauth/token",
            "token_endpoint_auth_methods_supported": [
                "client_secret_basic",
                "client_secret_post",
            ],
            "scopes_supported": ["mcp", "openid", "profile", "email"],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "client_credentials"],
            "mcp_client_id": MCP_CLIENT_ID,
        }
    )


async def handle_protected_resource(request: Request) -> JSONResponse:
    """Protected resource metadata endpoint (RFC 9728)."""
    resource_base = _resolve_resource_base(request)
    return JSONResponse(
        {
            "resource": resource_base,
            "authorization_servers": [resource_base],
            "scopes_supported": ["mcp", "openid", "profile", "email"],
            "bearer_methods_supported": ["header"],
        }
    )


def main():
    """Main entry point."""
    global oc_client, session_mgr, pty_mgr

    logger.info(
        "gateway_starting",
        opencode_host=OPENCODE_HOST,
        opencode_port=OPENCODE_PORT,
        gateway_port=GATEWAY_PORT,
    )

    oc_client = OpenCodeClient(base_url=f"http://{OPENCODE_HOST}:{OPENCODE_PORT}")
    session_mgr = SessionManager(oc_client)
    pty_mgr = PtyManager(oc_client)

    mcp = create_fastmcp()
    mcp_app = mcp.http_app(path="/mcp")

    mcp_app.add_middleware(HTTPSRedirectMiddleware)
    mcp_app.add_middleware(BearerAuthMiddleware)
    mcp_app.add_route("/health", handle_health, methods=["GET"])
    # Claude OAuth discovery
    mcp_app.add_route(
        "/.well-known/oauth-authorization-server",
        handle_oauth_discovery,
        methods=["GET"],
    )
    # ChatGPT OAuth discovery (with /mcp suffix)
    mcp_app.add_route(
        "/.well-known/oauth-authorization-server/mcp",
        handle_oauth_discovery,
        methods=["GET"],
    )
    # Protected resource metadata (RFC 9728)
    mcp_app.add_route(
        "/.well-known/oauth-protected-resource",
        handle_protected_resource,
        methods=["GET"],
    )
    mcp_app.add_route(
        "/.well-known/oauth-protected-resource/mcp",
        handle_protected_resource,
        methods=["GET"],
    )
    # Claude OAuth routes
    mcp_app.add_route("/authorize", handle_oauth_authorize, methods=["GET"])
    mcp_app.add_route("/oauth/authorize", handle_oauth_authorize, methods=["GET"])
    mcp_app.add_route("/oauth/authorize", handle_oauth_authorize_post, methods=["POST"])
    mcp_app.add_route("/oauth/token", handle_oauth_token, methods=["POST"])
    # ChatGPT OAuth routes (with /mcp prefix - same handlers)
    mcp_app.add_route("/mcp/authorize", handle_oauth_authorize, methods=["GET"])
    mcp_app.add_route("/mcp/oauth/authorize", handle_oauth_authorize, methods=["GET"])
    mcp_app.add_route(
        "/mcp/oauth/authorize", handle_oauth_authorize_post, methods=["POST"]
    )
    mcp_app.add_route("/mcp/oauth/token", handle_oauth_token, methods=["POST"])

    uvicorn.run(
        mcp_app, host="0.0.0.0", port=GATEWAY_PORT, log_level="info", access_log=False
    )


if __name__ == "__main__":
    main()
