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
        mock_response.json = MagicMock(
            return_value=[
                {
                    "id": "s1",
                    "title": "Session 1",
                    "slug": "s1",
                    "time": {"created": 123, "updated": 456},
                },
                {
                    "id": "s2",
                    "title": "Session 2",
                    "slug": "s2",
                    "time": {"created": 789, "updated": 789},
                },
            ]
        )
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
        mock_response.json = MagicMock(
            return_value={"id": "s1", "title": "Session 1", "directory": "/tmp"}
        )
        client.client.get = AsyncMock(return_value=mock_response)

        result = await client.get_session("s1")

        assert result["id"] == "s1"
        client.client.get.assert_called_once_with("/session/s1")

    @pytest.mark.asyncio
    async def test_create_session(self, client):
        """Test creating a session."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value={"id": "new-s1", "title": "New Session"}
        )
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
        mock_response.text = '{"success": true}'
        client.client.delete = AsyncMock(return_value=mock_response)

        result = await client.delete_session("s1")

        assert result["success"] is True
        client.client.delete.assert_called_once_with("/session/s1")

    @pytest.mark.asyncio
    async def test_delete_session_wraps_bool_response(self, client):
        """Test deleting wraps bare boolean response into a dict."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "true"
        mock_response.json = MagicMock(return_value=True)
        client.client.delete = AsyncMock(return_value=mock_response)

        result = await client.delete_session("s1")

        assert result == {"success": True, "deleted": True, "session_id": "s1"}
        client.client.delete.assert_called_once_with("/session/s1")

    @pytest.mark.asyncio
    async def test_get_session_status(self, client):
        """Test reading session status map."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"s1": {"type": "retry"}})
        client.client.get = AsyncMock(return_value=mock_response)

        result = await client.get_session_status()

        assert result == {"s1": {"type": "retry"}}
        client.client.get.assert_called_once_with("/session/status", params={})

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
        assert call_args[1]["json"]["model"] == {
            "providerID": "openai",
            "modelID": "gpt-4o",
        }

    @pytest.mark.asyncio
    async def test_stream_message(self, client):
        """Test stream_message yields parts from send_message."""
        client.send_message = AsyncMock(
            return_value={
                "parts": [
                    {"type": "text", "text": "Hello"},
                    {"type": "text", "text": " World"},
                ]
            }
        )

        messages = []
        async for msg in client.stream_message("s1", "Hello"):
            messages.append(msg)

        assert len(messages) == 3
        assert messages[0]["type"] == "part"
        assert messages[0]["part"]["text"] == "Hello"
        assert messages[-1]["type"] == "done"

    @pytest.mark.asyncio
    async def test_abort_message(self, client):
        """Test aborting a message."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"aborted": True})
        mock_response.text = '{"aborted": true}'
        client.client.post = AsyncMock(return_value=mock_response)

        result = await client.abort_message("s1")

        assert result["aborted"] is True
        client.client.post.assert_called_once_with("/session/s1/abort")

    @pytest.mark.asyncio
    async def test_abort_message_wraps_bool_response(self, client):
        """Test aborting wraps bare boolean response into a dict."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "true"
        mock_response.json = MagicMock(return_value=True)
        client.client.post = AsyncMock(return_value=mock_response)

        result = await client.abort_message("s1")

        assert result == {"success": True, "aborted": True, "session_id": "s1"}
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
    async def test_create_pty_with_command_args(self, client):
        """Test creating a PTY with explicit command details."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"id": "pty-1"})
        client.client.post = AsyncMock(return_value=mock_response)

        await client.create_pty(
            cwd="/tmp",
            command="/bin/bash",
            args=["-lc", "echo hi"],
            title="Runner",
            env={"FOO": "bar"},
        )

        call_args = client.client.post.call_args
        assert call_args[1]["json"]["command"] == "/bin/bash"
        assert call_args[1]["json"]["args"] == ["-lc", "echo hi"]
        assert call_args[1]["json"]["title"] == "Runner"
        assert call_args[1]["json"]["env"] == {"FOO": "bar"}

    @pytest.mark.asyncio
    async def test_list_ptys(self, client):
        """Test listing PTY sessions."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=[{"id": "pty-1"}])
        client.client.get = AsyncMock(return_value=mock_response)

        result = await client.list_ptys()

        assert result == [{"id": "pty-1"}]
        client.client.get.assert_called_once_with("/pty", params={})

    @pytest.mark.asyncio
    async def test_get_pty(self, client):
        """Test getting PTY metadata by ID."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value={"id": "pty-1", "status": "running"}
        )
        client.client.get = AsyncMock(return_value=mock_response)

        result = await client.get_pty("pty-1")

        assert result["id"] == "pty-1"
        client.client.get.assert_called_once_with("/pty/pty-1", params={})

    @pytest.mark.asyncio
    async def test_update_pty(self, client):
        """Test updating PTY title and size."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value={"id": "pty-1", "status": "running"}
        )
        client.client.put = AsyncMock(return_value=mock_response)

        result = await client.update_pty("pty-1", title="New", rows=30, cols=120)

        assert result["id"] == "pty-1"
        client.client.put.assert_called_once()
        call_args = client.client.put.call_args
        assert call_args[0][0] == "/pty/pty-1"
        assert call_args[1]["json"] == {
            "title": "New",
            "size": {"rows": 30, "cols": 120},
        }

    @pytest.mark.asyncio
    async def test_update_pty_requires_both_rows_and_cols(self, client):
        """Test update_pty validates rows/cols pair."""
        with pytest.raises(ValueError):
            await client.update_pty("pty-1", rows=24)

    @pytest.mark.asyncio
    async def test_write_pty(self, client):
        """Test writing PTY input through websocket helper."""
        client._pty_socket_exchange = AsyncMock(
            return_value={"success": True, "pty_id": "pty-1", "output": "ls\r\n"}
        )

        result = await client.write_pty("pty-1", "ls\n")

        assert result == {"success": True, "pty_id": "pty-1", "output": "ls\r\n"}
        client._pty_socket_exchange.assert_called_once_with(
            "pty-1", directory=None, write_data="ls\n"
        )

    @pytest.mark.asyncio
    async def test_run_shell(self, client):
        """Test running shell command via session shell endpoint."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = '{"info": {"id": "msg-1"}, "parts": []}'
        mock_response.json = MagicMock(
            return_value={"info": {"id": "msg-1"}, "parts": []}
        )
        client.client.post = AsyncMock(return_value=mock_response)

        result = await client.run_shell(
            "s1", "echo hi", model="openai/gpt-4o", agent="build"
        )

        assert result["info"]["id"] == "msg-1"
        call_args = client.client.post.call_args
        assert call_args[0][0] == "/session/s1/shell"
        assert call_args[1]["json"]["command"] == "echo hi"
        assert call_args[1]["json"]["agent"] == "build"
        assert call_args[1]["json"]["model"] == {
            "providerID": "openai",
            "modelID": "gpt-4o",
        }

    @pytest.mark.asyncio
    async def test_list_questions(self, client):
        """Test listing pending questions."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=[{"id": "q1"}])
        client.client.get = AsyncMock(return_value=mock_response)

        result = await client.list_questions()

        assert result == [{"id": "q1"}]
        client.client.get.assert_called_once_with("/question", params={})

    @pytest.mark.asyncio
    async def test_reply_question(self, client):
        """Test replying to pending question."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "true"
        mock_response.json = MagicMock(return_value=True)
        client.client.post = AsyncMock(return_value=mock_response)

        result = await client.reply_question("q1", [["Yes"]])

        assert result["answered"] is True
        assert result["request_id"] == "q1"
        assert result["answers"] == [["Yes"]]
        client.client.post.assert_called_once_with(
            "/question/q1/reply",
            params={},
            json={"answers": [["Yes"]]},
        )

    @pytest.mark.asyncio
    async def test_reject_question(self, client):
        """Test rejecting pending question."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "true"
        mock_response.json = MagicMock(return_value=True)
        client.client.post = AsyncMock(return_value=mock_response)

        result = await client.reject_question("q1")

        assert result["rejected"] is True
        assert result["request_id"] == "q1"
        client.client.post.assert_called_once_with(
            "/question/q1/reject",
            params={},
        )

    @pytest.mark.asyncio
    async def test_list_permissions(self, client):
        """Test listing pending permission requests."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=[{"id": "p1"}])
        client.client.get = AsyncMock(return_value=mock_response)

        result = await client.list_permissions()

        assert result == [{"id": "p1"}]
        client.client.get.assert_called_once_with("/permission", params={})

    @pytest.mark.asyncio
    async def test_reply_permission(self, client):
        """Test replying to pending permission request."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "true"
        mock_response.json = MagicMock(return_value=True)
        client.client.post = AsyncMock(return_value=mock_response)

        result = await client.reply_permission("p1", "always", message="trusted")

        assert result["replied"] is True
        assert result["request_id"] == "p1"
        assert result["reply"] == "always"
        client.client.post.assert_called_once_with(
            "/permission/p1/reply",
            params={},
            json={"reply": "always", "message": "trusted"},
        )

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
        """Test getting PTY output through websocket helper."""
        client._pty_socket_exchange = AsyncMock(
            return_value={"success": True, "pty_id": "pty-1", "output": "test output"}
        )

        result = await client.get_pty_output("pty-1")

        assert result["data"] == "test output"
        client._pty_socket_exchange.assert_called_once_with("pty-1")

    @pytest.mark.asyncio
    async def test_close_pty(self, client):
        """Test closing a PTY."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"success": True})
        mock_response.text = '{"success": true}'
        client.client.delete = AsyncMock(return_value=mock_response)

        result = await client.close_pty("pty-1")

        assert result["success"] is True
        client.client.delete.assert_called_once_with("/pty/pty-1")

    @pytest.mark.asyncio
    async def test_close_pty_wraps_bool_response(self, client):
        """Test closing PTY wraps bare boolean response into a dict."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "true"
        mock_response.json = MagicMock(return_value=True)
        client.client.delete = AsyncMock(return_value=mock_response)

        result = await client.close_pty("pty-1")

        assert result == {"success": True, "closed": True, "pty_id": "pty-1"}
        client.client.delete.assert_called_once_with("/pty/pty-1")
