"""Live MCP Registry v2 API (M44-03..M44-07)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from hiveplane.api.deps import get_mcp_registry
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.mcp.models import (
    McpServerEndpoint,
    McpServerRecord,
    McpToolRecord,
    ToolStatus,
)
from hiveplane.mcp.registry import (
    McpRegistry,
    ToolNotAvailableError,
    UnknownServerError,
    UnknownToolError,
)

router = APIRouter(tags=["mcp"])

RegistryDep = Annotated[McpRegistry, Depends(get_mcp_registry)]


class McpServerRequest(BaseModel):
    """Request body to register/connect an MCP server."""

    model_config = ConfigDict(extra="forbid")

    endpoint: McpServerEndpoint


class McpOnboardRequest(BaseModel):
    """Request body to onboard a discovered tool with a trust level."""

    model_config = ConfigDict(extra="forbid")

    trust_level: ToolTrustLevel
    actor: str = Field(min_length=1)


@router.post("/mcp/servers", response_model=McpServerRecord, status_code=status.HTTP_201_CREATED)
def connect_server(request: McpServerRequest, registry: RegistryDep) -> McpServerRecord:
    """Connect to an MCP server and discover its tools."""
    try:
        return registry.connect(request.endpoint)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/mcp/servers", response_model=list[McpServerRecord])
def list_servers(registry: RegistryDep) -> list[McpServerRecord]:
    """List registered MCP servers."""
    return registry.list_servers()


@router.post("/mcp/servers/{server_id}/refresh", response_model=list[McpToolRecord])
def refresh_server(server_id: str, registry: RegistryDep) -> list[McpToolRecord]:
    """Re-run dynamic discovery for a server."""
    try:
        return registry.refresh(server_id)
    except UnknownServerError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/mcp/tools", response_model=list[McpToolRecord])
def list_tools(
    registry: RegistryDep,
    status_filter: Annotated[ToolStatus | None, Query(alias="status")] = None,
    trust_level: Annotated[ToolTrustLevel | None, Query()] = None,
) -> list[McpToolRecord]:
    """List discovered/onboarded tools, optionally filtered."""
    return registry.list_tools(status=status_filter, trust_level=trust_level)


@router.get("/mcp/tools/{tool_id}", response_model=McpToolRecord)
def show_tool(tool_id: str, registry: RegistryDep) -> McpToolRecord:
    """Show one tool by its stable id."""
    try:
        return registry.get_tool(tool_id)
    except UnknownToolError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/mcp/tools/{tool_id}/onboard", response_model=McpToolRecord)
def onboard_tool(
    tool_id: str, request: McpOnboardRequest, registry: RegistryDep
) -> McpToolRecord:
    """Onboard a discovered tool and assign its trust level."""
    try:
        return registry.onboard(
            tool_id, trust_level=request.trust_level, actor=request.actor
        )
    except UnknownToolError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ToolNotAvailableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.delete("/mcp/tools/{tool_id}", response_model=McpToolRecord)
def remove_tool(tool_id: str, registry: RegistryDep) -> McpToolRecord:
    """Retire a tool; its stable id is never reused."""
    try:
        return registry.remove(tool_id)
    except UnknownToolError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
