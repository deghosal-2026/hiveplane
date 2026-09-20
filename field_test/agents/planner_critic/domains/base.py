"""Domain Pack protocol and manifest loader (M3, #139).

A *domain pack* bundles domain-specific gate evaluators, precondition
catalogs, and optional critic prompt templates into one installable unit.
Packs are **additive** to the built-in six deterministic gates — they never
replace them. The domain prompt template is **prepended** to the critic's
system prompt, never replacing it either.

Usage::

    from planner_critic.domains.base import pack_from_dict, DomainPack

    my_pack = pack_from_dict({"name": "secops", "gates": [...]})
    engine = Engine(planner, critic, domain_pack=my_pack)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

import yaml

from ..gates.base import BaseGate


@runtime_checkable
class DomainPack(Protocol):
    """Protocol for an installable domain strategy pack.

    A domain pack provides:

    * **gate_evaluators** — domain-specific deterministic gate instances
      that run *in addition* to the built-in six (schema_valid, dep_cycles,
      ordering, verification, rollback, preconditions, parallel_safety).
    * **precondition_catalog** — a mapping of precondition fact keys to
      human-readable descriptions, used by the init scaffolding and the
      precondition closer.
    * **critic_prompt_template** — an optional prompt fragment prepended to
      the critic's system prompt so the LLM can focus on domain-specific
      failure modes.
    * **pack_config** — arbitrary configuration data consumed by the pack's
      own gates (e.g. budget caps, allowed clusters).
    """

    name: str
    gate_evaluators: list[BaseGate]
    precondition_catalog: dict[str, str]
    critic_prompt_template: str | None
    pack_config: dict[str, Any]


def pack_from_dict(manifest: dict[str, Any]) -> DomainPack:
    """Build a protocol-compliant domain pack from a manifest dict.

    Args:
        manifest: The decoded ``domain-pack.yaml`` content.

    Returns:
        A lightweight :class:`DomainPack` protocol-compliant object.

    Raises:
        ValueError: When ``name`` is missing.
    """
    pack_name_raw: str | None = manifest.get("name")
    if not pack_name_raw:
        raise ValueError("domain pack manifest must include a 'name' field")
    pack_name: str = pack_name_raw

    gates_data: list[dict[str, Any]] = manifest.get("gates", [])

    class _Pack:
        """Protocol-compliant domain pack wrapper."""

        def __init__(self) -> None:
            self.name = pack_name
            self.gate_evaluators = [_build_gate(g) for g in gates_data]
            self.precondition_catalog: dict[str, str] = manifest.get("preconditions", {})
            self.critic_prompt_template: str | None = manifest.get("critic_prompt")
            self.pack_config: dict[str, Any] = manifest.get("config", {})

    return _Pack()


def load_domain_pack_from_manifest(path: str | Path) -> DomainPack:
    """Load a domain pack from a ``domain-pack.yaml`` file.

    Args:
        path: Filesystem path to the manifest YAML.

    Returns:
        A :class:`DomainPack` protocol-compliant object.

    Raises:
        FileNotFoundError: When the manifest file does not exist.
        yaml.YAMLError: When the manifest is not valid YAML.
        ValueError: When the manifest lacks required fields.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"domain pack manifest not found: {path}")
    raw = p.read_text(encoding="utf-8")
    manifest: dict[str, Any] = yaml.safe_load(raw)
    return pack_from_dict(manifest)


def _build_gate(gate_spec: dict[str, Any]) -> BaseGate:
    """Resolve a gate dict to a :class:`BaseGate` instance.

    For now, gates must be importable Python classes specified via
    ``module`` and ``class_name`` keys. Inline gate definitions (via
    ``evaluator``) are reserved for a future extension.

    Args:
        gate_spec: A gate specification from the manifest.

    Returns:
        An instantiated gate.

    Raises:
        ImportError: When the gate module or class cannot be resolved.
    """
    mod_path = gate_spec.get("module", "")
    class_name = gate_spec.get("class", gate_spec.get("class_name", "Gate"))
    if not mod_path:
        raise ValueError(f"gate spec must include a 'module' path; got {gate_spec}")
    import importlib

    mod = importlib.import_module(mod_path)
    cls = getattr(mod, class_name, None)
    if cls is None:
        raise ImportError(f"class {class_name!r} not found in module {mod_path!r}")
    gate_cls: type[BaseGate] = cast("type[BaseGate]", cls)
    return gate_cls()


def find_domain_packs(
    namespace: str = "planner_critic.domains",
) -> dict[str, DomainPack]:
    """Discover installed domain packs via namespace scanning.

    Scans ``planner_critic.domains.*`` for modules that expose a
    ``domain_pack`` attribute conforming to the :class:`DomainPack`
    protocol.

    Args:
        namespace: The dotted package path to scan.

    Returns:
        A mapping of pack name → DomainPack.
    """
    import importlib
    import importlib.util
    import pkgutil

    packs: dict[str, DomainPack] = {}
    spec = importlib.util.find_spec(namespace)
    if spec is None or spec.submodule_search_locations is None:
        return packs

    for _finder, mod_name, _ispkg in pkgutil.iter_modules(spec.submodule_search_locations):
        full_name = f"{namespace}.{mod_name}"
        try:
            mod = importlib.import_module(full_name)
        except Exception:  # noqa: S112
            continue
        candidate = getattr(mod, "domain_pack", None)
        if isinstance(candidate, DomainPack):
            packs[candidate.name] = candidate
    return packs


__all__ = [
    "DomainPack",
    "find_domain_packs",
    "load_domain_pack_from_manifest",
    "pack_from_dict",
]
