"""``hiveplane wrap`` orchestration: detect, plan, and write (M31-05, M31-06).

The contract is scaffold-only: wrap inspects an app statically, generates drafts
into a **new** output directory, and never writes to the source tree. Nothing is
registered, certified, or executed.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.wrap.detect import Detection, WrapError, detect
from hiveplane.wrap.scaffold import render_files


class WrapPlan(BaseModel):
    """The detection result and the files wrap would generate."""

    model_config = ConfigDict(extra="forbid")

    source_path: str = Field(min_length=1)
    framework: str = Field(min_length=1)
    detected_framework: str | None = None
    entrypoint: str | None = None
    files: dict[str, str] = Field(default_factory=dict)


def plan_wrap(path: str | Path, *, framework: str | None = None) -> WrapPlan:
    """Inspect ``path`` and plan the generated files (writes nothing)."""
    detection = detect(path, framework=framework)
    return _plan_from(detection, requested=framework)


def _plan_from(detection: Detection, *, requested: str | None) -> WrapPlan:
    if detection.framework is None:
        raise WrapError("no framework selected")
    files = render_files(detection)
    return WrapPlan(
        source_path=str(detection.source_root),
        framework=detection.framework,
        detected_framework=(
            sorted(detection.imported_frameworks)[0] if detection.imported_frameworks else None
        ),
        entrypoint=detection.entrypoint,
        files=files,
    )


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def write_wrap(plan: WrapPlan, out: str | Path, *, force: bool = False) -> list[Path]:
    """Write a plan into ``out``, refusing to touch the source tree.

    Refuses when ``out`` is the source tree or nested inside it, and when ``out``
    already exists and is non-empty unless ``force`` is set.
    """
    source = Path(plan.source_path).resolve()
    out_path = Path(out).resolve()
    if out_path == source or _is_within(out_path, source) or _is_within(source, out_path):
        raise WrapError(
            f"refusing to write into or around the source tree {source}; "
            "choose an --out outside it"
        )
    if out_path.exists() and any(out_path.iterdir()) and not force:
        raise WrapError(f"output directory {out_path} is not empty; pass --force to overwrite")

    out_path.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, content in plan.files.items():
        target = out_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(target)
    return written
