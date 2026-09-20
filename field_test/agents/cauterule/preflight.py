"""Operational preflight checks — provider + corpus validation before expensive runs."""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cauterule.config import Config
from cauterule.corpus.validation import validate_annotations, validate_corpus_sizes
from cauterule.serialization.trajectory_jsonl import load_trajectories

SLOW_THRESHOLD_S = 30.0
MIN_FREE_GB = 0.5
REQUIRED_FIELDS = {
    "trajectory_id",
    "timestamp",
    "task",
    "steps",
    "success",
    "domain",
    "quality_label",
    "tags",
}
RAW_REQUIRED_FIELDS = REQUIRED_FIELDS | {"expected_outcome"}

MODEL_COST_PER_REQUEST: dict[str, float] = {
    "gpt-4o": 0.005,
    "gpt-4o-mini": 0.0004,
    "claude-3-opus": 0.015,
    "claude-3-haiku": 0.00025,
    "llama-3.2-3b-instruct": 0.0,
    "qwen3-4b-instruct": 0.0,
    "llama-3.1-8b-instruct": 0.0,
}
DEFAULT_COST_PER_REQUEST = 0.01

# Latency estimates (p95) per model in seconds (#486).
MODEL_LATENCY_S: dict[str, float] = {
    "gpt-4o": 2.5,
    "gpt-4o-mini": 1.2,
    "claude-3-opus": 5.0,
    "claude-3-haiku": 0.8,
    "llama-3.2-3b-instruct": 1.0,
    "qwen3-4b-instruct": 1.1,
    "llama-3.1-8b-instruct": 1.5,
}

# Tiered strategy guidance (#486).
MODEL_TIERS: dict[str, str] = {
    "llama-3.2-3b-instruct": "local (iteration/regression)",
    "qwen3-4b-instruct": "local (iteration/regression)",
    "llama-3.1-8b-instruct": "local (regression/pre-commit)",
    "gpt-4o-mini": "cheap cloud (PR/release gate, nightly)",
    "claude-3-haiku": "cheap cloud (PR/release gate)",
    "gpt-4o": "flagship (calibration, disputed cases)",
    "claude-3-opus": "flagship (calibration, disputed cases)",
}


@dataclass(frozen=True)
class CheckResult:
    """Single check result."""

    name: str
    passed: bool
    message: str = ""
    latency_s: float | None = None


@dataclass(frozen=True)
class PreflightResult:
    """Aggregate preflight result."""

    passed: bool
    provider_checks: tuple[CheckResult, ...] = field(default_factory=tuple)
    corpus_checks: tuple[CheckResult, ...] = field(default_factory=tuple)
    cost_estimate_usd: float | None = None

    @property
    def warnings(self) -> list[str]:
        """Return messages for failed checks."""
        return [c.message for c in (*self.provider_checks, *self.corpus_checks) if not c.passed]


def check_provider(
    config: Config, probe: Callable[[Config], float] | None = None
) -> list[CheckResult]:
    """Check LLM provider availability and latency.

    Args:
        config: Loaded :class:`Config`.
        probe: Optional callable ``probe(config) -> float`` returning latency in
            seconds or raising on failure. Used for testing.

    Returns:
        List of :class:`CheckResult`.
    """
    results: list[CheckResult] = []

    # API key check.
    if config.llm.provider in {"openai", "anthropic"} and not config.llm.api_key:
        results.append(
            CheckResult(
                name="api_key",
                passed=False,
                message="API key not set for provider " + config.llm.provider,
            )
        )
    else:
        results.append(
            CheckResult(name="api_key", passed=True, message="API key present or not required")
        )

    # Model ID check (basic non-empty).
    if not config.llm.model:
        results.append(CheckResult(name="model_id", passed=False, message="Model ID is empty"))
    else:
        results.append(
            CheckResult(name="model_id", passed=True, message=f"Model ID: {config.llm.model}")
        )

    # Latency probe.
    if probe is not None:
        start = time.monotonic()
        try:
            latency = probe(config)
            elapsed = latency if latency is not None else time.monotonic() - start
            if elapsed > SLOW_THRESHOLD_S:
                results.append(
                    CheckResult(
                        name="latency",
                        passed=False,
                        message=f"Slow model: {elapsed:.1f}s > {SLOW_THRESHOLD_S:.0f}s",
                        latency_s=elapsed,
                    )
                )
            else:
                results.append(
                    CheckResult(
                        name="latency",
                        passed=True,
                        message=f"Latency: {elapsed:.1f}s",
                        latency_s=elapsed,
                    )
                )
        except Exception as exc:
            results.append(
                CheckResult(name="endpoint", passed=False, message=f"Provider probe failed: {exc}")
            )
    else:
        results.append(
            CheckResult(name="endpoint", passed=True, message="Endpoint check skipped (no probe)")
        )

    return results


