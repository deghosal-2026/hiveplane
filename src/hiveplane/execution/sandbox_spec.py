"""The sandboxed child's run spec (M23, #110, D11).

Kept separate from :mod:`hiveplane.execution.subprocess_worker` so adapters can
build the child command without importing the worker module (which imports the
adapter error types and would form a cycle).
"""

from __future__ import annotations

import sys

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hiveplane.core.sandbox import ResourceCaps


class SandboxWorkerSpec(BaseModel):
    """Everything the child needs to run one sandboxed agent task."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    entrypoint: str = Field(min_length=1)
    task: dict[str, JsonValue] = Field(default_factory=dict)
    base_url: str = Field(min_length=1)
    token: str = Field(min_length=1)
    root: str = "."
    resource_caps: ResourceCaps | None = None


def worker_command(spec: SandboxWorkerSpec) -> list[str]:
    """The argv that launches this spec in a subprocess."""
    return [
        sys.executable,
        "-m",
        "hiveplane.execution.subprocess_worker",
        "--spec",
        spec.model_dump_json(),
    ]
