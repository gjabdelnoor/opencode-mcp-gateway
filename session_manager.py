import asyncio
import os
import time
import shlex
import httpx
from typing import Optional, Literal
from datetime import datetime
import structlog
from opencode_client import OpenCodeClient, Session

logger = structlog.get_logger()

TOOL_TIMEOUT = 50  # seconds - MCP tool call timeout
NEAR_TIMEOUT_THRESHOLD = 45  # seconds - start returning partial results
MIN_WAIT_DURATION = 30  # seconds - minimum wait time for wait_for_session


class SessionInfo:
    def __init__(self, session_id: str, title: str, owner: str, created_at: datetime):
        self.id = session_id
        self.title = title
        self.owner = owner
        self.created_at = created_at
        self.last_used = created_at
        self.client: Optional[OpenCodeClient] = None

    def touch(self):
        self.last_used = datetime.now()


class SessionManager:
    def __init__(self, oc_client: OpenCodeClient):
        self.oc = oc_client
        self.sessions: dict[str, SessionInfo] = {}
        self.user_session_ids: set[str] = set()
        self.claude_session_ids: set[str] = set()
        self.active_session_id: Optional[str] = None
        self.session_models: dict[str, str] = {}
        self.session_modes: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self.default_planning_model = (
            os.environ.get("DEFAULT_PLANNING_MODEL", "").strip() or None
        )
        self.default_building_model = (
            os.environ.get("DEFAULT_BUILDING_MODEL", "").strip() or None
        )

    async def refresh_user_sessions(self):
        """Load user's existing sessions from OpenCode."""
        async with self._lock:
            try:
                sessions = await self.oc.list_sessions()
                self.user_session_ids = {s.id for s in sessions}
                logger.info("refreshed_user_sessions", count=len(sessions))
            except Exception as e:
                logger.error("failed_to_refresh_user_sessions", error=str(e))

    def get_all_session_ids(self) -> list[str]:
        """Return all known session IDs."""
        return list(self.user_session_ids) + list(self.claude_session_ids)

    def get_claude_session_ids(self) -> list[str]:
        """Return session IDs owned by Claude."""
        return list(self.claude_session_ids)

    async def create_session(
        self,
        initial_message: str,
        title: Optional[str] = None,
        directory: Optional[str] = None,
        owner: str = "claude",
        mode: str = "planning",
        permissions: Optional[list] = None,
    ) -> dict:
        """Create a new session with mandatory initial message.

        Args:
            initial_message: REQUIRED - First message to send to the session
            title: Optional session title
            directory: Optional working directory
            owner: "claude" or "user"
            mode: "planning" (default) or "building"
            permissions: Optional permission list for auto-accept
        """
        async with self._lock:
            result = await self.oc.create_session(
                title=title, directory=directory, permissions=permissions
            )
            session_id = result.get("id")
            if session_id:
                info = SessionInfo(
                    session_id=session_id,
                    title=title or "Untitled",
                    owner=owner,
                    created_at=datetime.now(),
                )
                self.sessions[session_id] = info
                if owner == "claude":
                    self.claude_session_ids.add(session_id)
                self.session_modes[session_id] = mode
                logger.info(
                    "created_session", session_id=session_id, owner=owner, mode=mode
                )

        session_id = result.get("id")
        if session_id and initial_message:
            result["initial_response"] = await self._send_message_with_timeout(
                session_id,
                initial_message,
            )

        return result

    async def delete_session(self, session_id: str) -> dict:
        """Delete a session."""
        async with self._lock:
            result = await self.oc.delete_session(session_id)
            if session_id in self.sessions:
                del self.sessions[session_id]
            self.claude_session_ids.discard(session_id)
            self.user_session_ids.discard(session_id)
            logger.info("deleted_session", session_id=session_id)
            return result

    async def fork_session(self, session_id: str) -> dict:
        """Fork an existing session."""
        async with self._lock:
            result = await self.oc.fork_session(session_id)
            new_id = result.get("id")
            if new_id:
                info = SessionInfo(
                    session_id=new_id,
                    title=f"Fork of {session_id}",
                    owner="claude",
                    created_at=datetime.now(),
                )
                self.sessions[new_id] = info
                self.claude_session_ids.add(new_id)
                logger.info("forked_session", original=session_id, forked=new_id)
            return result

    def _agent_for_session_mode(self, session_id: str) -> str:
        mode = self.session_modes.get(session_id, "planning")
        return "plan" if mode == "planning" else "build"

    async def ensure_session(self, session_id: Optional[str] = None) -> str:
        """Resolve a session ID for operations that can run without explicit session input."""
        if session_id:
            return session_id

        if self.active_session_id:
            return self.active_session_id

        async with self._lock:
            created = await self.oc.create_session(title="Raw Bash Session")
            new_id = created.get("id")
            if not new_id:
                raise RuntimeError(
                    "Failed to create fallback session for bash execution"
                )

            info = SessionInfo(
                session_id=new_id,
                title="Raw Bash Session",
                owner="claude",
                created_at=datetime.now(),
            )
            self.sessions[new_id] = info
            self.claude_session_ids.add(new_id)
            self.session_modes[new_id] = "building"
            self.active_session_id = new_id
            logger.info("created_shell_session", session_id=new_id)
            return new_id

    @staticmethod
    def _build_shell_command(
        command: str, workdir: Optional[str], timeout_seconds: int
    ) -> str:
        inner = command
        if workdir:
            inner = f"cd {shlex.quote(workdir)} && {inner}"

        wrapped = f"bash -lc {shlex.quote(inner)}"
        if timeout_seconds > 0:
            wrapped = f"timeout {int(timeout_seconds)}s {wrapped}"

        return wrapped

    @staticmethod
    def _format_question_request(request: dict) -> dict:
        questions = []
        for item in request.get("questions", []):
            questions.append(
                {
                    "header": item.get("header", ""),
                    "question": item.get("question", ""),
                    "multiple": bool(item.get("multiple", False)),
                    "custom": item.get("custom", True),
                    "options": item.get("options", []),
                }
            )

        return {
            "request_id": request.get("id"),
            "session_id": request.get("sessionID"),
            "tool": request.get("tool"),
            "questions": questions,
        }

    @staticmethod
    def _format_permission_request(request: dict) -> dict:
        return {
            "request_id": request.get("id"),
            "session_id": request.get("sessionID"),
            "permission": request.get("permission"),
            "patterns": request.get("patterns", []),
            "metadata": request.get("metadata", {}),
            "always": request.get("always", []),
            "tool": request.get("tool"),
        }

    async def _collect_pending_inputs(self, session_id: Optional[str]) -> dict:
        pending_questions: list[dict] = []
        pending_permissions: list[dict] = []
        errors: list[str] = []

        try:
            questions = await self.oc.list_questions()
            if session_id:
                questions = [q for q in questions if q.get("sessionID") == session_id]
            pending_questions = [self._format_question_request(q) for q in questions]
        except Exception as e:
            errors.append(f"question_list_failed: {e}")

        try:
            permissions = await self.oc.list_permissions()
            if session_id:
                permissions = [
                    p for p in permissions if p.get("sessionID") == session_id
                ]
            pending_permissions = [
                self._format_permission_request(p) for p in permissions
            ]
        except Exception as e:
            errors.append(f"permission_list_failed: {e}")

        return {
            "pending_questions": pending_questions,
            "pending_permissions": pending_permissions,
            "needs_human_input": bool(pending_questions or pending_permissions),
            "pending_input_errors": errors,
        }

    async def _attach_pending_inputs(
        self, result: dict, session_id: Optional[str]
    ) -> dict:
        pending = await self._collect_pending_inputs(session_id)
        result.update(pending)
        if result.get("needs_human_input") and "next_action" not in result:
            result["next_action"] = (
                "Pending interactive request detected. "
                "Call question_list/permission_list, then answer via question_reply or permission_reply."
            )
        return result

    async def list_pending_questions(self, session_id: Optional[str] = None) -> dict:
        pending = await self._collect_pending_inputs(session_id)
        return {
            "session_id": session_id,
            "questions": pending["pending_questions"],
            "count": len(pending["pending_questions"]),
            "needs_human_input": bool(pending["pending_questions"]),
            "errors": [
                e
                for e in pending["pending_input_errors"]
                if e.startswith("question_list_failed")
            ],
        }

    async def answer_question(self, request_id: str, answers: list[list[str]]) -> dict:
        result = await self.oc.reply_question(request_id=request_id, answers=answers)
        pending = await self._collect_pending_inputs(None)
        result["remaining_questions"] = len(pending["pending_questions"])
        result["needs_human_input"] = bool(
            pending["pending_questions"] or pending["pending_permissions"]
        )
        return result

    async def reject_question(self, request_id: str) -> dict:
        result = await self.oc.reject_question(request_id=request_id)
        pending = await self._collect_pending_inputs(None)
        result["remaining_questions"] = len(pending["pending_questions"])
        result["needs_human_input"] = bool(
            pending["pending_questions"] or pending["pending_permissions"]
        )
        return result

    async def list_pending_permissions(self, session_id: Optional[str] = None) -> dict:
        pending = await self._collect_pending_inputs(session_id)
        return {
            "session_id": session_id,
            "permissions": pending["pending_permissions"],
            "count": len(pending["pending_permissions"]),
            "needs_human_input": bool(pending["pending_permissions"]),
            "errors": [
                e
                for e in pending["pending_input_errors"]
                if e.startswith("permission_list_failed")
            ],
        }

    async def reply_permission(
        self,
        request_id: str,
        reply: Literal["once", "always", "reject"],
        message: str = "",
    ) -> dict:
        result = await self.oc.reply_permission(
            request_id=request_id,
            reply=reply,
            message=message or None,
        )
        pending = await self._collect_pending_inputs(None)
        result["remaining_permissions"] = len(pending["pending_permissions"])
        result["needs_human_input"] = bool(
            pending["pending_questions"] or pending["pending_permissions"]
        )
        return result

    def _extract_message_activity(self, message: dict) -> dict:
        parts = message.get("parts", [])
        text_chunks = []
        tool_calls = []
        reasoning_chunks = []

        for part in parts:
            part_type = part.get("type", "")
            if part_type == "text":
                text = part.get("text", "")
                if text:
                    text_chunks.append(text)
            elif part_type == "reasoning":
                text = part.get("text", "")
                if text:
                    reasoning_chunks.append(text)
            elif part_type == "tool":
                tool_calls.append(
                    {
                        "tool": part.get("tool", "unknown"),
                        "state": part.get("state", {}),
                    }
                )

        info = message.get("info", {})
        return {
            "text": "\n".join(text_chunks).strip(),
            "tool_calls": tool_calls,
            "reasoning": reasoning_chunks,
            "parts": parts,
            "info": info,
            "completed": bool(info.get("time", {}).get("completed")),
        }

    def _resolve_model_for_session(
        self, session_id: str, model: Optional[str] = None
    ) -> Optional[str]:
        if model:
            return model

        stored_model = self.session_models.get(session_id)
        if stored_model:
            return stored_model

        mode = self.session_modes.get(session_id, "planning")
        if mode == "planning":
            return self.default_planning_model
        return self.default_building_model

    async def _recent_message_ids(self, session_id: str, limit: int = 20) -> set[str]:
        try:
            messages = await self.oc.list_messages(session_id, limit=limit)
        except Exception as e:
            logger.warning(
                "list_message_ids_failed", session_id=session_id, error=str(e)
            )
            return set()

        result: set[str] = set()
        for msg in messages:
            message_id = msg.get("info", {}).get("id")
            if message_id:
                result.add(message_id)
        return result

    async def _latest_assistant_snapshot(
        self,
        session_id: str,
        limit: int = 20,
        exclude_ids: Optional[set[str]] = None,
    ) -> Optional[dict]:
        exclude_ids = exclude_ids or set()
        try:
            messages = await self.oc.list_messages(session_id, limit=limit)
        except Exception as e:
            logger.warning("list_messages_failed", session_id=session_id, error=str(e))
            return None

        for msg in reversed(messages):
            info = msg.get("info", {})
            if info.get("role") != "assistant":
                continue
            if info.get("id") in exclude_ids:
                continue
            return msg
        return None

    async def _session_backend_status(self, session_id: str) -> Optional[dict]:
        try:
            status_map = await self.oc.get_session_status()
        except Exception as e:
            logger.warning("session_status_failed", session_id=session_id, error=str(e))
            return None

        status = status_map.get(session_id)
        return status if isinstance(status, dict) else None

    async def _format_backend_retry_result(
        self,
        session_id: str,
        agent: str,
        elapsed: int,
        backend_status: dict,
    ) -> dict:
        return await self._attach_pending_inputs(
            {
                "error": backend_status.get(
                    "message", "OpenCode backend is retrying the request."
                ),
                "backend_status": backend_status,
                "elapsed_seconds": elapsed,
                "agent": agent,
                "mode": self.session_modes.get(session_id, "planning"),
                "next_action": (
                    "The current OpenCode model appears unavailable. "
                    "Use switch_model with a supported model or set DEFAULT_PLANNING_MODEL/DEFAULT_BUILDING_MODEL."
                ),
            },
            session_id,
        )

    async def _send_message_with_timeout(
        self,
        session_id: str,
        prompt: str,
        model: Optional[str] = None,
        timeout: int = TOOL_TIMEOUT,
    ) -> dict:
        """Send message with near-timeout handling.

        Returns full result if OpenCode responds in time.
        Returns partial result + reasoning + still_active=True if nearing timeout.
        """
        model = self._resolve_model_for_session(session_id, model)
        agent = self._agent_for_session_mode(session_id)
        start_time = time.time()
        existing_message_ids = await self._recent_message_ids(session_id)
        poll_interval = 1.0

        try:
            response = await self.oc.prompt_async(
                session_id=session_id,
                prompt=prompt,
                model=model,
                agent=agent,
            )
            if isinstance(response, dict) and response.get("accepted") is False:
                return await self._attach_pending_inputs(
                    {
                        "error": "OpenCode rejected the prompt_async request.",
                        "backend_response": response,
                        "elapsed_seconds": int(time.time() - start_time),
                        "agent": agent,
                        "mode": self.session_modes.get(session_id, "planning"),
                    },
                    session_id,
                )

            latest: Optional[dict] = None
            while time.time() - start_time < timeout:
                elapsed = int(time.time() - start_time)
                latest = await self._latest_assistant_snapshot(
                    session_id,
                    exclude_ids=existing_message_ids,
                )
                if latest:
                    extracted = self._extract_message_activity(latest)
                    if (
                        extracted["completed"]
                        or extracted["text"]
                        or extracted["tool_calls"]
                    ):
                        return await self._attach_pending_inputs(
                            {
                                "text": extracted["text"],
                                "tool_calls": extracted["tool_calls"],
                                "reasoning": extracted["reasoning"],
                                "completed": extracted["completed"] or True,
                                "elapsed_seconds": elapsed,
                                "agent": agent,
                                "mode": self.session_modes.get(session_id, "planning"),
                            },
                            session_id,
                        )

                backend_status = await self._session_backend_status(session_id)
                if backend_status and backend_status.get("type") == "retry":
                    return await self._format_backend_retry_result(
                        session_id,
                        agent,
                        elapsed,
                        backend_status,
                    )

                await asyncio.sleep(poll_interval)

            elapsed = int(time.time() - start_time)
            latest = await self._latest_assistant_snapshot(
                session_id,
                exclude_ids=existing_message_ids,
            )
            if latest:
                extracted = self._extract_message_activity(latest)
                return await self._attach_pending_inputs(
                    {
                        "partial_result": {
                            "text": extracted["text"][:1000],
                            "tool_calls": extracted["tool_calls"][:5],
                            "message": "Response still in progress. Use read_session_logs for full output, or wait_for_session to continue monitoring.",
                        },
                        "reasoning_so_far": extracted["reasoning"][:5],
                        "still_active": True,
                        "elapsed_seconds": elapsed,
                        "agent": agent,
                        "mode": self.session_modes.get(session_id, "planning"),
                    },
                    session_id,
                )

            backend_status = await self._session_backend_status(session_id)
            if backend_status and backend_status.get("type") == "retry":
                return await self._format_backend_retry_result(
                    session_id,
                    agent,
                    elapsed,
                    backend_status,
                )

            return await self._attach_pending_inputs(
                {
                    "partial_result": {
                        "text": "",
                        "tool_calls": [],
                        "message": "Request accepted but no output yet. Use read_session_logs or wait_for_session.",
                    },
                    "reasoning_so_far": [],
                    "still_active": True,
                    "elapsed_seconds": elapsed,
                    "agent": agent,
                    "mode": self.session_modes.get(session_id, "planning"),
                },
                session_id,
            )

        except httpx.TimeoutException:
            elapsed = int(time.time() - start_time)
            latest = await self._latest_assistant_snapshot(
                session_id,
                exclude_ids=existing_message_ids,
            )
            if latest:
                extracted = self._extract_message_activity(latest)
                return await self._attach_pending_inputs(
                    {
                        "partial_result": {
                            "text": extracted["text"][:1000],
                            "tool_calls": extracted["tool_calls"][:5],
                            "message": f"Response still in progress after {elapsed} seconds. Use read_session_logs for full output, or wait_for_session to continue monitoring.",
                        },
                        "reasoning_so_far": extracted["reasoning"][:5],
                        "still_active": True,
                        "elapsed_seconds": elapsed,
                        "agent": agent,
                        "mode": self.session_modes.get(session_id, "planning"),
                    },
                    session_id,
                )

            backend_status = await self._session_backend_status(session_id)
            if backend_status and backend_status.get("type") == "retry":
                return await self._format_backend_retry_result(
                    session_id,
                    agent,
                    elapsed,
                    backend_status,
                )

            return await self._attach_pending_inputs(
                {
                    "partial_result": {
                        "text": "",
                        "tool_calls": [],
                        "message": f"Response still in progress after {elapsed} seconds. Use read_session_logs for full output, or wait_for_session to continue monitoring.",
                    },
                    "reasoning_so_far": [],
                    "still_active": True,
                    "elapsed_seconds": elapsed,
                    "agent": agent,
                    "mode": self.session_modes.get(session_id, "planning"),
                },
                session_id,
            )

        except Exception as e:
            logger.error("send_message_error", session_id=session_id, error=str(e))
            elapsed = int(time.time() - start_time)
            return await self._attach_pending_inputs(
                {
                    "error": str(e),
                    "elapsed_seconds": elapsed,
                    "agent": agent,
                    "mode": self.session_modes.get(session_id, "planning"),
                },
                session_id,
            )

    async def send_message(
        self, session_id: str, prompt: str, model: Optional[str] = None
    ) -> dict:
        """Send a message to a session with timeout handling."""
        return await self._send_message_with_timeout(session_id, prompt, model=model)

    async def run_shell_command(
        self,
        command: str,
        session_id: Optional[str] = None,
        workdir: Optional[str] = None,
        timeout_seconds: int = 120,
        description: str = "",
    ) -> dict:
        """Run a raw shell command via OpenCode's shell endpoint."""
        timeout_seconds = max(1, int(timeout_seconds))
        start_time = time.time()

        resolved_session_id = await self.ensure_session(session_id=session_id)
        model = self.session_models.get(resolved_session_id)
        agent = self._agent_for_session_mode(resolved_session_id)
        wrapped_command = self._build_shell_command(
            command=command,
            workdir=workdir,
            timeout_seconds=timeout_seconds,
        )
        request_timeout = max(timeout_seconds + 15, TOOL_TIMEOUT)

        try:
            response = await self.oc.run_shell(
                session_id=resolved_session_id,
                command=wrapped_command,
                model=model,
                agent=agent,
                timeout=request_timeout,
            )
            elapsed = int(time.time() - start_time)

            info = response.get("info", {}) if isinstance(response, dict) else {}
            parts = response.get("parts", []) if isinstance(response, dict) else []

            outputs: list[str] = []
            tool_calls = []
            tool_status = "unknown"

            for part in parts:
                part_type = part.get("type", "")
                if part_type == "text":
                    text = part.get("text", "")
                    if text:
                        outputs.append(text)

                if part_type != "tool":
                    continue

                state = part.get("state", {})
                metadata = state.get("metadata", {})
                output = state.get("output") or metadata.get("output")
                if output:
                    outputs.append(str(output))

                if part.get("tool") == "bash":
                    tool_status = state.get("status", tool_status)

                tool_calls.append(
                    {
                        "tool": part.get("tool", "unknown"),
                        "state": state,
                    }
                )

            cleaned_output: list[str] = []
            for chunk in outputs:
                if chunk and chunk not in cleaned_output:
                    cleaned_output.append(chunk)

            return await self._attach_pending_inputs(
                {
                    "session_id": resolved_session_id,
                    "message_id": info.get("id"),
                    "command": command,
                    "executed_command": wrapped_command,
                    "description": description,
                    "workdir": workdir,
                    "timeout_seconds": timeout_seconds,
                    "output": "\n".join(cleaned_output).strip(),
                    "tool_calls": tool_calls,
                    "tool_status": tool_status,
                    "completed": bool(info.get("time", {}).get("completed")),
                    "elapsed_seconds": elapsed,
                    "agent": agent,
                    "mode": self.session_modes.get(resolved_session_id, "planning"),
                },
                resolved_session_id,
            )

        except httpx.TimeoutException:
            elapsed = int(time.time() - start_time)
            return await self._attach_pending_inputs(
                {
                    "session_id": resolved_session_id,
                    "command": command,
                    "executed_command": wrapped_command,
                    "description": description,
                    "workdir": workdir,
                    "timeout_seconds": timeout_seconds,
                    "elapsed_seconds": elapsed,
                    "still_active": True,
                    "message": "Command is still running or delayed. Use read_session_logs for more detail.",
                    "agent": agent,
                    "mode": self.session_modes.get(resolved_session_id, "planning"),
                },
                resolved_session_id,
            )
        except Exception as e:
            elapsed = int(time.time() - start_time)
            logger.error(
                "run_shell_command_error", session_id=resolved_session_id, error=str(e)
            )
            return await self._attach_pending_inputs(
                {
                    "session_id": resolved_session_id,
                    "command": command,
                    "executed_command": wrapped_command,
                    "description": description,
                    "workdir": workdir,
                    "timeout_seconds": timeout_seconds,
                    "elapsed_seconds": elapsed,
                    "error": str(e),
                    "agent": agent,
                    "mode": self.session_modes.get(resolved_session_id, "planning"),
                },
                resolved_session_id,
            )

    async def send_message_stream(
        self,
        session_id: str,
        prompt: str,
        stream: bool = True,
        model: Optional[str] = None,
    ):
        """Send a message to a session (legacy stream support)."""
        if model is None:
            model = self.session_models.get(session_id)
        agent = self._agent_for_session_mode(session_id)
        if stream:
            return self.oc.stream_message(session_id, prompt, model=model, agent=agent)
        else:
            return await self.oc.send_message(
                session_id, prompt, model=model, agent=agent
            )

    async def abort_message(self, session_id: str) -> dict:
        """Abort ongoing message generation."""
        return await self.oc.abort_message(session_id)

    async def get_session(self, session_id: str) -> dict:
        """Get full session state."""
        return await self.oc.get_session(session_id)

    async def list_sessions(
        self, cursor: Optional[str] = None, limit: int = 10
    ) -> dict:
        """List sessions with pagination and recent message preview.

        Returns dict with:
            - sessions: list of session info with last 3 messages
            - next_cursor: cursor for next page or None
        """
        await self.refresh_user_sessions()
        all_ids = self.get_all_session_ids()

        if cursor:
            try:
                start_idx = all_ids.index(cursor) + 1
                all_ids = all_ids[start_idx:]
            except ValueError:
                pass

        result = []
        last_id = None
        for sid in all_ids[:limit]:
            try:
                sess = await self.oc.get_session(sid)
                owner = "claude" if sid in self.claude_session_ids else "user"
                mode = self.session_modes.get(sid, "planning")
                model = self.session_models.get(sid)

                recent_messages = []
                try:
                    messages = await self.oc.list_messages(sid, limit=3)
                except Exception as e:
                    logger.warning(
                        "list_recent_messages_failed", session_id=sid, error=str(e)
                    )
                    messages = []

                for msg in messages[-3:]:
                    info = msg.get("info", {})
                    parts = msg.get("parts", [])
                    text_chunks = [
                        p.get("text", "") for p in parts if p.get("type") == "text"
                    ]
                    recent_messages.append(
                        {
                            "role": info.get("role"),
                            "content": "\n".join([t for t in text_chunks if t]).strip()[
                                :300
                            ],
                            "parts_count": len(parts),
                            "message_id": info.get("id"),
                        }
                    )

                result.append(
                    {
                        "id": sid,
                        "title": sess.get("title", "Untitled"),
                        "owner": owner,
                        "directory": sess.get("directory"),
                        "created": sess.get("time", {}).get("created"),
                        "updated": sess.get("time", {}).get("updated"),
                        "model": model,
                        "mode": mode,
                        "is_active": sid == self.active_session_id,
                        "recent_messages": recent_messages,
                    }
                )
                last_id = sid
            except Exception as e:
                logger.warning("failed_to_get_session", session_id=sid, error=str(e))
                continue

        next_cursor = last_id if len(all_ids) > limit else None

        return {
            "sessions": result,
            "next_cursor": next_cursor,
            "total": len(all_ids),
        }

    async def read_session_logs(
        self, session_id: str, mode: Literal["summary", "full"] = "summary"
    ) -> dict:
        """Read session logs (non-blocking).

        Args:
            session_id: The session ID
            mode: "summary" (last 3 messages) or "full" (all messages)
        """
        try:
            limit = 3 if mode == "summary" else 200
            messages = await self.oc.list_messages(session_id, limit=limit)

            parsed_messages = []
            for msg in messages:
                info = msg.get("info", {})
                parts = msg.get("parts", [])
                parsed_parts = []

                for part in parts:
                    part_type = part.get("type", "")
                    if part_type == "text":
                        parsed_parts.append(
                            {
                                "type": "text",
                                "text": part.get("text", "")[:500],
                            }
                        )
                    elif part_type == "tool_use":
                        parsed_parts.append(
                            {
                                "type": "tool_use",
                                "tool": part.get("name", "unknown"),
                                "input": str(part.get("input", {}))[:200],
                            }
                        )
                    elif part_type == "tool_result":
                        parsed_parts.append(
                            {
                                "type": "tool_result",
                                "content": str(part.get("content", ""))[:200],
                            }
                        )
                    elif part_type == "reasoning":
                        parsed_parts.append(
                            {
                                "type": "reasoning",
                                "text": part.get("text", "")[:500],
                            }
                        )
                    elif part_type == "tool":
                        parsed_parts.append(
                            {
                                "type": "tool",
                                "tool": part.get("tool", "unknown"),
                                "state": part.get("state", {}),
                            }
                        )
                    else:
                        parsed_parts.append(
                            {
                                "type": part_type,
                            }
                        )

                text_chunks = [
                    p.get("text", "") for p in parts if p.get("type") == "text"
                ]

                parsed_messages.append(
                    {
                        "id": info.get("id", ""),
                        "role": info.get("role", ""),
                        "content": "\n".join([t for t in text_chunks if t]).strip()[
                            :500
                        ]
                        if text_chunks
                        else None,
                        "mode": info.get("mode"),
                        "agent": info.get("agent"),
                        "created": info.get("time", {}).get("created"),
                        "completed": info.get("time", {}).get("completed"),
                        "parts": parsed_parts,
                    }
                )

            return {
                "session_id": session_id,
                "mode": mode,
                "messages": parsed_messages,
                "total_messages": len(messages),
            }
        except Exception as e:
            logger.error("read_session_logs_error", session_id=session_id, error=str(e))
            return {"error": str(e), "session_id": session_id}

    def set_active_session(self, session_id: str) -> dict:
        """Set the active session for Claude."""
        if session_id in self.user_session_ids or session_id in self.claude_session_ids:
            self.active_session_id = session_id
            logger.info("set_active_session", session_id=session_id)
            return {"success": True, "active_session_id": session_id}
        return {"success": False, "error": "Session not found"}

    def get_active_session(self) -> Optional[str]:
        """Get the active session ID."""
        return self.active_session_id

    def set_session_model(self, session_id: str, model: str) -> dict:
        """Set the model for a session."""
        self.session_models[session_id] = model
        logger.info("set_session_model", session_id=session_id, model=model)
        return {"success": True, "session_id": session_id, "model": model}

    def get_session_model(self, session_id: str) -> Optional[str]:
        """Get the model for a session."""
        return self.session_models.get(session_id)

    def set_session_mode(self, session_id: str, mode: str) -> dict:
        """Set the mode for a session (planning or building)."""
        if mode not in ("planning", "building"):
            return {"success": False, "error": "Mode must be 'planning' or 'building'"}

        if session_id not in self.sessions and session_id not in self.user_session_ids:
            return {"success": False, "error": "Session not found"}

        self.session_modes[session_id] = mode
        logger.info("set_session_mode", session_id=session_id, mode=mode)
        return {"success": True, "session_id": session_id, "mode": mode}

    def get_session_mode(self, session_id: str) -> Optional[str]:
        """Get the mode for a session."""
        return self.session_modes.get(session_id)

    async def switch_mode_and_send(
        self, session_id: str, mode: str, message: str
    ) -> dict:
        """Switch session mode AND send a message in one call.

        Args:
            session_id: The session ID
            mode: Target mode ("planning" or "building")
            message: Message to send after switching mode
        """
        mode_result = self.set_session_mode(session_id, mode)
        if not mode_result.get("success"):
            return mode_result

        send_result = await self._send_message_with_timeout(session_id, message)
        send_result["mode_switched_to"] = mode

        return send_result

    async def set_session_permissions(self, session_id: str, permissions: list) -> dict:
        """Set permissions for a session."""
        if session_id not in self.sessions:
            return {"success": False, "error": "Session not found in manager"}

        try:
            result = await self.oc.update_session(session_id, permission=permissions)
            logger.info(
                "set_session_permissions",
                session_id=session_id,
                permissions=permissions,
            )
            return {
                "success": True,
                "session_id": session_id,
                "permissions": permissions,
            }
        except Exception as e:
            logger.error(
                "set_session_permissions_error", session_id=session_id, error=str(e)
            )
            return {"success": False, "error": str(e)}

    async def wait_for_session(self, session_id: str, duration: int = 50) -> dict:
        """Wait for a session and collect activity.

        Monitors a session for the specified duration, collecting tool calls,
        outputs, and internal reasoning. Returns a summary of activity.

        Args:
            session_id: The session ID to monitor
            duration: Seconds to wait (minimum 30, default 50)

        Returns:
            dict with activity summary including tool calls, outputs, and reasoning.
            If session still active near timeout, includes still_active=True and
            flavor text suggesting read_session_logs.
        """
        duration = max(duration, MIN_WAIT_DURATION)

        start_time = time.time()
        activity = {
            "tool_calls": [],
            "outputs": [],
            "reasoning": [],
            "messages": [],
            "duration_seconds": duration,
            "session_id": session_id,
        }

        seen_message_ids = set()
        check_interval = 2
        near_timeout_returned = False

        while time.time() - start_time < duration:
            try:
                current_messages = await self.oc.list_messages(session_id, limit=50)

                for msg in current_messages:
                    info = msg.get("info", {})
                    message_id = info.get("id")
                    if not message_id or message_id in seen_message_ids:
                        continue

                    seen_message_ids.add(message_id)
                    parts = msg.get("parts", [])
                    text_chunks = [
                        p.get("text", "") for p in parts if p.get("type") == "text"
                    ]

                    activity["messages"].append(
                        {
                            "id": message_id,
                            "role": info.get("role"),
                            "content": "\n".join([t for t in text_chunks if t]).strip()[
                                :200
                            ],
                            "mode": info.get("mode"),
                            "agent": info.get("agent"),
                        }
                    )

                    if info.get("role") == "assistant":
                        for part in parts:
                            part_type = part.get("type")
                            if part_type == "tool":
                                activity["tool_calls"].append(
                                    {
                                        "tool": part.get("tool", "unknown"),
                                        "input": str(
                                            part.get("state", {}).get("input", {})
                                        )[:100],
                                    }
                                )
                            elif part_type == "reasoning":
                                text = part.get("text", "")
                                if len(text) > 10:
                                    activity["reasoning"].append(text[:500])

                elapsed = time.time() - start_time
                if elapsed >= NEAR_TIMEOUT_THRESHOLD:
                    near_timeout_returned = True
                    activity["reasoning"].append(
                        f"Session still active after {int(elapsed)} seconds. "
                        "Use read_session_logs for full output."
                    )
                    break

                await asyncio.sleep(check_interval)
            except Exception as e:
                logger.error(
                    "wait_for_session_error", session_id=session_id, error=str(e)
                )
                activity["error"] = str(e)
                break

        elapsed = time.time() - start_time
        activity["elapsed_seconds"] = int(elapsed)

        summary_parts = []
        if activity["tool_calls"]:
            summary_parts.append(f"Tools called ({len(activity['tool_calls'])}): ")
            for tc in activity["tool_calls"][:5]:
                summary_parts.append(f"  - {tc['tool']}: {tc['input'][:60]}...")

        if activity["reasoning"]:
            summary_parts.append(
                f"\nInternal reasoning ({len(activity['reasoning'])} entries):"
            )
            for r in activity["reasoning"][:3]:
                summary_parts.append(f"  {r[:100]}...")

        activity["summary"] = (
            "\n".join(summary_parts) if summary_parts else "No significant activity"
        )

        if near_timeout_returned:
            activity["still_active"] = True
            activity["flavor_text"] = (
                "*Session still active.* Use `read_session_logs` for detailed output "
                "or `wait_for_session` again to continue monitoring."
            )

        return activity
