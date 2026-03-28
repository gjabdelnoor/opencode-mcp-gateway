import httpx
import json
import sse_starlette.sse as sse
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

    async def create_session(self, title: Optional[str] = None, directory: Optional[str] = None) -> dict:
        payload = {}
        if title:
            payload["title"] = title
        if directory:
            payload["directory"] = directory
        resp = await self.client.post("/session", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def delete_session(self, session_id: str) -> dict:
        resp = await self.client.delete(f"/session/{session_id}")
        resp.raise_for_status()
        return resp.json()

    async def send_message(self, session_id: str, prompt: str, model: Optional[str] = None) -> dict:
        payload = {
            "parts": [{"type": "text", "text": prompt}],
            "agent": "default",
        }
        if model:
            payload["model"] = {"providerID": model}

        resp = await self.client.post(
            f"/session/{session_id}/message",
            json=payload,
            timeout=httpx.Timeout(TIMEOUT, connect=10.0)
        )
        resp.raise_for_status()
        return resp.json()

    async def stream_message(self, session_id: str, prompt: str, model: Optional[str] = None) -> AsyncIterator[dict]:
        payload = {
            "parts": [{"type": "text", "text": prompt}],
            "agent": "default",
        }
        if model:
            payload["model"] = {"providerID": model}
        
        async with self.client.stream(
            "POST",
            f"/session/{session_id}/message",
            json=payload,
            timeout=httpx.Timeout(TIMEOUT, connect=10.0)
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    try:
                        data = json.loads(line[5:].strip())
                        yield data
                    except json.JSONDecodeError:
                        continue

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