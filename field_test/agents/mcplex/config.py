"""
MCPlex configuration models and loader.

Defines the Pydantic schemas for config.yaml and handles file loading
with structured error reporting.  Connector type is validated via an
Enum so a typo like ``type: htt`` is caught immediately rather than
silently dropping the connector's tools.
"""

import logging
import os
import re
from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

_ENV_VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-(.*?))?\}")
"""Matches ${VAR} and ${VAR:-default} patterns for env-var interpolation."""


class ConnectorType(str, Enum):
    """Valid connector types."""

    http = "http"


class HttpToolConfig(BaseModel):
    """HTTP-proxy specific configuration for a single tool.

    Fields
    ------
    method : str
        HTTP verb (GET, POST, etc.).
    path : str
        URL path relative to the connector's ``base_url``.
    param_mapping : dict[str, str]
        Maps MCP argument names → backend API parameter names.
    headers : dict[str, str]
        Static headers injected on every request to this tool.
    """

    method: str = "GET"
    path: str
    param_mapping: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)


class ToolDef(BaseModel):
    """A single MCP tool exposed to the agent."""

    name: str
    description: str
    parameters: dict
    returns: dict = Field(default_factory=dict)
    http: HttpToolConfig | None = None


class ConnectorDef(BaseModel):
    """A backend service that provides one or more tools."""

    name: str
    type: ConnectorType = ConnectorType.http
    base_url: str = ""

    @model_validator(mode="after")
    def _require_base_url_for_http(self):
        if self.type == ConnectorType.http and not self.base_url:
            raise ValueError(
                f"Connector {self.name!r} is type 'http' but has no base_url. "
                "Set base_url to the backend API root URL."
            )
        return self

    tools: list[ToolDef]


class Config(BaseModel):
    """Top-level config holding all connector definitions."""

    connectors: list[ConnectorDef]
    rate_limits: dict[str, dict] = Field(default_factory=dict)
    """Per-tool rate limits.  Keys are tool names; values have
    ``max_requests`` (int) and ``window_seconds`` (int)."""


def _interpolate_env(value):
    """Recursively resolve ${VAR} and ${VAR:-default} patterns in strings."""
    if isinstance(value, str):

        def _replacer(match):
            var_name = match.group(1)
            default = match.group(2)
            return os.environ.get(var_name, default if default is not None else "")

        return _ENV_VAR_RE.sub(_replacer, value)
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(v) for v in value]
    return value


def load_config(path: Path) -> Config:
    """Load and validate a YAML config file.

    Environment variables in the form ``${VAR}`` or ``${VAR:-default}``
    are interpolated in string values (especially useful for ``headers``
    containing auth tokens).

    Returns an empty config if the file exists but is blank.
    Raises on file-not-found or malformed YAML with a logged hint.
    """
    try:
        raw = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        logger.error("Config file not found: %s", path)
        raise
    except yaml.YAMLError as e:
        logger.error("Invalid YAML in config file %s: %s", path, e)
        raise
    if raw is None:
        raw = {"connectors": []}
    interpolated = _interpolate_env(raw)
    assert isinstance(interpolated, dict)
    return Config(**interpolated)  # pyright: ignore[reportArgumentType]
