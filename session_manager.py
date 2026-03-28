import asyncio
import time
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
        permissions: Optional[list] = None
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
                title=title,
                directory=directory,
                permissions=permissions
            )
            session_id = result.get("id")
            if session_id:
                info = SessionInfo(
                    session_id=session_id,
                    title=title or "Untitled",
                    owner=owner,
                    created_at=datetime.now()
                )
                self.sessions[session_id] = info
                if owner == "claude":
                    self.claude_session_ids.add(session_id)
                self.session_modes[session_id] = mode
                logger.info("created_session", session_id=session_id, owner=owner, mode=mode)

                if initial_message:
                    send_result = await self._send_message_with_timeout(
                        session_id, initial_message
                    )
                    result["initial_response"] = send_result

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
                    created_at=datetime.now()
                )
                self.sessions[new_id] = info
                self.claude_session_ids.add(new_id)
                logger.info("forked_session", original=session_id, forked=new_id)
            return result

    def _agent_for_session_mode(self, session_id: str) -> str:
        mode = self.session_modes.get(session_id, "planning")
        return "plan" if mode == "planning" else "build"

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
                tool_calls.append({
                    "tool": part.get("tool", "unknown"),
                    "state": part.get("state", {}),
                })

        info = message.get("info", {})
        return {
            "text": "\n".join(text_chunks).strip(),
            "tool_calls": tool_calls,
            "reasoning": reasoning_chunks,
            "parts": parts,
            "info": info,
            "completed": bool(info.get("time", {}).get("completed")),
        }

    async def _latest_assistant_snapshot(self, session_id: str, limit: int = 20) -> Optional[dict]:
        try:
            messages = await self.oc.list_messages(session_id, limit=limit)
        except Exception as e:
            logger.warning("list_messages_failed", session_id=session_id, error=str(e))
            return None

        for msg in reversed(messages):
            info = msg.get("info", {})
            if info.get("role") == "assistant":
                return msg
        return None

    async def _send_message_with_timeout(
        self,
        session_id: str,
        prompt: str,
        model: Optional[str] = None,
        timeout: int = TOOL_TIMEOUT
    ) -> dict:
        """Send message with near-timeout handling.

        Returns full result if OpenCode responds in time.
        Returns partial result + reasoning + still_active=True if nearing timeout.
        """
        if model is None:
            model = self.session_models.get(session_id)

        agent = self._agent_for_session_mode(session_id)
        request_timeout = max(1, min(timeout, NEAR_TIMEOUT_THRESHOLD))
        start_time = time.time()

        try:
            response = await self.oc.send_message(
                session_id=session_id,
                prompt=prompt,
                model=model,
                agent=agent,
                timeout=request_timeout,
            )
            elapsed = int(time.time() - start_time)

            if response and response.get("parts"):
                extracted = self._extract_message_activity(response)
                return {
                    "text": extracted["text"],
                    "tool_calls": extracted["tool_calls"],
                    "reasoning": extracted["reasoning"],
                    "completed": extracted["completed"] or True,
                    "elapsed_seconds": elapsed,
                    "agent": agent,
                    "mode": self.session_modes.get(session_id, "planning"),
                }

            # Empty response can happen when the message is accepted but still processing.
            latest = await self._latest_assistant_snapshot(session_id)
            if latest:
                extracted = self._extract_message_activity(latest)
                return {
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
                }

            return {
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
            }

        except httpx.TimeoutException:
            elapsed = int(time.time() - start_time)
            latest = await self._latest_assistant_snapshot(session_id)
            if latest:
                extracted = self._extract_message_activity(latest)
                return {
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
                }

            return {
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
            }

        except Exception as e:
            logger.error("send_message_error", session_id=session_id, error=str(e))
            elapsed = int(time.time() - start_time)
            return {
                "error": str(e),
                "elapsed_seconds": elapsed,
                "agent": agent,
                "mode": self.session_modes.get(session_id, "planning"),
            }

    async def send_message(self, session_id: str, prompt: str, model: Optional[str] = None) -> dict:
        """Send a message to a session with timeout handling."""
        return await self._send_message_with_timeout(session_id, prompt, model=model)

    async def send_message_stream(self, session_id: str, prompt: str, stream: bool = True, model: Optional[str] = None):
        """Send a message to a session (legacy stream support)."""
        if model is None:
            model = self.session_models.get(session_id)
        agent = self._agent_for_session_mode(session_id)
        if stream:
            return self.oc.stream_message(session_id, prompt, model=model, agent=agent)
        else:
            return await self.oc.send_message(session_id, prompt, model=model, agent=agent)

    async def abort_message(self, session_id: str) -> dict:
        """Abort ongoing message generation."""
        return await self.oc.abort_message(session_id)

    async def get_session(self, session_id: str) -> dict:
        """Get full session state."""
        return await self.oc.get_session(session_id)

    async def list_sessions(self, cursor: Optional[str] = None, limit: int = 10) -> dict:
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
                    logger.warning("list_recent_messages_failed", session_id=sid, error=str(e))
                    messages = []

                for msg in messages[-3:]:
                    info = msg.get("info", {})
                    parts = msg.get("parts", [])
                    text_chunks = [p.get("text", "") for p in parts if p.get("type") == "text"]
                    recent_messages.append({
                        "role": info.get("role"),
                        "content": "\n".join([t for t in text_chunks if t]).strip()[:300],
                        "parts_count": len(parts),
                        "message_id": info.get("id"),
                    })

                result.append({
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
                })
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

    async def read_session_logs(self, session_id: str, mode: Literal["summary", "full"] = "summary") -> dict:
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
                        parsed_parts.append({
                            "type": "text",
                            "text": part.get("text", "")[:500],
                        })
                    elif part_type == "tool_use":
                        parsed_parts.append({
                            "type": "tool_use",
                            "tool": part.get("name", "unknown"),
                            "input": str(part.get("input", {}))[:200],
                        })
                    elif part_type == "tool_result":
                        parsed_parts.append({
                            "type": "tool_result",
                            "content": str(part.get("content", ""))[:200],
                        })
                    elif part_type == "reasoning":
                        parsed_parts.append({
                            "type": "reasoning",
                            "text": part.get("text", "")[:500],
                        })
                    elif part_type == "tool":
                        parsed_parts.append({
                            "type": "tool",
                            "tool": part.get("tool", "unknown"),
                            "state": part.get("state", {}),
                        })
                    else:
                        parsed_parts.append({
                            "type": part_type,
                        })

                text_chunks = [p.get("text", "") for p in parts if p.get("type") == "text"]

                parsed_messages.append({
                    "id": info.get("id", ""),
                    "role": info.get("role", ""),
                    "content": "\n".join([t for t in text_chunks if t]).strip()[:500] if text_chunks else None,
                    "mode": info.get("mode"),
                    "agent": info.get("agent"),
                    "created": info.get("time", {}).get("created"),
                    "completed": info.get("time", {}).get("completed"),
                    "parts": parsed_parts,
                })

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
        self,
        session_id: str,
        mode: str,
        message: str
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
            logger.info("set_session_permissions", session_id=session_id, permissions=permissions)
            return {"success": True, "session_id": session_id, "permissions": permissions}
        except Exception as e:
            logger.error("set_session_permissions_error", session_id=session_id, error=str(e))
            return {"success": False, "error": str(e)}

    async def wait_for_session(
        self,
        session_id: str,
        duration: int = 50
    ) -> dict:
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
                    text_chunks = [p.get("text", "") for p in parts if p.get("type") == "text"]

                    activity["messages"].append({
                        "id": message_id,
                        "role": info.get("role"),
                        "content": "\n".join([t for t in text_chunks if t]).strip()[:200],
                        "mode": info.get("mode"),
                        "agent": info.get("agent"),
                    })

                    if info.get("role") == "assistant":
                        for part in parts:
                            part_type = part.get("type")
                            if part_type == "tool":
                                activity["tool_calls"].append({
                                    "tool": part.get("tool", "unknown"),
                                    "input": str(part.get("state", {}).get("input", {}))[:100],
                                })
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
                logger.error("wait_for_session_error", session_id=session_id, error=str(e))
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
            summary_parts.append(f"\nInternal reasoning ({len(activity['reasoning'])} entries):")
            for r in activity["reasoning"][:3]:
                summary_parts.append(f"  {r[:100]}...")

        activity["summary"] = "\n".join(summary_parts) if summary_parts else "No significant activity"

        if near_timeout_returned:
            activity["still_active"] = True
            activity["flavor_text"] = (
                "*Session still active.* Use `read_session_logs` for detailed output "
                "or `wait_for_session` again to continue monitoring."
            )

        return activity
