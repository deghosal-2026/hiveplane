"""Artifact binding: the immutable hash a certification is bound to (M32-02, D26).

Everything that can change an agent's behavior is an input to one
``artifact_hash``. Under-hashing lets a change slip through; over-hashing causes
false re-certifications. The binding deliberately excludes the manifest's
identity metadata and the certification block itself (which changes when an
attestation is applied) so applying a certification never invalidates it.

Inputs:

- ``manifest_hash`` — canonical JSON of the behavior-affecting spec fields
  (runtime, model, tools, budget, approvals, output shaping, sandbox);
- ``toolset_hash`` — sorted tool allow entries (id, trust, approval) plus deny
  and default trust;
- ``model_binding`` — the exact canonical model identity, never an alias;
- ``policy_version`` — the team's effective policy-pack version, when known.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from hiveplane.core.workload import AgentWorkload


def canonical_json(payload: Any) -> str:
    """Return compact, sorted-key JSON for stable hashing."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ArtifactBinding(BaseModel):
    """The hash pre-image components bound by a certification."""

    model_config = ConfigDict(extra="forbid")

    artifact_hash: str = Field(min_length=1)
    manifest_hash: str = Field(min_length=1)
    toolset_hash: str = Field(min_length=1)
    model_identity: str = Field(min_length=1)
    policy_version: str | None = None


def _behavioral_spec(manifest: AgentWorkload) -> dict[str, Any]:
    spec = manifest.spec
    return {
        "runtime": spec.runtime.model_dump(mode="json"),
        "model": spec.model.model_dump(mode="json"),
        "tools": spec.tools.model_dump(mode="json"),
        "budget": spec.budget.model_dump(mode="json"),
        "approvals": spec.approvals.model_dump(mode="json"),
        "output_shaping": (
            spec.output_shaping.model_dump(mode="json") if spec.output_shaping else None
        ),
        "sandbox": spec.sandbox.model_dump(mode="json") if spec.sandbox else None,
    }


def _toolset(manifest: AgentWorkload) -> dict[str, Any]:
    tools = manifest.spec.tools
    allow = sorted(
        (
            {
                "tool_id": entry.tool_id,
                "trust_level": entry.trust_level.value,
                "require_approval": entry.require_approval,
            }
            for entry in tools.allow
        ),
        key=lambda entry: entry["tool_id"],
    )
    return {
        "allow": allow,
        "deny": sorted(tools.deny),
        "default_trust": tools.default_trust.value,
    }


def compute_binding(
    manifest: AgentWorkload, *, policy_version: str | None = None
) -> ArtifactBinding:
    """Compute the artifact binding for a manifest (deterministic)."""
    from hiveplane.core.spec import canonical_model_identity

    manifest_hash = _sha256(canonical_json(_behavioral_spec(manifest)))
    toolset_hash = _sha256(canonical_json(_toolset(manifest)))
    identity = manifest.spec.model.identity
    model_binding = canonical_model_identity(identity) if identity is not None else "unbound"
    artifact_hash = _sha256(
        canonical_json(
            {
                "manifest_hash": manifest_hash,
                "toolset_hash": toolset_hash,
                "model_binding": model_binding,
                "policy_version": policy_version,
            }
        )
    )
    return ArtifactBinding(
        artifact_hash=artifact_hash,
        manifest_hash=manifest_hash,
        toolset_hash=toolset_hash,
        model_identity=model_binding,
        policy_version=policy_version,
    )


def changed_bindings(
    before: ArtifactBinding | None, after: ArtifactBinding
) -> list[str]:
    """Name the binding components that changed between two bindings.

    Returns human-readable strings; a missing ``before`` binding reports every
    component as changed (nothing was certified).
    """
    if before is None:
        return ["manifest", "toolset", "model_binding", "policy_version"]
    changes: list[str] = []
    if before.manifest_hash != after.manifest_hash:
        changes.append("manifest")
    if before.toolset_hash != after.toolset_hash:
        changes.append("toolset")
    if before.model_identity != after.model_identity:
        changes.append(f"model_binding: {before.model_identity} -> {after.model_identity}")
    if before.policy_version != after.policy_version:
        changes.append(f"policy_version: {before.policy_version} -> {after.policy_version}")
    return changes
