"""Tests for SessionManager."""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestSessionManager:
    """Test cases for SessionManager."""

    @staticmethod
    def _completed_assistant_message(text: str = "done") -> list[dict]:
        return [
            {
                "info": {"id": "msg-1", "role": "assistant", "time": {"completed": 1}},
                "parts": [{"type": "text", "text": text}],
            }
        ]

    @pytest.mark.asyncio
    async def test_create_session_requires_initial_message(
        self, session_manager, mock_opencode_client
    ):
        """Test creating a new session requires initial_message."""
        mock_opencode_client.list_messages = AsyncMock(
            return_value=self._completed_assistant_message("created")
        )
        result = await session_manager.create_session(
            initial_message="Hello, build me a website",
            title="Test Session",
            directory="/tmp",
        )

        assert result["id"] == "new-session-1"
        mock_opencode_client.create_session.assert_called_once_with(
            title="Test Session", directory="/tmp", permissions=None
        )

    @pytest.mark.asyncio
    async def test_create_session_with_mode(
        self, session_manager, mock_opencode_client
    ):
        """Test creating a session in planning mode."""
        mock_opencode_client.list_messages = AsyncMock(
            return_value=self._completed_assistant_message("created")
        )
        result = await session_manager.create_session(
            initial_message="Hello", mode="planning"
        )

        assert result["id"] == "new-session-1"
        assert session_manager.get_session_mode("new-session-1") == "planning"

    @pytest.mark.asyncio
    async def test_create_session_with_auto_accept(
        self, session_manager, mock_opencode_client
    ):
        """Test creating a session with auto-accept permissions."""
        mock_opencode_client.list_messages = AsyncMock(
            return_value=self._completed_assistant_message("created")
        )
        permissions = [{"permission": "*", "pattern": "*", "action": "allow"}]
        result = await session_manager.create_session(
            initial_message="Hello", permissions=permissions
        )

        mock_opencode_client.create_session.assert_called_once_with(
            title=None, directory=None, permissions=permissions
        )

    @pytest.mark.asyncio
    async def test_delete_session(self, session_manager, mock_opencode_client):
        """Test deleting a session."""
        mock_opencode_client.list_messages = AsyncMock(
            return_value=self._completed_assistant_message("created")
        )
        await session_manager.create_session(initial_message="Hello", owner="claude")
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
        mock_opencode_client.list_messages = AsyncMock(
            return_value=TestSessionManager._completed_assistant_message("created")
        )
        await session_manager.create_session(initial_message="Hello", owner="claude")

        result = session_manager.set_active_session("new-session-1")

        assert result["success"] is True
        assert session_manager.get_active_session() == "new-session-1"

    @pytest.mark.asyncio
    async def test_set_active_session_not_found(
        self, session_manager, mock_opencode_client
    ):
        """Test setting active session with non-existent ID."""
        result = session_manager.set_active_session("non-existent-session")

        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_set_session_model(self, session_manager, mock_opencode_client):
        """Test setting session model."""
        result = session_manager.set_session_model(
            "test-session-1", "anthropic/claude-3-5-sonnet"
        )

        assert result["success"] is True
        assert (
            session_manager.get_session_model("test-session-1")
            == "anthropic/claude-3-5-sonnet"
        )

    @pytest.mark.asyncio
    async def test_get_session_model_not_set(
        self, session_manager, mock_opencode_client
    ):
        """Test getting model for session that doesn't have one set."""
        assert session_manager.get_session_model("test-session-1") is None

    @pytest.mark.asyncio
    async def test_list_sessions_with_pagination(
        self, session_manager, mock_opencode_client
    ):
        """Test listing sessions returns paginated format."""
        from opencode_client import Session

        mock_opencode_client.list_sessions = AsyncMock(
            return_value=[
                Session(
                    id="s1", title="Session 1", slug="s1", created=123, updated=123
                ),
                Session(
                    id="s2", title="Session 2", slug="s2", created=123, updated=123
                ),
            ]
        )
        mock_opencode_client.get_session = AsyncMock(
            return_value={
                "id": "s1",
                "title": "Session 1",
                "messages": [],
                "time": {"created": 123, "updated": 123},
            }
        )

        result = await session_manager.list_sessions(limit=10)

        assert "sessions" in result
        assert "next_cursor" in result
        assert len(result["sessions"]) == 2
        assert result["sessions"][0]["id"] == "s1"
        assert result["sessions"][0]["title"] == "Session 1"

    @pytest.mark.asyncio
    async def test_list_sessions_includes_recent_messages(
        self, session_manager, mock_opencode_client
    ):
        """Test listing sessions includes recent message previews."""
        from opencode_client import Session

        mock_opencode_client.list_sessions = AsyncMock(
            return_value=[
                Session(
                    id="s1", title="Session 1", slug="s1", created=123, updated=123
                ),
            ]
        )
        mock_opencode_client.get_session = AsyncMock(
            return_value={
                "id": "s1",
                "title": "Session 1",
                "messages": [
                    {"id": "m1", "role": "user", "content": "First message"},
                    {"id": "m2", "role": "assistant", "content": "Response"},
                    {"id": "m3", "role": "user", "content": "Third message"},
                ],
                "time": {"created": 123, "updated": 123},
            }
        )

        result = await session_manager.list_sessions(limit=10)

        assert len(result["sessions"]) == 1
        assert "recent_messages" in result["sessions"][0]
        assert len(result["sessions"][0]["recent_messages"]) == 3

    @pytest.mark.asyncio
    async def test_read_session_logs_summary(
        self, session_manager, mock_opencode_client
    ):
        """Test reading session logs in summary mode returns last 3 messages."""
        mock_opencode_client.get_session = AsyncMock(
            return_value={
                "id": "test-session-1",
                "messages": [
                    {"id": "m1", "role": "user", "content": "Message 1", "parts": []},
                    {
                        "id": "m2",
                        "role": "assistant",
                        "content": "",
                        "parts": [
                            {"type": "text", "text": "Thinking..."},
                            {
                                "type": "tool_use",
                                "name": "bash",
                                "input": {"cmd": "ls"},
                            },
                        ],
                    },
                    {"id": "m3", "role": "user", "content": "Message 3", "parts": []},
                ],
            }
        )

        result = await session_manager.read_session_logs(
            "test-session-1", mode="summary"
        )

        assert result["session_id"] == "test-session-1"
        assert result["mode"] == "summary"
        assert len(result["messages"]) == 3

    @pytest.mark.asyncio
    async def test_read_session_logs_full(self, session_manager, mock_opencode_client):
        """Test reading session logs in full mode."""
        mock_opencode_client.get_session = AsyncMock(
            return_value={
                "id": "test-session-1",
                "messages": [
                    {"id": "m1", "role": "user", "content": "Message 1", "parts": []},
                    {"id": "m2", "role": "assistant", "content": "", "parts": []},
                ],
            }
        )

        result = await session_manager.read_session_logs("test-session-1", mode="full")

        assert result["mode"] == "full"
        assert len(result["messages"]) == 2

    @pytest.mark.asyncio
    async def test_switch_mode_and_send(self, session_manager, mock_opencode_client):
        """Test switching mode and sending message."""
        session_manager.sessions["test-session-1"] = MagicMock()

        mock_opencode_client.prompt_async = AsyncMock(
            return_value={"accepted": True, "status_code": 204}
        )
        mock_opencode_client.list_messages = AsyncMock(
            return_value=[
                {
                    "info": {
                        "id": "msg-1",
                        "role": "assistant",
                        "time": {"completed": 1},
                    },
                    "parts": [{"type": "text", "text": "Done"}],
                }
            ]
        )

        result = await session_manager.switch_mode_and_send(
            "test-session-1", mode="building", message="Proceed with build"
        )

        assert result["mode_switched_to"] == "building"
        assert session_manager.get_session_mode("test-session-1") == "building"

    @pytest.mark.asyncio
    async def test_set_session_permissions(self, session_manager, mock_opencode_client):
        """Test setting session permissions on known session."""
        session_manager.sessions["test-session-1"] = MagicMock()
        permissions = [{"permission": "*", "pattern": "*", "action": "allow"}]
        result = await session_manager.set_session_permissions(
            "test-session-1", permissions
        )

        assert result["success"] is True
        mock_opencode_client.update_session.assert_called_once_with(
            "test-session-1", permission=permissions
        )

    @pytest.mark.asyncio
    async def test_wait_for_session_minimum_duration(
        self, session_manager, mock_opencode_client
    ):
        """Test wait_for_session enforces minimum 30 seconds."""
        mock_opencode_client.get_session = AsyncMock(
            return_value={
                "id": "test-session-1",
                "messages": [],
            }
        )

        start = time.time()
        result = await session_manager.wait_for_session("test-session-1", duration=10)
        elapsed = time.time() - start

        assert result["duration_seconds"] == 30
        assert elapsed >= 30

    @pytest.mark.asyncio
    async def test_send_message_with_timeout(
        self, session_manager, mock_opencode_client
    ):
        """Test send_message returns proper result structure."""
        mock_opencode_client.prompt_async = AsyncMock(
            return_value={"accepted": True, "status_code": 204}
        )
        mock_opencode_client.list_messages = AsyncMock(
            side_effect=[
                [],
                [
                    {
                        "info": {
                            "id": "msg-1",
                            "role": "assistant",
                            "time": {"completed": 1},
                        },
                        "parts": [{"type": "text", "text": "Hello world"}],
                    }
                ],
            ]
        )

        result = await session_manager.send_message("test-session-1", "Hi")

        assert "text" in result
        assert result["completed"] is True
        assert result["text"] == "Hello world"

    @pytest.mark.asyncio
    async def test_send_message_timeout_returns_partial(
        self, session_manager, mock_opencode_client
    ):
        """Test send_message returns partial result near timeout."""
        import session_manager as sm

        original_threshold = sm.NEAR_TIMEOUT_THRESHOLD
        sm.NEAR_TIMEOUT_THRESHOLD = 0

        mock_opencode_client.prompt_async = AsyncMock(
            return_value={"accepted": True, "status_code": 204}
        )
        mock_opencode_client.list_messages = AsyncMock(
            return_value=[
                {
                    "info": {"id": "msg-1", "role": "assistant", "time": {}},
                    "parts": [{"type": "text", "text": "Partial"}],
                }
            ]
        )

        try:
            start = time.time()
            result = await session_manager.send_message("test-session-1", "Hi")
            elapsed = time.time() - start

            assert elapsed < 60
            assert "partial_result" in result
            assert result["still_active"] is True
        finally:
            sm.NEAR_TIMEOUT_THRESHOLD = original_threshold

    @pytest.mark.asyncio
    async def test_ensure_session_uses_active_session(
        self, session_manager, mock_opencode_client
    ):
        """Test ensure_session prefers active session when set."""
        session_manager.active_session_id = "active-session"

        result = await session_manager.ensure_session()

        assert result == "active-session"
        mock_opencode_client.create_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_session_creates_fallback_session(
        self, session_manager, mock_opencode_client
    ):
        """Test ensure_session creates session when none are available."""
        mock_opencode_client.list_sessions = AsyncMock(return_value=[])
        mock_opencode_client.create_session = AsyncMock(
            return_value={"id": "shell-session"}
        )

        result = await session_manager.ensure_session()

        assert result == "shell-session"
        assert session_manager.active_session_id == "shell-session"
        assert session_manager.get_session_mode("shell-session") == "building"
        mock_opencode_client.create_session.assert_called_once_with(
            title="Raw Bash Session"
        )

    @pytest.mark.asyncio
    async def test_run_shell_command(self, session_manager, mock_opencode_client):
        """Test raw shell command execution response parsing."""
        session_id = "test-session-1"
        session_manager.active_session_id = session_id
        session_manager.session_modes[session_id] = "building"

        mock_opencode_client.run_shell = AsyncMock(
            return_value={
                "info": {
                    "id": "msg-1",
                    "time": {"completed": 1234567890},
                },
                "parts": [
                    {
                        "type": "tool",
                        "tool": "bash",
                        "state": {
                            "status": "completed",
                            "input": {"command": "echo shell_ok"},
                            "output": "shell_ok\n",
                            "metadata": {"output": "shell_ok\n"},
                        },
                    }
                ],
            }
        )

        result = await session_manager.run_shell_command(
            command="echo shell_ok",
            session_id=session_id,
            workdir="/tmp",
            timeout_seconds=30,
            description="smoke",
        )

        assert result["session_id"] == session_id
        assert result["message_id"] == "msg-1"
        assert result["output"] == "shell_ok"
        assert result["tool_status"] == "completed"
        assert result["agent"] == "build"
        assert result["mode"] == "building"
        assert "timeout 30s" in result["executed_command"]
        assert "cd /tmp" in result["executed_command"]

    @pytest.mark.asyncio
    async def test_list_pending_questions(self, session_manager, mock_opencode_client):
        """Test listing pending questions with session filter."""
        mock_opencode_client.list_questions = AsyncMock(
            return_value=[
                {
                    "id": "q1",
                    "sessionID": "test-session-1",
                    "questions": [
                        {
                            "header": "Confirm",
                            "question": "Proceed?",
                            "options": [{"label": "Yes", "description": "Continue"}],
                        }
                    ],
                },
                {
                    "id": "q2",
                    "sessionID": "other-session",
                    "questions": [],
                },
            ]
        )

        result = await session_manager.list_pending_questions(
            session_id="test-session-1"
        )

        assert result["count"] == 1
        assert result["questions"][0]["request_id"] == "q1"
        assert result["needs_human_input"] is True

    @pytest.mark.asyncio
    async def test_send_message_includes_pending_inputs(
        self, session_manager, mock_opencode_client
    ):
        """Test send_message surfaces pending question queue metadata."""
        mock_opencode_client.prompt_async = AsyncMock(
            return_value={"accepted": True, "status_code": 204}
        )
        mock_opencode_client.list_messages = AsyncMock(
            side_effect=[
                [],
                [
                    {
                        "info": {
                            "id": "m1",
                            "role": "assistant",
                            "time": {"completed": 1},
                        },
                        "parts": [{"type": "text", "text": "done"}],
                    }
                ],
            ]
        )
        mock_opencode_client.list_questions = AsyncMock(
            return_value=[
                {
                    "id": "q1",
                    "sessionID": "test-session-1",
                    "questions": [
                        {
                            "header": "Select",
                            "question": "Pick one",
                            "options": [{"label": "A", "description": "Option A"}],
                        }
                    ],
                }
            ]
        )
        mock_opencode_client.list_permissions = AsyncMock(return_value=[])

        result = await session_manager.send_message("test-session-1", "Hi")

        assert result["text"] == "done"
        assert result["needs_human_input"] is True
        assert len(result["pending_questions"]) == 1
        assert "question_list/permission_list" in result["next_action"]

    @pytest.mark.asyncio
    async def test_send_message_surfaces_backend_retry_status(
        self, session_manager, mock_opencode_client
    ):
        """Test send_message exposes backend retry errors like unsupported model."""
        mock_opencode_client.prompt_async = AsyncMock(
            return_value={"accepted": True, "status_code": 204}
        )
        mock_opencode_client.list_messages = AsyncMock(
            return_value=[
                {
                    "info": {"id": "m1", "role": "assistant", "time": {}},
                    "parts": [],
                }
            ]
        )
        mock_opencode_client.get_session_status = AsyncMock(
            return_value={
                "test-session-1": {
                    "type": "retry",
                    "message": "unsupported model",
                    "attempt": 3,
                }
            }
        )

        result = await session_manager.send_message("test-session-1", "Hi")

        assert result["error"] == "unsupported model"
        assert result["backend_status"]["type"] == "retry"
        assert "switch_model" in result["next_action"]

    @pytest.mark.asyncio
    async def test_answer_question_updates_queue_state(
        self, session_manager, mock_opencode_client
    ):
        """Test answering question returns remaining queue information."""
        mock_opencode_client.reply_question = AsyncMock(
            return_value={
                "success": True,
                "answered": True,
                "request_id": "q1",
            }
        )
        mock_opencode_client.list_questions = AsyncMock(return_value=[])
        mock_opencode_client.list_permissions = AsyncMock(return_value=[])

        result = await session_manager.answer_question("q1", [["Yes"]])

        assert result["success"] is True
        assert result["remaining_questions"] == 0
        assert result["needs_human_input"] is False


class TestSessionModes:
    """Test session mode functionality."""

    @pytest.mark.asyncio
    async def test_session_defaults_to_planning_mode(
        self, session_manager, mock_opencode_client
    ):
        """Test new sessions default to planning mode."""
        mock_opencode_client.list_messages = AsyncMock(
            return_value=TestSessionManager._completed_assistant_message("created")
        )
        await session_manager.create_session(initial_message="Hello")

        mode = session_manager.get_session_mode("new-session-1")
        assert mode == "planning"

    @pytest.mark.asyncio
    async def test_switch_mode_invalid_mode(
        self, session_manager, mock_opencode_client
    ):
        """Test switching to invalid mode returns error."""
        result = session_manager.set_session_mode("test-session-1", "invalid")

        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_switch_mode_nonexistent_session(
        self, session_manager, mock_opencode_client
    ):
        """Test switching mode on nonexistent session returns error."""
        result = session_manager.set_session_mode("nonexistent", "building")

        assert result["success"] is False
