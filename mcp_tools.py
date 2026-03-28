import json
import asyncio
from typing import Any, Optional, Callable, Awaitable
import structlog
from mcp.types import Tool, TextContent
from session_manager import SessionManager
from pty_manager import PtyManager

logger = structlog.get_logger()

ToolFunc = Callable[[], Awaitable[list[TextContent]]]


class MCPToolRegistry:
    """Registry that maps tool names to handler functions."""

    def __init__(self):
        self._tools: list[Tool] = []
        self._handlers: dict[str, Callable[..., Awaitable[list[TextContent]]]] = {}

    def tool(self, name: str, description: str, input_schema: dict):
        """Decorator to register a tool."""
        def decorator(func: Callable[..., Awaitable[list[TextContent]]]):
            self._tools.append(Tool(
                name=name,
                description=description,
                inputSchema=input_schema
            ))
            self._handlers[name] = func
            return func
        return decorator

    def get_tools(self) -> list[Tool]:
        return self._tools

    async def call(self, name: str, arguments: dict) -> list[TextContent]:
        if name not in self._handlers:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        try:
            return await self._handlers[name](**arguments)
        except Exception as e:
            logger.error("tool_call_error", tool=name, error=str(e))
            return [TextContent(type="text", text=f"Error: {str(e)}")]


