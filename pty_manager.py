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

    async def create_pty(
        self,
        cwd: Optional[str] = None,
        owner: str = "claude",
        command: Optional[str] = None,
        args: Optional[list[str]] = None,
        title: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
    ) -> dict:
        """Create a new PTY for Claude."""
        async with self._lock:
            result = await self.oc.create_pty(
                cwd=cwd,
                command=command,
                args=args,
                title=title,
                env=env,
            )
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
            try:
                result = await self.oc.get_pty_output(pty_id)
                if pty_id in self.ptys:
                    self.ptys[pty_id].touch()
                    cached = self.ptys[pty_id].buffer
                    output = result.get("data", "") or cached
                    self.ptys[pty_id].buffer = ""
                    return output
                return result.get("data", "")
            except Exception as e:
                logger.error("pty_read_error", pty_id=pty_id, error=str(e))
                return ""

    async def get_pty(self, pty_id: str) -> dict:
        """Get details for a specific PTY."""
        async with self._lock:
            result = await self.oc.get_pty(pty_id)
            if pty_id in self.ptys:
                self.ptys[pty_id].touch()
            return result

    async def list_remote_ptys(self) -> list[dict]:
        """List all PTYs currently known by OpenCode."""
        async with self._lock:
            remote = await self.oc.list_ptys()
            for pty in remote:
                pty_id = pty.get("id")
                if pty_id in self.ptys:
                    pty["owner"] = self.ptys[pty_id].owner
            return remote

    async def update_pty(
        self,
        pty_id: str,
        title: Optional[str] = None,
        cols: Optional[int] = None,
        rows: Optional[int] = None,
    ) -> dict:
        """Update PTY metadata like title and terminal size."""
        async with self._lock:
            result = await self.oc.update_pty(
                pty_id=pty_id,
                title=title,
                rows=rows,
                cols=cols,
            )
            if pty_id in self.ptys:
                self.ptys[pty_id].touch()
            return result

    async def send_input(self, pty_id: str, data: str) -> dict:
        """Send input to PTY. Note: OpenCode may not support direct input via REST."""
        async with self._lock:
            if pty_id not in self.ptys:
                return {"error": "PTY not found"}
            self.ptys[pty_id].touch()
            result = await self.oc.write_pty(pty_id=pty_id, data=data)
            self.ptys[pty_id].buffer = result.get("output", "")
            return result

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