def check_corpus(
    corpus_path: str | Path,
    catalog_path: str | Path | None = None,
) -> list[CheckResult]:
    """Validate corpus JSONL files.

    Args:
        corpus_path: Path to a JSONL file or directory of JSONL files.
        catalog_path: Optional path to a ``catalog.yaml`` for size/annotation checks.

    Returns:
        List of :class:`CheckResult`.
    """
    results: list[CheckResult] = []
    path = Path(corpus_path)

    # Schema version checks via catalog, if provided.
    if catalog_path is not None:
        from cauterule.corpus.catalog import load_catalog

        try:
            catalog = load_catalog(catalog_path)
        except (OSError, ValueError) as exc:
            results.append(
                CheckResult(
                    name="catalog_load",
                    passed=False,
                    message=f"Failed to load catalog: {exc}",
                )
            )
            return results

        counts = {cid: meta.trajectory_count for cid, meta in catalog.items()}
        for w in validate_corpus_sizes(counts):
            results.append(CheckResult(name="corpus_size", passed=False, message=w))
            # Annotations: load first batch and validate.
            trajs: list[Any] = []
        for f in sorted(path.rglob("*.jsonl")) if path.is_dir() else [path]:
            if not f.is_file() or f.suffix != ".jsonl":
                continue
            trajs.extend(list(load_trajectories(f)))
        for w in validate_annotations(trajs):
            results.append(CheckResult(name="annotation", passed=False, message=w))

    files: list[Path] = []
    if path.is_file() and path.suffix == ".jsonl":
        files = [path]
    elif path.is_dir():
        files = list(path.rglob("*.jsonl"))
        if not files:
            results.append(
                CheckResult(name="corpus_files", passed=False, message=f"No JSONL files in {path}")
            )
            return results
    else:
        results.append(
            CheckResult(name="corpus_path", passed=False, message=f"Corpus path not found: {path}")
        )
        return results

    # Determine required fields: raw corpora need expected_outcome.
    is_raw = "raw" in str(path)
    required = RAW_REQUIRED_FIELDS if is_raw else REQUIRED_FIELDS

    seen_ids: set[str] = set()
    total_trajectories = 0
    schema_version_seen: str | None = None

    for f in files:
        if f.stat().st_size == 0:
            results.append(
                CheckResult(name=f"corpus_empty:{f.name}", passed=False, message=f"Empty file: {f}")
            )
            continue
        try:
            content = f.read_text(encoding="utf-8")
        except Exception as exc:
            results.append(
                CheckResult(
                    name=f"corpus_read:{f.name}", passed=False, message=f"Cannot read {f}: {exc}"
                )
            )
            continue

        for idx, line in enumerate(content.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                results.append(
                    CheckResult(
                        name=f"corpus_json:{f.name}:{idx}",
                        passed=False,
                        message=f"Invalid JSON in {f}:{idx}: {exc}",
                    )
                )
                continue

            if not isinstance(data, dict):
                results.append(
                    CheckResult(
                        name=f"corpus_schema:{f.name}:{idx}",
                        passed=False,
                        message=f"Line {idx} in {f} is not an object",
                    )
                )
                continue

            missing = required - set(data.keys())
            # Accept either 'id' or 'trajectory_id' for trajectory ID.
            if "trajectory_id" in missing and "id" in data:
                missing = missing - {"trajectory_id"}
            if missing:
                results.append(
                    CheckResult(
                        name=f"corpus_fields:{f.name}:{idx}",
                        passed=False,
                        message=f"Missing fields {missing} in {f}:{idx}",
                    )
                )

            tid = str(data.get("trajectory_id") or data.get("id") or "")
            if tid:
                if tid in seen_ids:
                    results.append(
                        CheckResult(
                            name=f"corpus_duplicate:{tid}",
                            passed=False,
                            message=f"Duplicate trajectory ID: {tid}",
                        )
                    )
                seen_ids.add(tid)

            # Schema version check on first line.
            sv = data.get("schema_version")
            if sv is not None and schema_version_seen is None:
                schema_version_seen = str(sv)

            total_trajectories += 1

    if schema_version_seen is None or schema_version_seen == "":
        results.append(
            CheckResult(
                name="schema_version",
                passed=True,
                message="schema_version absent (defaults OK for v1.0)",
            )
        )
    elif schema_version_seen != "1.0":
        results.append(
            CheckResult(
                name="schema_version",
                passed=False,
                message=(
                    f"Unknown or incompatible schema_version: "
                    f"{schema_version_seen!r}. Expected: 1.0"
                ),
            )
        )

    if total_trajectories == 0:
        results.append(
            CheckResult(name="corpus_size", passed=False, message="Corpus contains 0 trajectories")
        )

    if not any(not r.passed for r in results):
        results.append(
            CheckResult(
                name="corpus_ok",
                passed=True,
                message=f"Corpus OK: {len(files)} files, {total_trajectories} trajectories",
            )
        )

    return results


def estimate_cost(
    num_trajectories: int, model: str | None = None, tokens_per_request: int = 4000
) -> float:
    """Estimate total cost for a run based on model."""
    rate = MODEL_COST_PER_REQUEST.get(model or "", DEFAULT_COST_PER_REQUEST)
    return round(num_trajectories * rate * (tokens_per_request / 1000), 2)


def cost_per_1k_trajectories(model: str) -> dict[str, float | str]:
    """Return $/1k trajs, p95 latency, and tier for *model* (#486)."""
    rate = MODEL_COST_PER_REQUEST.get(model, DEFAULT_COST_PER_REQUEST)
    cost = round(1000 * rate * (4000 / 1000), 2)
    latency = MODEL_LATENCY_S.get(model, 2.0)
    tier = MODEL_TIERS.get(model, "unclassified")
    return {"model": model, "cost_per_1k": cost, "p95_latency_s": latency, "tier": tier}


def cost_table(models: list[str] | None = None) -> list[dict[str, float | str]]:
    """Return the published cost/latency/tier table (#486)."""
    models = models or list(MODEL_COST_PER_REQUEST.keys())
    return [cost_per_1k_trajectories(m) for m in models]


def check_output_dir(output_path: str | Path) -> CheckResult:
    """Check that *output_path* is writable and has free space."""
    path = Path(output_path)
    parent = path.parent if path.suffix else path
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
    if not os.access(parent, os.W_OK):
        return CheckResult(
            name="output_dir",
            passed=False,
            message=f"Output directory is not writable: {parent}",
        )
    free_gb = shutil.disk_usage(parent).free / (1024**3)
    if free_gb < MIN_FREE_GB:
        return CheckResult(
            name="output_dir",
            passed=False,
            message=f"Low disk space: {free_gb:.1f} GB free (need {MIN_FREE_GB:.1f})",
        )
    if path.suffix and path.exists():
        return CheckResult(
            name="output_dir",
            passed=True,
            message=f"Output file exists (may overwrite): {path}",
        )
    return CheckResult(name="output_dir", passed=True, message=f"Output directory ready: {parent}")


def run_preflight(
    config: Config,
    corpus_path: str | Path | None = None,
    probe: Callable[[Config], float] | None = None,
    output_dir: str | Path | None = None,
    catalog_path: str | Path | None = None,
    cost_per_request_usd: float | None = None,
) -> PreflightResult:
    """Run all preflight checks and return aggregate result.

    ``cost_per_request_usd`` optionally overrides cost estimation with a flat
    $/LLM-request rate (used by the field-test runner, #678); when omitted, the
    model's per-1k-token rate is used.
    """
    provider_results = check_provider(config, probe=probe)
    corpus_results: list[CheckResult] = []
    if corpus_path is not None:
        corpus_results = check_corpus(corpus_path, catalog_path=catalog_path)

    if output_dir is not None:
        corpus_results.append(check_output_dir(output_dir))

    all_passed = all(r.passed for r in provider_results) and all(r.passed for r in corpus_results)
    cost: float | None = None
    if corpus_path is not None and all_passed:
        try:
            path = Path(corpus_path)
            total = 0
            if path.is_file():
                total = sum(
                    1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
                )
            elif path.is_dir():
                total = sum(
                    1
                    for f in path.rglob("*.jsonl")
                    for line in f.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )
            if cost_per_request_usd is not None:
                cost = round(total * cost_per_request_usd, 2)
            else:
                cost = estimate_cost(total, config.llm.model)
        except Exception:
            cost = None

    return PreflightResult(
        passed=all_passed,
        provider_checks=tuple(provider_results),
        corpus_checks=tuple(corpus_results),
        cost_estimate_usd=cost,
    )
