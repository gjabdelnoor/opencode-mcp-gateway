"""Tests for OpenCodeClient."""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from opencode_client import OpenCodeClient, Session, Message


class TestOpenCodeClient:
    """Test cases for OpenCodeClient."""

    @pytest.fixture
    def client(self):
        """Create an OpenCodeClient with mocked HTTP client."""
        client = OpenCodeClient(base_url="http://localhost:9999")
        client.client = MagicMock()
        return client

    @pytest.mark.asyncio
    async def test_health(self, client):
        """Test health check."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"status": "ok"})
        client.client.get = AsyncMock(return_value=mock_response)
        
        result = await client.health()
        
        assert result["status"] == "ok"
        client.client.get.assert_called_once_with("/global/health")

    @pytest.mark.asyncio
    async def test_list_sessions(self, client):
        """Test listing sessions."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=[
            {"id": "s1", "title": "Session 1", "slug": "s1", "time": {"created": 123, "updated": 456}},
            {"id": "s2", "title": "Session 2", "slug": "s2", "time": {"created": 789, "updated": 012}},
        ])
        client.client.get = AsyncMock(return_value=mock_response)
        
        result = await client.list_sessions()
        
        assert len(result) == 2
        assert result[0].id == "s1"
        assert result[0].title == "Session 1"
        assert result[1].id == "s2"
        assert result[1].title == "Session 2"

    @pytest.mark.asyncio
    async def test_get_session(self, client):
        """Test getting a single session."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={
            "id": "s1", "title": "Session 1", "directory": "/tmp"
        })
        client.client.get = AsyncMock(return_value=mock_response)
        
        result = await client.get_session("s1")
        
        assert result["id"] == "s1"
        client.client.get.assert_called_once_with("/session/s1")

    @pytest.mark.asyncio
    async def test_create_session(self, client):
        """Test creating a session."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"id": "new-s1", "title": "New Session"})
        client.client.post = AsyncMock(return_value=mock_response)
        
        result = await client.create_session(title="New Session", directory="/tmp")
        
        assert result["id"] == "new-s1"
        client.client.post.assert_called_once()
        call_args = client.client.post.call_args
        assert call_args[0][0] == "/session"
        assert call_args[1]["json"]["title"] == "New Session"
        assert call_args[1]["json"]["directory"] == "/tmp"

    @pytest.mark.asyncio
    async def test_create_session_with_defaults(self, client):
        """Test creating a session with no arguments."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"id": "new-s1"})
        client.client.post = AsyncMock(return_value=mock_response)
        
        result = await client.create_session()
        
        client.client.post.assert_called_once()
        call_args = client.client.post.call_args
        assert call_args[1]["json"] == {}

    @pytest.mark.asyncio
    async def test_delete_session(self, client):
        """Test deleting a session."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"success": True})
        client.client.delete = AsyncMock(return_value=mock_response)
        
        result = await client.delete_session("s1")
        
        assert result["success"] is True
        client.client.delete.assert_called_once_with("/session/s1")

    @pytest.mark.asyncio
    async def test_send_message(self, client):
        """Test sending a message."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"response": "Hello!"})
        client.client.post = AsyncMock(return_value=mock_response)
        
        result = await client.send_message("s1", "Hello")
        
        assert result["response"] == "Hello!"
        client.client.post.assert_called_once()
        call_args = client.client.post.call_args
        assert call_args[0][0] == "/session/s1/message"
        assert call_args[1]["json"]["parts"][0]["text"] == "Hello"

    @pytest.mark.asyncio
    async def test_send_message_with_model(self, client):
        """Test sending a message with model override."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"response": "Hello!"})
        client.client.post = AsyncMock(return_value=mock_response)
        
        result = await client.send_message("s1", "Hello", model="openai/gpt-4o")
        
        call_args = client.client.post.call_args
        assert call_args[1]["json"]["model"] == {"providerID": "openai/gpt-4o"}

    @pytest.mark.asyncio
    async def test_stream_message(self, client):
        """Test streaming messages."""
        async def mock_stream():
            async def inner():
                yield {"type": "text", "content": "Hello"}
                yield {"type": "text", "content": " World"}
            return inner()
        
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_stream())
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_response.aiter_lines = mock_stream
        client.client.stream = MagicMock(return_value=mock_stream_ctx)
        
        messages = []
        async for msg in client.stream_message("s1", "Hello"):
            messages.append(msg)
        
        assert len(messages) == 2
        assert messages[0]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_abort_message(self, client):
        """Test aborting a message."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"aborted": True})
        client.client.post = AsyncMock(return_value=mock_response)
        
        result = await client.abort_message("s1")
        
        assert result["aborted"] is True
        client.client.post.assert_called_once_with("/session/s1/abort")

    @pytest.mark.asyncio
    async def test_fork_session(self, client):
        """Test forking a session."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"id": "forked-s1"})
        client.client.post = AsyncMock(return_value=mock_response)
        
        result = await client.fork_session("s1")
        
        assert result["id"] == "forked-s1"
        client.client.post.assert_called_once_with("/session/s1/fork")

    @pytest.mark.asyncio
    async def test_create_pty(self, client):
        """Test creating a PTY."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"id": "pty-1"})
        client.client.post = AsyncMock(return_value=mock_response)
        
        result = await client.create_pty(cwd="/tmp")
        
        assert result["id"] == "pty-1"
        client.client.post.assert_called_once()
        call_args = client.client.post.call_args
        assert call_args[0][0] == "/pty"
        assert call_args[1]["json"]["cwd"] == "/tmp"

    @pytest.mark.asyncio
    async def test_resize_pty(self, client):
        """Test resizing a PTY."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"success": True})
        client.client.put = AsyncMock(return_value=mock_response)
        
        result = await client.resize_pty("pty-1", cols=80, rows=24)
        
        assert result["success"] is True
        client.client.put.assert_called_once()
        call_args = client.client.put.call_args
        assert call_args[0][0] == "/pty/pty-1"
        assert call_args[1]["json"]["cols"] == 80
        assert call_args[1]["json"]["rows"] == 24

    @pytest.mark.asyncio
    async def test_get_pty_output(self, client):
        """Test getting PTY output."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"data": "test output"})
        client.client.get = AsyncMock(return_value=mock_response)
        
        result = await client.get_pty_output("pty-1")
        
        assert result["data"] == "test output"
        client.client.get.assert_called_once_with("/pty/pty-1")

    @pytest.mark.asyncio
    async def test_close_pty(self, client):
        """Test closing a PTY."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"success": True})
        client.client.delete = AsyncMock(return_value=mock_response)
        
        result = await client.close_pty("pty-1")
        
        assert result["success"] is True
        client.client.delete.assert_called_once_with("/pty/pty-1")