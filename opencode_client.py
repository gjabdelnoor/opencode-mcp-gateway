import httpx
from typing import Any, AsyncIterator, Optional
from pydantic import BaseModel
import structlog

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
            sessions.append(Session(
                id=s.get("id", ""),
                title=s.get("title", "Untitled"),
                slug=s.get("slug", ""),
                directory=s.get("directory"),
                parent_id=s.get("parentID"),
                created=s.get("time", {}).get("created", 0),
                updated=s.get("time", {}).get("updated", 0),
            ))
        return sessions

    async def get_session(self, session_id: str) -> dict:
        resp = await self.client.get(f"/session/{session_id}")
        resp.raise_for_status()
        return resp.json()

    async def create_session(self, title: Optional[str] = None, directory: Optional[str] = None, permissions: Optional[list] = None) -> dict:
        payload = {}
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
        return resp.json()

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
        payload = {
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
            timeout=httpx.Timeout(timeout, connect=10.0)
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
        payload = {
            "parts": [{"type": "text", "text": prompt}],
            "agent": agent,
        }

        model_payload = self._build_model_payload(model)
        if model_payload:
            payload["model"] = model_payload

        resp = await self.client.post(
            f"/session/{session_id}/prompt_async",
            json=payload,
            timeout=httpx.Timeout(TIMEOUT, connect=10.0)
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

    async def list_messages(self, session_id: str, limit: int = 50, directory: Optional[str] = None) -> list[dict]:
        params = {"limit": limit}
        if directory:
            params["directory"] = directory
        resp = await self.client.get(f"/session/{session_id}/message", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    async def get_message(self, session_id: str, message_id: str, directory: Optional[str] = None) -> dict:
        params = {}
        if directory:
            params["directory"] = directory
        resp = await self.client.get(f"/session/{session_id}/message/{message_id}", params=params)
        resp.raise_for_status()
        return resp.json()

    async def abort_message(self, session_id: str) -> dict:
        resp = await self.client.post(f"/session/{session_id}/abort")
        resp.raise_for_status()
        return resp.json()

    async def fork_session(self, session_id: str) -> dict:
        resp = await self.client.post(f"/session/{session_id}/fork")
        resp.raise_for_status()
        return resp.json()

    async def create_pty(self, cwd: Optional[str] = None) -> dict:
        payload = {}
        if cwd:
            payload["cwd"] = cwd
        resp = await self.client.post("/pty", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def resize_pty(self, pty_id: str, cols: int, rows: int) -> dict:
        resp = await self.client.put(
            f"/pty/{pty_id}",
            json={"cols": cols, "rows": rows}
        )
        resp.raise_for_status()
        return resp.json()

    async def get_pty_output(self, pty_id: str) -> dict:
        resp = await self.client.get(f"/pty/{pty_id}")
        resp.raise_for_status()
        return resp.json()

    async def close_pty(self, pty_id: str) -> dict:
        resp = await self.client.delete(f"/pty/{pty_id}")
        resp.raise_for_status()
        return resp.json()

    async def update_session(self, session_id: str, **kwargs) -> dict:
        """Update session properties.
        
        Args:
            session_id: The session ID
            **kwargs: Properties to update (title, permission, etc.)
        """
        resp = await self.client.patch(f"/session/{session_id}", json=kwargs)
        resp.raise_for_status()
        return resp.json()
