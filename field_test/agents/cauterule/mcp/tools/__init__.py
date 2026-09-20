"""MCP tool implementations for CauterRule."""

from __future__ import annotations

from cauterule.mcp.tools.get_rule import get_rule
from cauterule.mcp.tools.list_rules import list_rules
from cauterule.mcp.tools.matching import get_matching_rules
from cauterule.mcp.tools.report_failure import report_failure

__all__ = [
    "get_matching_rules",
    "get_rule",
    "list_rules",
    "report_failure",
]
