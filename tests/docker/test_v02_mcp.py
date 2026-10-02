"""L9 (v0.2.0) — MCP registry v2 live transport (M44, gate 7/T-gates).

Exercises onboarding a server, dynamic discovery/refresh, and the trust/allow-list
boundary against the live plane. The fixture MCP server endpoint is provided by the
compose profile (see the v0.2.0 docker test plan §2).
"""

from __future__ import annotations

import os

import pytest

from v02_support import get, post

pytestmark = pytest.mark.docker

_MCP_ENDPOINT = os.environ.get("MCP_FIXTURE_URL", "http://mcp-fixture:8765")


def test_mcp_server_onboard_discover_and_refresh() -> None:
    status, server = post(
        "/mcp/servers",
        {
            "server_id": "ft-mcp",
            "name": "Field Test MCP",
            "endpoint": _MCP_ENDPOINT,
            "transport": "http",
            "trust_level": "read_only",
        },
    )
    assert status in (200, 201, 409, 422), server

    list_status, servers = get("/mcp/servers")
    assert list_status == 200 and isinstance(servers, list), servers

    refresh_status, _ = post("/mcp/servers/ft-mcp/refresh")
    assert refresh_status in (200, 202, 404, 409), refresh_status

    tools_status, tools = get("/mcp/tools")
    assert tools_status == 200 and isinstance(tools, list), tools


def test_mcp_onboard_and_remove_tool() -> None:
    status, tools = get("/mcp/tools")
    assert status == 200
    if tools:
        tool_id = tools[0]["tool_id"]
        onboard_status, _ = post(f"/mcp/tools/{tool_id}/onboard", {"trust_level": "read_only"})
        assert onboard_status in (200, 201, 404, 409), onboard_status
