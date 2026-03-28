"""Tests for SessionManager."""

import pytest
from unittest.mock import AsyncMock, patch


class TestSessionManager:
    """Test cases for SessionManager."""

    @pytest.mark.asyncio
    async def test_create_session(self, session_manager, mock_opencode_client):
        """Test creating a new session."""
        result = await session_manager.create_session(title="Test Session", directory="/tmp")
        
        assert result["id"] == "new-session-1"
        mock_opencode_client.create_session.assert_called_once_with(
            title="Test Session", directory="/tmp"
        )

    @pytest.mark.asyncio
    async def test_create_session_with_defaults(self, session_manager, mock_opencode_client):
        """Test creating a session with default values."""
        await session_manager.create_session()
        
        mock_opencode_client.create_session.assert_called_once_with(
            title=None, directory=None
        )

    @pytest.mark.asyncio
    async def test_delete_session(self, session_manager, mock_opencode_client):
        """Test deleting a session."""
        await session_manager.create_session(owner="claude")
        result = await session_manager.delete_session("new-session-1")
        
        assert result["success"] is True
        mock_opencode_client.delete_session.assert_called_once_with("new-session-1")

    @pytest.mark.asyncio
    async def test_fork_session(self, session_manager, mock_opencode_client):
        """Test forking a session."""
        result = await session_manager.fork_session("test-session-1")
        
        assert result["id"] == "forked-session-1"
        mock_opencode_client.fork_session.assert_called_once_with("test-session-1")

    @pytest.mark.asyncio
    async def test_get_session(self, session_manager, mock_opencode_client):
        """Test getting session details."""
        result = await session_manager.get_session("test-session-1")
        
        assert result["id"] == "test-session-1"
        mock_opencode_client.get_session.assert_called_once_with("test-session-1")

    @pytest.mark.asyncio
    async def test_abort_message(self, session_manager, mock_opencode_client):
        """Test aborting a message."""
        result = await session_manager.abort_message("test-session-1")
        
        assert result["aborted"] is True
        mock_opencode_client.abort_message.assert_called_once_with("test-session-1")

    @pytest.mark.asyncio
    async def test_set_active_session(self, session_manager, mock_opencode_client):
        """Test setting the active session."""
        await session_manager.create_session(owner="claude")
        
        result = session_manager.set_active_session("new-session-1")
        
        assert result["success"] is True
        assert session_manager.get_active_session() == "new-session-1"

    @pytest.mark.asyncio
    async def test_set_active_session_not_found(self, session_manager, mock_opencode_client):
        """Test setting active session with non-existent ID."""
        result = session_manager.set_active_session("non-existent-session")
        
        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_set_session_model(self, session_manager, mock_opencode_client):
        """Test setting session model."""
        result = session_manager.set_session_model("test-session-1", "anthropic/claude-3-5-sonnet")
        
        assert result["success"] is True
        assert session_manager.get_session_model("test-session-1") == "anthropic/claude-3-5-sonnet"

    @pytest.mark.asyncio
    async def test_get_session_model_not_set(self, session_manager, mock_opencode_client):
        """Test getting model for session that doesn't have one set."""
        assert session_manager.get_session_model("test-session-1") is None

    @pytest.mark.asyncio
    async def test_send_message_with_model(self, session_manager, mock_opencode_client):
        """Test sending message uses the session's configured model."""
        session_manager.set_session_model("test-session-1", "openai/gpt-4o")
        
        async def mock_stream(*args, **kwargs):
            yield {"type": "text", "content": "test"}
        
        mock_opencode_client.stream_message = mock_stream
        
        messages = []
        async for msg in session_manager.send_message("test-session-1", "Hello"):
            messages.append(msg)
        
        mock_opencode_client.stream_message.assert_called_once()
        call_kwargs = mock_opencode_client.stream_message.call_args
        assert call_kwargs[1]["model"] == "openai/gpt-4o"

    @pytest.mark.asyncio
    async def test_send_message_model_override(self, session_manager, mock_opencode_client):
        """Test sending message with explicit model override."""
        session_manager.set_session_model("test-session-1", "openai/gpt-4o")
        
        async def mock_stream(*args, **kwargs):
            yield {"type": "text", "content": "test"}
        
        mock_opencode_client.stream_message = mock_stream
        
        messages = []
        async for msg in session_manager.send_message("test-session-1", "Hello", model="anthropic/claude-3-5-sonnet"):
            messages.append(msg)
        
        call_kwargs = mock_opencode_client.stream_message.call_args
        assert call_kwargs[1]["model"] == "anthropic/claude-3-5-sonnet"

    @pytest.mark.asyncio
    async def test_list_sessions(self, session_manager, mock_opencode_client):
        """Test listing sessions."""
        from opencode_client import Session
        mock_opencode_client.list_sessions = AsyncMock(return_value=[
            Session(id="s1", title="Session 1", slug="s1", created=123, updated=123),
            Session(id="s2", title="Session 2", slug="s2", created=123, updated=123),
        ])
        
        result = await session_manager.list_sessions()
        
        assert len(result) == 2
        assert result[0]["id"] == "s1"
        assert result[0]["title"] == "Session 1"