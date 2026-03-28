"""Tests for PtyManager."""

import pytest
from unittest.mock import AsyncMock
from pty_manager import PtyManager


class TestPtyManager:
    """Test cases for PtyManager."""

    @pytest.mark.asyncio
    async def test_create_pty(self, pty_manager, mock_opencode_client):
        """Test creating a PTY."""
        result = await pty_manager.create_pty(cwd="/tmp", owner="claude")
        
        assert result["id"] == "pty-1"
        mock_opencode_client.create_pty.assert_called_once_with(cwd="/tmp")
        assert "pty-1" in pty_manager.ptys
        assert pty_manager.ptys["pty-1"].owner == "claude"

    @pytest.mark.asyncio
    async def test_create_pty_default_owner(self, pty_manager, mock_opencode_client):
        """Test creating a PTY with default owner."""
        result = await pty_manager.create_pty()
        
        assert result["id"] == "pty-1"
        assert pty_manager.ptys["pty-1"].owner == "claude"

    @pytest.mark.asyncio
    async def test_resize_pty(self, pty_manager, mock_opencode_client):
        """Test resizing a PTY."""
        await pty_manager.create_pty(owner="claude")
        
        result = await pty_manager.resize_pty("pty-1", cols=120, rows=40)
        
        assert result["success"] is True
        mock_opencode_client.resize_pty.assert_called_once_with("pty-1", 120, 40)

    @pytest.mark.asyncio
    async def test_read_output(self, pty_manager, mock_opencode_client):
        """Test reading PTY output."""
        await pty_manager.create_pty(owner="claude")
        
        result = await pty_manager.read_output("pty-1")
        
        assert result == "test output"
        mock_opencode_client.get_pty_output.assert_called_once_with("pty-1")

    @pytest.mark.asyncio
    async def test_read_output_not_found(self, pty_manager, mock_opencode_client):
        """Test reading PTY output for non-existent PTY."""
        result = await pty_manager.read_output("non-existent-pty")
        
        assert result == ""

    @pytest.mark.asyncio
    async def test_close_pty(self, pty_manager, mock_opencode_client):
        """Test closing a PTY."""
        await pty_manager.create_pty(owner="claude")
        assert "pty-1" in pty_manager.ptys
        
        result = await pty_manager.close_pty("pty-1")
        
        assert result["success"] is True
        assert "pty-1" not in pty_manager.ptys
        mock_opencode_client.close_pty.assert_called_once_with("pty-1")

    @pytest.mark.asyncio
    async def test_list_ptys(self, pty_manager, mock_opencode_client):
        """Test listing all PTYs."""
        await pty_manager.create_pty(cwd="/tmp", owner="claude")
        await pty_manager.create_pty(cwd="/home", owner="user")
        
        result = await pty_manager.list_ptys()
        
        assert len(result) == 2

    def test_get_claude_ptys(self, pty_manager, mock_opencode_client):
        """Test getting PTYs owned by Claude."""
        import asyncio
        asyncio.get_event_loop().run_until_complete(pty_manager.create_pty(owner="claude"))
        asyncio.get_event_loop().run_until_complete(pty_manager.create_pty(owner="user"))
        
        result = pty_manager.get_claude_ptys()
        
        assert "pty-1" in result
        assert len(result) == 1