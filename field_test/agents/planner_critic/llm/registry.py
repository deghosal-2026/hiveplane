"""Config-driven provider registry (F-21, F-23, PRD §2.4).

Providers are defined in config, not code: a TOML file maps provider names to
their transport/base_url/model, and roles to the provider they use. The engine
loads whatever is configured, so swapping local OMLX/Ollama for a paid
provider is a config edit, not a code change. The registry round-trips
losslessly and separates planner vs. critic providers (F-23).
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from ..types import PlanningError
from .base import LLMProvider
from .transport_openai import DEFAULT_TIMEOUT_S, OpenAICompatibleProvider

logger = logging.getLogger(__name__)

ROLE_PLANNER = "planner"
ROLE_CRITIC = "critic"
DEFAULT_ROLES = (ROLE_PLANNER, ROLE_CRITIC)

DEFAULT_CONFIG = """
# PlannerCritic provider registry (F-21): providers live in config, not code.
# ``plancritic providers add/list/rm`` manages this file.

[roles]
planner = "local"
critic = "local"

[providers.local]
transport = "openai-compatible"
base_url = "http://localhost:11434/v1"
model = "llama3.2"
"""


@dataclass(frozen=True)
class ProviderSpec:
    """A provider definition as stored in config.

    Attributes:
        name: Provider id used as the config key.
        transport: Transport kind (today: ``openai-compatible``).
        base_url: Endpoint base URL (local endpoints override via this).
        model: Model name served at that endpoint.
        api_key: Optional API key; None means unauthenticated/local.
        max_tokens: Optional max response tokens; None falls back to the
            transport default (env: ``PC_MAX_TOKENS``, default 16384).
        timeout_s: Optional per-request timeout in seconds; None uses the
            transport default (180s).
        suppress_thinking: When True, send vLLM's ``chat_template_kwargs``
            with ``enable_thinking=False`` to suppress chain-of-thought on
            Qwen-family models. Off by default (strict OpenAI-compatible);
            only enable for vLLM/Ollama endpoints that accept this field.
    """

    name: str
    transport: str
    base_url: str
    model: str
    model_version: str = ""
    temperature: float | None = None
    api_key: str | None = None
    max_tokens: int | None = None
    timeout_s: float | None = None
    suppress_thinking: bool = False


@dataclass
class ProviderRegistry:
    """Loads and persists the provider config file.

    Attributes:
        providers: name → spec, loaded from config.
        roles: role → provider name.
        path: Config file path the registry reads/writes.
    """

    providers: dict[str, ProviderSpec] = field(default_factory=dict)
    roles: dict[str, str] = field(default_factory=dict)
    path: Path = field(default_factory=lambda: Path("plancritic.toml"))

    # -- loading -------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path = "plancritic.toml") -> ProviderRegistry:
        """Load a registry from a TOML file; empty registry if absent.

        Args:
            path: Config file path.

        Returns:
            A populated registry; unknown/malformed entries are skipped with
            a warning rather than crashing the engine.
        """
        path = Path(path)
        if not path.exists():
            return cls(path=path)
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        return cls._from_dict(data, path)

    @classmethod
    def _from_dict(cls, data: dict[str, object], path: Path) -> ProviderRegistry:
        """Build a registry from a parsed TOML mapping.

        Malformed entries (non-dict provider specs, non-str role values) are
        skipped with a warning rather than crashing the engine (DD-04).
        """
        providers: dict[str, ProviderSpec] = {}
        raw_providers = data.get("providers")
        if isinstance(raw_providers, dict):
            for name, spec in raw_providers.items():
                if not isinstance(spec, dict):
                    logger.warning(
                        "provider '%s' in %s is malformed (expected a table); skipping",
                        name,
                        path,
                    )
                    continue
                spec_dict = cast("dict[str, object]", spec)
                api_key = spec_dict.get("api_key")
                max_tokens_raw = spec_dict.get("max_tokens")
                timeout_raw = spec_dict.get("timeout_s")
                suppress_thinking_raw = spec_dict.get("suppress_thinking")
                model_version_raw = spec_dict.get("model_version")
                temperature_raw = spec_dict.get("temperature")
                providers[name] = ProviderSpec(
                    name=name,
                    transport=str(spec_dict.get("transport", "openai-compatible")),
                    base_url=str(spec_dict.get("base_url", "")),
                    model=str(spec_dict.get("model", "")),
                    model_version=str(model_version_raw)
                    if isinstance(model_version_raw, str)
                    else "",
                    api_key=str(api_key) if isinstance(api_key, str) else None,
                    max_tokens=(
                        int(max_tokens_raw) if isinstance(max_tokens_raw, (int, float)) else None
                    ),
                    timeout_s=(
                        float(timeout_raw) if isinstance(timeout_raw, (int, float)) else None
                    ),
                    suppress_thinking=bool(suppress_thinking_raw)
                    if isinstance(suppress_thinking_raw, bool)
                    else False,
                    temperature=float(temperature_raw)
                    if isinstance(temperature_raw, (int, float))
                    else None,
                )
        raw_roles = data.get("roles")
        roles: dict[str, str] = {}
        if isinstance(raw_roles, dict):
            for role, provider in raw_roles.items():
                if isinstance(role, str) and isinstance(provider, str):
                    roles[role] = provider
                else:
                    logger.warning(
                        "role entry in %s is malformed (expected string values); skipping",
                        path,
                    )
        return cls(providers=providers, roles=roles, path=path)

    # -- mutation ------------------------------------------------------------
    def add(
        self,
        name: str,
        *,
        base_url: str,
        model: str,
        transport: str = "openai-compatible",
        api_key: str | None = None,
        role: str | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
        suppress_thinking: bool = False,
        model_version: str = "",
        temperature: float | None = None,
    ) -> None:
        """Add (or replace) a provider and optionally bind it to a role.

        Args:
            name: Provider id.
            base_url: Endpoint base URL.
            model: Model name.
            transport: Transport kind (default openai-compatible).
            api_key: Optional API key.
            role: Optional role to bind (planner/critic).
            max_tokens: Optional max response tokens override.
            timeout_s: Optional per-request timeout in seconds.
            suppress_thinking: When True, send vLLM's thinking-suppression
                kwargs (Qwen-family models). Off by default for strict
                OpenAI-compatible endpoints.
            model_version: Optional model version string for DecisionContext.
            temperature: Optional temperature setting for DecisionContext.
        """
        self.providers[name] = ProviderSpec(
            name=name,
            transport=transport,
            base_url=base_url,
            model=model,
            api_key=api_key,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
            suppress_thinking=suppress_thinking,
            model_version=model_version,
            temperature=temperature,
        )
        if role is not None:
            self.roles[role] = name

    def remove(self, name: str) -> bool:
        """Remove a provider; returns False if it was not present.

        Args:
            name: Provider id to remove.
        """
        removed = self.providers.pop(name, None) is not None
        if removed:
            self.roles = {r: p for r, p in self.roles.items() if p != name}
        return removed

    def save(self) -> None:
        """Write the registry back to ``self.path`` as TOML.

        Raises:
            PlanningError: if the config file cannot be written.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(self._to_toml())
        except OSError as err:
            raise PlanningError(f"cannot write provider config {self.path}: {err}") from err

    def _to_toml(self) -> str:
        """Render the registry as a TOML document."""
        lines = ["# PlannerCritic provider registry (F-21)", "[roles]"]
        for role in DEFAULT_ROLES:
            if role in self.roles:
                lines.append(f'{role} = "{self.roles[role]}"')
        lines.append("")
        lines.append("[providers]")
        for name, spec in self.providers.items():
            api = f'\napi_key = "{spec.api_key}"' if spec.api_key else ""
            ver = f'\nmodel_version = "{spec.model_version}"' if spec.model_version else ""
            temp = f"\ntemperature = {spec.temperature}" if spec.temperature is not None else ""
            lines.append(
                f'[providers."{name}"]\n'
                f'transport = "{spec.transport}"\n'
                f'base_url = "{spec.base_url}"\n'
                f'model = "{spec.model}"{api}{ver}{temp}'
            )
        return "\n".join(lines) + "\n"

    # -- runtime -------------------------------------------------------------
    def get_provider(self, role: str) -> LLMProvider:
        """Construct the transport for a role from config (F-23).

        Args:
            role: planner or critic.

        Returns:
            A configured provider instance.

        Raises:
            PlanningError: if the role has no provider bound or the provider
                uses an unknown transport.
        """
        provider_name = self.roles.get(role)
        if provider_name is None:
            raise PlanningError(f"no provider bound to role '{role}'")
        spec = self.providers.get(provider_name)
        if spec is None:
            raise PlanningError(f"role '{role}' references unknown provider '{provider_name}'")
        if spec.transport != "openai-compatible":
            raise PlanningError(
                f"unsupported transport '{spec.transport}' for provider '{provider_name}'"
            )
        extra_payload: dict[str, object] = {}
        if spec.suppress_thinking:
            extra_payload["chat_template_kwargs"] = {"enable_thinking": False}
        return OpenAICompatibleProvider(
            name=spec.name,
            base_url=spec.base_url,
            model=spec.model,
            api_key=spec.api_key,
            max_tokens=spec.max_tokens,
            timeout=spec.timeout_s if spec.timeout_s is not None else DEFAULT_TIMEOUT_S,
            extra_payload=extra_payload or None,
        )
