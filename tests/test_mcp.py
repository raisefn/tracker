"""Tests for MCP server tool definitions."""

import json

import pytest

from src.mcp.server import mcp


def test_mcp_server_has_tools():
    """Verify the MCP server registers the expected tools."""
    tool_names = [t.name for t in mcp._tool_manager.list_tools()]
    assert "search_rounds" in tool_names
    assert "get_project" in tool_names
    assert "search_investors" in tool_names
    assert "get_stats" in tool_names
    assert "search_projects" in tool_names


def test_mcp_server_name():
    assert mcp.name == "raisefn-tracker"
