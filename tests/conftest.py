"""Pytest fixtures and configuration for integration tests."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from opencode_client import OpenCodeClient
from session_manager import SessionManager
from pty_manager import PtyManager


@pytest.fixture
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_opencode_client():
    """Create a fresh mock OpenCode client for each test."""
    client = MagicMock(spec=OpenCodeClient)
    client.list_sessions = AsyncMock(return_value=[])
    client.get_session = AsyncMock(
        return_value={
            "id": "test-session-1",
            "title": "Test Session",
            "directory": "/tmp",
            "messages": [],
            "time": {"created": 1234567890, "updated": 1234567890},
        }
    )
    client.create_session = AsyncMock(
        return_value={"id": "new-session-1", "title": "New Session"}
    )
    client.delete_session = AsyncMock(return_value={"success": True})
    client.send_message = AsyncMock(return_value={"response": "test response"})
    client.prompt_async = AsyncMock(return_value={"accepted": True, "status_code": 204})
    client.stream_message = MagicMock(return_value=iter([]))
    client.abort_message = AsyncMock(return_value={"aborted": True})
    client.fork_session = AsyncMock(return_value={"id": "forked-session-1"})
    client.create_pty = AsyncMock(return_value={"id": "pty-1"})
    client.list_ptys = AsyncMock(return_value=[{"id": "pty-1", "status": "running"}])
    client.get_pty = AsyncMock(return_value={"id": "pty-1", "status": "running"})
    client.update_pty = AsyncMock(return_value={"id": "pty-1", "status": "running"})
    client.write_pty = AsyncMock(return_value={"success": True})
    client.resize_pty = AsyncMock(return_value={"success": True})
    client.get_pty_output = AsyncMock(return_value={"data": "test output"})
    client.close_pty = AsyncMock(return_value={"success": True})
    client.run_shell = AsyncMock(
        return_value={
            "info": {"id": "msg-1", "time": {"completed": 1234567900}},
            "parts": [
                {
                    "type": "tool",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": "echo ok"},
                        "output": "ok\n",
                        "metadata": {"output": "ok\n"},
                    },
                }
            ],
        }
    )
    client.list_questions = AsyncMock(return_value=[])
    client.reply_question = AsyncMock(
        return_value={"success": True, "answered": True, "request_id": "q1"}
    )
    client.reject_question = AsyncMock(
        return_value={"success": True, "rejected": True, "request_id": "q1"}
    )
    client.list_permissions = AsyncMock(return_value=[])
    client.reply_permission = AsyncMock(
        return_value={
            "success": True,
            "replied": True,
            "request_id": "p1",
            "reply": "once",
        }
    )
    client.update_session = AsyncMock(return_value={"success": True})
    client.list_messages = AsyncMock(return_value=[])
    client.get_session_status = AsyncMock(return_value={})
    return client


@pytest.fixture
def session_manager(mock_opencode_client):
    """Create a SessionManager with mock client."""
    return SessionManager(mock_opencode_client)


@pytest.fixture
def pty_manager(mock_opencode_client):
    """Create a PtyManager with mock client."""
    return PtyManager(mock_opencode_client)
