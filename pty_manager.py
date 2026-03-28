import asyncio
from datetime import datetime
from typing import Optional
import structlog
from opencode_client import OpenCodeClient

logger = structlog.get_logger()


class PtyInfo:
    def __init__(self, pty_id: str, owner: str, cwd: Optional[str] = None):
        self.id = pty_id
        self.owner = owner
        self.cwd = cwd
        self.created_at = datetime.now()
        self.last_used = datetime.now()
        self.buffer = ""

    def touch(self):
        self.last_used = datetime.now()


class PtyManager:
    def __init__(self, oc_client: OpenCodeClient):
        self.oc = oc_client
        self.ptys: dict[str, PtyInfo] = {}
        self._lock = asyncio.Lock()

    async def create_pty(self, cwd: Optional[str] = None, owner: str = "claude") -> dict:
        """Create a new PTY for Claude."""
        async with self._lock:
            result = await self.oc.create_pty(cwd=cwd)
            pty_id = result.get("id")
            if pty_id:
                info = PtyInfo(pty_id=pty_id, owner=owner, cwd=cwd)
                self.ptys[pty_id] = info
                logger.info("created_pty", pty_id=pty_id, owner=owner)
            return result

    async def resize_pty(self, pty_id: str, cols: int, rows: int) -> dict:
        """Resize a PTY terminal."""
        async with self._lock:
            result = await self.oc.resize_pty(pty_id, cols, rows)
            if pty_id in self.ptys:
                self.ptys[pty_id].touch()
            return result

    async def read_output(self, pty_id: str) -> str:
        """Read current PTY output buffer."""
        async with self._lock:
            if pty_id not in self.ptys:
                return ""
            try:
                result = await self.oc.get_pty_output(pty_id)
                self.ptys[pty_id].touch()
                return result.get("data", "")
            except Exception as e:
                logger.error("pty_read_error", pty_id=pty_id, error=str(e))
                return ""

    async def send_input(self, pty_id: str, data: str) -> dict:
        """Send input to PTY. Note: OpenCode may not support direct input via REST."""
        async with self._lock:
            if pty_id not in self.ptys:
                return {"error": "PTY not found"}
            self.ptys[pty_id].touch()
            return await self.oc.client.post(
                f"/pty/{pty_id}",
                json={"input": data}
            )

    async def close_pty(self, pty_id: str) -> dict:
        """Close a PTY session."""
        async with self._lock:
            result = await self.oc.close_pty(pty_id)
            if pty_id in self.ptys:
                del self.ptys[pty_id]
                logger.info("closed_pty", pty_id=pty_id)
            return result

    async def list_ptys(self) -> list[dict]:
        """List all active PTYs."""
        async with self._lock:
            return [
                {
                    "id": info.id,
                    "owner": info.owner,
                    "cwd": info.cwd,
                    "created_at": info.created_at.isoformat(),
                    "last_used": info.last_used.isoformat(),
                }
                for info in self.ptys.values()
            ]

    def get_claude_ptys(self) -> list[str]:
        """Get PTY IDs owned by Claude."""
        return [pty_id for pty_id, info in self.ptys.items() if info.owner == "claude"]