import httpx
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlencode, urlsplit, urlunsplit
import asyncio
from pydantic import BaseModel
import structlog
import websockets

logger = structlog.get_logger()

OPENCODE_BASE_URL = "http://localhost:9999"
TIMEOUT = 120.0


class Session(BaseModel):
    id: str
    title: str
    slug: str
    directory: Optional[str] = None
    parent_id: Optional[str] = None
    created: int
    updated: int


class Message(BaseModel):
    id: str
    role: str
    content: Optional[str] = None
    parts: list[dict] = []


class OpenCodeClient:
    def __init__(self, base_url: str = OPENCODE_BASE_URL):
        self.base_url = base_url
        self.client = httpx.AsyncClient(base_url=base_url, timeout=TIMEOUT)

    async def close(self):
        await self.client.aclose()

    async def health(self) -> dict:
        resp = await self.client.get("/global/health")
        resp.raise_for_status()
        return resp.json()

    async def list_sessions(self) -> list[Session]:
        resp = await self.client.get("/session")
        resp.raise_for_status()
        data = resp.json()
        sessions = []
        for s in data if isinstance(data, list) else data.get("sessions", []):
            sessions.append(
                Session(
                    id=s.get("id", ""),
                    title=s.get("title", "Untitled"),
                    slug=s.get("slug", ""),
                    directory=s.get("directory"),
                    parent_id=s.get("parentID"),
                    created=s.get("time", {}).get("created", 0),
                    updated=s.get("time", {}).get("updated", 0),
                )
            )
        return sessions

    async def get_session(self, session_id: str) -> dict:
        resp = await self.client.get(f"/session/{session_id}")
        resp.raise_for_status()
        return resp.json()

    async def create_session(
        self,
        title: Optional[str] = None,
        directory: Optional[str] = None,
        permissions: Optional[list] = None,
    ) -> dict:
        payload: dict[str, Any] = {}
        if title:
            payload["title"] = title
        if directory:
            payload["directory"] = directory
        if permissions:
            payload["permission"] = permissions
        resp = await self.client.post("/session", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def delete_session(self, session_id: str) -> dict:
        resp = await self.client.delete(f"/session/{session_id}")
        resp.raise_for_status()
        result = self._parse_response_body(resp)
        if result is None:
            result = True
        return self._coerce_action_result(
            result,
            flag_key="deleted",
            id_key="session_id",
            id_value=session_id,
        )

    async def get_session_status(
        self, directory: Optional[str] = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"directory": directory} if directory else {}
        resp = await self.client.get("/session/status", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _parse_response_body(resp: httpx.Response) -> Any:
        if not resp.text:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    @staticmethod
    def _coerce_action_result(
        result: Any,
        *,
        flag_key: str,
        id_key: Optional[str] = None,
        id_value: Optional[str] = None,
    ) -> dict:
        if isinstance(result, dict):
            return result

        success = bool(result)
        payload: dict[str, Any] = {"success": success, flag_key: success}

        if id_key and id_value:
            payload[id_key] = id_value

        if result is not None and not isinstance(result, bool):
            payload["result"] = result

        return payload

    def _build_model_payload(self, model: Optional[str]) -> Optional[dict]:
        if not model:
            return None
        if "/" in model:
            provider_id, model_id = model.split("/", 1)
            if provider_id and model_id:
                return {"providerID": provider_id, "modelID": model_id}
        return {"providerID": model}

    async def send_message(
        self,
        session_id: str,
        prompt: str,
        model: Optional[str] = None,
        agent: str = "build",
        timeout: float = TIMEOUT,
        no_reply: Optional[bool] = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "parts": [{"type": "text", "text": prompt}],
            "agent": agent,
        }

        model_payload = self._build_model_payload(model)
        if model_payload:
            payload["model"] = model_payload

        if no_reply is not None:
            payload["noReply"] = no_reply

        resp = await self.client.post(
            f"/session/{session_id}/message",
            json=payload,
            timeout=httpx.Timeout(timeout, connect=10.0),
        )
        resp.raise_for_status()
        if not resp.text:
            return {}
        try:
            return resp.json()
        except ValueError:
            logger.warning("send_message_non_json_response", session_id=session_id)
            return {}

    async def prompt_async(
        self,
        session_id: str,
        prompt: str,
        model: Optional[str] = None,
        agent: str = "build",
    ) -> dict:
        payload: dict[str, Any] = {
            "parts": [{"type": "text", "text": prompt}],
            "agent": agent,
        }

        model_payload = self._build_model_payload(model)
        if model_payload:
            payload["model"] = model_payload

        resp = await self.client.post(
            f"/session/{session_id}/prompt_async",
            json=payload,
            timeout=httpx.Timeout(TIMEOUT, connect=10.0),
        )
        resp.raise_for_status()
        return {
            "accepted": resp.status_code in (200, 202, 204),
            "status_code": resp.status_code,
        }

    async def stream_message(
        self,
        session_id: str,
        prompt: str,
        model: Optional[str] = None,
        agent: str = "build",
    ) -> AsyncIterator[dict]:
        message = await self.send_message(
            session_id=session_id,
            prompt=prompt,
            model=model,
            agent=agent,
        )
        for part in message.get("parts", []):
            yield {"type": "part", "part": part}
        yield {"type": "done", "message": message}

    async def run_shell(
        self,
        session_id: str,
        command: str,
        model: Optional[str] = None,
        agent: str = "build",
        directory: Optional[str] = None,
        timeout: float = TIMEOUT,
    ) -> dict:
        payload: dict[str, Any] = {
            "agent": agent,
            "command": command,
        }

        model_payload = self._build_model_payload(model)
        if model_payload:
            payload["model"] = model_payload

        params: dict[str, Any] = {"directory": directory} if directory else {}

        resp = await self.client.post(
            f"/session/{session_id}/shell",
            params=params,
            json=payload,
            timeout=httpx.Timeout(timeout, connect=10.0),
        )
        resp.raise_for_status()
        result = self._parse_response_body(resp)
        if isinstance(result, dict):
            return result
        return {"success": bool(result), "result": result}

    async def list_messages(
        self, session_id: str, limit: int = 50, directory: Optional[str] = None
    ) -> list[dict]:
        params: dict[str, Any] = {
            "limit": limit,
            **({"directory": directory} if directory else {}),
        }
        resp = await self.client.get(f"/session/{session_id}/message", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    async def list_permissions(self, directory: Optional[str] = None) -> list[dict]:
        params: dict[str, Any] = {"directory": directory} if directory else {}
        resp = await self.client.get("/permission", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    async def reply_permission(
        self,
        request_id: str,
        reply: str,
        message: Optional[str] = None,
        directory: Optional[str] = None,
    ) -> dict:
        params: dict[str, Any] = {"directory": directory} if directory else {}
        payload: dict[str, Any] = {"reply": reply}
        if message:
            payload["message"] = message

        resp = await self.client.post(
            f"/permission/{request_id}/reply",
            params=params,
            json=payload,
        )
        resp.raise_for_status()
        result = self._parse_response_body(resp)
        if result is None:
            result = True
        coerced = self._coerce_action_result(
            result,
            flag_key="replied",
            id_key="request_id",
            id_value=request_id,
        )
        coerced["reply"] = reply
        return coerced

    async def list_questions(self, directory: Optional[str] = None) -> list[dict]:
        params: dict[str, Any] = {"directory": directory} if directory else {}
        resp = await self.client.get("/question", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    async def reply_question(
        self,
        request_id: str,
        answers: list[list[str]],
        directory: Optional[str] = None,
    ) -> dict:
        params: dict[str, Any] = {"directory": directory} if directory else {}
        resp = await self.client.post(
            f"/question/{request_id}/reply",
            params=params,
            json={"answers": answers},
        )
        resp.raise_for_status()
        result = self._parse_response_body(resp)
        if result is None:
            result = True
        coerced = self._coerce_action_result(
            result,
            flag_key="answered",
            id_key="request_id",
            id_value=request_id,
        )
        coerced["answers"] = answers
        return coerced

    async def reject_question(
        self, request_id: str, directory: Optional[str] = None
    ) -> dict:
        params: dict[str, Any] = {"directory": directory} if directory else {}
        resp = await self.client.post(
            f"/question/{request_id}/reject",
            params=params,
        )
        resp.raise_for_status()
        result = self._parse_response_body(resp)
        if result is None:
            result = True
        return self._coerce_action_result(
            result,
            flag_key="rejected",
            id_key="request_id",
            id_value=request_id,
        )

    async def get_message(
        self, session_id: str, message_id: str, directory: Optional[str] = None
    ) -> dict:
        params: dict[str, Any] = {"directory": directory} if directory else {}
        resp = await self.client.get(
            f"/session/{session_id}/message/{message_id}", params=params
        )
        resp.raise_for_status()
        return resp.json()

    async def abort_message(self, session_id: str) -> dict:
        resp = await self.client.post(f"/session/{session_id}/abort")
        resp.raise_for_status()
        result = self._parse_response_body(resp)
        if result is None:
            result = True
        return self._coerce_action_result(
            result,
            flag_key="aborted",
            id_key="session_id",
            id_value=session_id,
        )

    async def fork_session(self, session_id: str) -> dict:
        resp = await self.client.post(f"/session/{session_id}/fork")
        resp.raise_for_status()
        return resp.json()

    async def create_pty(
        self,
        cwd: Optional[str] = None,
        command: Optional[str] = None,
        args: Optional[list[str]] = None,
        title: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
    ) -> dict:
        payload: dict[str, Any] = {}
        if cwd:
            payload["cwd"] = cwd
        if command:
            payload["command"] = command
        if args is not None:
            payload["args"] = args
        if title:
            payload["title"] = title
        if env:
            payload["env"] = env
        resp = await self.client.post("/pty", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def list_ptys(self, directory: Optional[str] = None) -> list[dict]:
        params: dict[str, Any] = {"directory": directory} if directory else {}
        resp = await self.client.get("/pty", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    async def get_pty(self, pty_id: str, directory: Optional[str] = None) -> dict:
        params: dict[str, Any] = {"directory": directory} if directory else {}
        resp = await self.client.get(f"/pty/{pty_id}", params=params)
        resp.raise_for_status()
        return resp.json()

    async def update_pty(
        self,
        pty_id: str,
        title: Optional[str] = None,
        rows: Optional[int] = None,
        cols: Optional[int] = None,
        directory: Optional[str] = None,
    ) -> dict:
        params: dict[str, Any] = {"directory": directory} if directory else {}
        payload: dict[str, Any] = {}

        if title is not None:
            payload["title"] = title

        if rows is not None or cols is not None:
            if rows is None or cols is None:
                raise ValueError(
                    "rows and cols must both be set when updating PTY size"
                )
            payload["size"] = {"rows": rows, "cols": cols}

        if not payload:
            return await self.get_pty(pty_id, directory=directory)

        resp = await self.client.put(f"/pty/{pty_id}", params=params, json=payload)
        resp.raise_for_status()
        return resp.json()

    async def write_pty(
        self, pty_id: str, data: str, directory: Optional[str] = None
    ) -> dict:
        result = await self._pty_socket_exchange(
            pty_id, directory=directory, write_data=data
        )
        return {
            "success": True,
            "pty_id": pty_id,
            "output": result["output"],
        }

    async def resize_pty(self, pty_id: str, cols: int, rows: int) -> dict:
        resp = await self.client.put(
            f"/pty/{pty_id}", json={"cols": cols, "rows": rows}
        )
        resp.raise_for_status()
        return resp.json()

    async def get_pty_output(self, pty_id: str) -> dict:
        result = await self._pty_socket_exchange(pty_id)
        return {"data": result["output"], "pty_id": pty_id}

    async def close_pty(self, pty_id: str) -> dict:
        resp = await self.client.delete(f"/pty/{pty_id}")
        resp.raise_for_status()
        result = self._parse_response_body(resp)
        if result is None:
            result = True
        return self._coerce_action_result(
            result,
            flag_key="closed",
            id_key="pty_id",
            id_value=pty_id,
        )

    async def update_session(self, session_id: str, **kwargs) -> dict:
        """Update session properties.

        Args:
            session_id: The session ID
            **kwargs: Properties to update (title, permission, etc.)
        """
        resp = await self.client.patch(f"/session/{session_id}", json=kwargs)
        resp.raise_for_status()
        return resp.json()

    def _pty_websocket_url(self, pty_id: str, directory: Optional[str] = None) -> str:
        parts = urlsplit(self.base_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        path = f"/pty/{pty_id}/connect"
        query = urlencode({"directory": directory}) if directory else ""
        return urlunsplit((scheme, parts.netloc, path, query, ""))

    @staticmethod
    async def _read_pty_frames(
        ws, *, idle_timeout: float = 0.25, max_frames: int = 64
    ) -> str:
        chunks: list[str] = []

        for _ in range(max_frames):
            try:
                frame = await asyncio.wait_for(ws.recv(), timeout=idle_timeout)
            except asyncio.TimeoutError:
                break

            if isinstance(frame, bytes):
                if frame.startswith(b"\x00{"):
                    continue
                text = frame.decode("utf-8", "ignore")
                if text:
                    chunks.append(text)
                continue

            if frame:
                chunks.append(frame)

        return "".join(chunks)

    async def _pty_socket_exchange(
        self,
        pty_id: str,
        *,
        directory: Optional[str] = None,
        write_data: Optional[str] = None,
    ) -> dict[str, Any]:
        uri = self._pty_websocket_url(pty_id, directory=directory)
        async with websockets.connect(uri, max_size=2**20) as ws:
            await self._read_pty_frames(ws)
            if write_data is not None:
                await ws.send(write_data)
                output = await self._read_pty_frames(ws, idle_timeout=0.5)
            else:
                output = await self._read_pty_frames(ws)

        return {"success": True, "pty_id": pty_id, "output": output}