def create_mcp_tools(session_mgr: SessionManager, pty_mgr: PtyManager) -> MCPToolRegistry:
    """Create all MCP tools and return a registry."""

    registry = MCPToolRegistry()

    @registry.tool(
        name="session_list",
        description="List all OpenCode sessions. Returns session IDs, titles, and owners (user/claude).",
        input_schema={"type": "object", "properties": {}}
    )
    async def tool_session_list() -> list[TextContent]:
        try:
            sessions = await session_mgr.list_sessions()
            return [TextContent(type="text", text=json.dumps({"sessions": sessions}, indent=2))]
        except Exception as e:
            logger.error("session_list_error", error=str(e))
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    @registry.tool(
        name="session_create",
        description="Create a new OpenCode session for Claude to use for coding tasks.",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Optional session title"},
                "directory": {"type": "string", "description": "Optional working directory"}
            }
        }
    )
    async def tool_session_create(title: Optional[str] = None, directory: Optional[str] = None) -> list[TextContent]:
        try:
            result = await session_mgr.create_session(title=title, directory=directory)
            return [TextContent(type="text", text=json.dumps({"session": result}, indent=2))]
        except Exception as e:
            logger.error("session_create_error", error=str(e))
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    @registry.tool(
        name="session_get",
        description="Get full details of a specific session including messages and context.",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "The session ID to retrieve"}
            },
            "required": ["session_id"]
        }
    )
    async def tool_session_get(session_id: str) -> list[TextContent]:
        try:
            result = await session_mgr.get_session(session_id)
            return [TextContent(type="text", text=json.dumps({"session": result}, indent=2))]
        except Exception as e:
            logger.error("session_get_error", session_id=session_id, error=str(e))
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    @registry.tool(
        name="session_delete",
        description="Delete a session owned by Claude.",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "The session ID to delete"}
            },
            "required": ["session_id"]
        }
    )
    async def tool_session_delete(session_id: str) -> list[TextContent]:
        try:
            result = await session_mgr.delete_session(session_id)
            return [TextContent(type="text", text=json.dumps({"deleted": result}, indent=2))]
        except Exception as e:
            logger.error("session_delete_error", session_id=session_id, error=str(e))
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    @registry.tool(
        name="session_fork",
        description="Fork an existing session to create a new branch.",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "The session ID to fork"}
            },
            "required": ["session_id"]
        }
    )
    async def tool_session_fork(session_id: str) -> list[TextContent]:
        try:
            result = await session_mgr.fork_session(session_id)
            return [TextContent(type="text", text=json.dumps({"forked_session": result}, indent=2))]
        except Exception as e:
            logger.error("session_fork_error", session_id=session_id, error=str(e))
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    @registry.tool(
        name="message_send",
        description="Send a prompt to an OpenCode session to steer the agent. This is how Claude directs the OpenCode agent to perform tasks.",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "The session ID to send the prompt to"},
                "prompt": {"type": "string", "description": "The instruction/prompt for the OpenCode agent"}
            },
            "required": ["session_id", "prompt"]
        }
    )
    async def tool_message_send(session_id: str, prompt: str) -> list[TextContent]:
        try:
            response_parts = []
            async for event in await session_mgr.send_message(session_id, prompt):
                response_parts.append(event)
            full_response = "\n".join(json.dumps(p) for p in response_parts)
            return [TextContent(type="text", text=full_response)]
        except Exception as e:
            logger.error("message_send_error", session_id=session_id, error=str(e))
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    @registry.tool(
        name="message_abort",
        description="Abort ongoing generation in a session.",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "The session ID to abort"}
            },
            "required": ["session_id"]
        }
    )
    async def tool_message_abort(session_id: str) -> list[TextContent]:
        try:
            result = await session_mgr.abort_message(session_id)
            return [TextContent(type="text", text=json.dumps({"aborted": result}, indent=2))]
        except Exception as e:
            logger.error("message_abort_error", session_id=session_id, error=str(e))
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    @registry.tool(
        name="bash_create",
        description="Create a new PTY (bash terminal) for Claude's direct command execution.",
        input_schema={
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "Optional working directory for the terminal"}
            }
        }
    )
    async def tool_bash_create(cwd: Optional[str] = None) -> list[TextContent]:
        try:
            result = await pty_mgr.create_pty(cwd=cwd, owner="claude")
            return [TextContent(type="text", text=json.dumps({"pty": result}, indent=2))]
        except Exception as e:
            logger.error("bash_create_error", error=str(e))
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    @registry.tool(
        name="bash_read",
        description="Read current output from Claude's PTY terminal.",
        input_schema={
            "type": "object",
            "properties": {
                "pty_id": {"type": "string", "description": "The PTY ID to read from"}
            },
            "required": ["pty_id"]
        }
    )
    async def tool_bash_read(pty_id: str) -> list[TextContent]:
        try:
            output = await pty_mgr.read_output(pty_id)
            return [TextContent(type="text", text=output)]
        except Exception as e:
            logger.error("bash_read_error", pty_id=pty_id, error=str(e))
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    @registry.tool(
        name="bash_resize",
        description="Resize the PTY terminal.",
        input_schema={
            "type": "object",
            "properties": {
                "pty_id": {"type": "string", "description": "The PTY ID to resize"},
                "cols": {"type": "integer", "description": "Number of columns"},
                "rows": {"type": "integer", "description": "Number of rows"}
            },
            "required": ["pty_id", "cols", "rows"]
        }
    )
    async def tool_bash_resize(pty_id: str, cols: int, rows: int) -> list[TextContent]:
        try:
            result = await pty_mgr.resize_pty(pty_id, cols, rows)
            return [TextContent(type="text", text=json.dumps({"resized": result}, indent=2))]
        except Exception as e:
            logger.error("bash_resize_error", pty_id=pty_id, error=str(e))
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    @registry.tool(
        name="bash_close",
        description="Close Claude's PTY terminal.",
        input_schema={
            "type": "object",
            "properties": {
                "pty_id": {"type": "string", "description": "The PTY ID to close"}
            },
            "required": ["pty_id"]
        }
    )
    async def tool_bash_close(pty_id: str) -> list[TextContent]:
        try:
            result = await pty_mgr.close_pty(pty_id)
            return [TextContent(type="text", text=json.dumps({"closed": result}, indent=2))]
        except Exception as e:
            logger.error("bash_close_error", pty_id=pty_id, error=str(e))
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    @registry.tool(
        name="status",
        description="Get gateway health status and statistics.",
        input_schema={"type": "object", "properties": {}}
    )
    async def tool_status() -> list[TextContent]:
        try:
            sessions = session_mgr.get_all_session_ids()
            ptys = pty_mgr.get_claude_ptys()
            return [TextContent(type="text", text=json.dumps({
                "status": "healthy",
                "total_sessions": len(sessions),
                "claude_sessions": len(session_mgr.get_claude_session_ids()),
                "claude_ptys": ptys,
            }, indent=2))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    return registry


async def call_tool(name: str, arguments: dict, session_mgr: SessionManager, pty_mgr: PtyManager) -> list[TextContent]:
    """Call a tool by name with arguments using a fresh registry."""
    registry = create_mcp_tools(session_mgr, pty_mgr)
    return await registry.call(name, arguments)