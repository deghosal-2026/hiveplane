"""Configuration for CauterRule.

Loads ``cauterule.toml`` and environment variables into typed dataclasses.

TOML layout (all sections optional, defaults shown):

```toml
[llm]
provider = "openai"
model = "gpt-4o"
api_key = ""
base_url = ""
temperature = 0.0
max_tokens = 4096
timeout = 30
max_retries = 2

[paths]
rules = "rules"
trajectories = "trajectories"

[thresholds]
precision = 0.8
recall = 0.5

[promotion]
mode = "hybrid"  # auto | human-review | hybrid

[redaction]
patterns = []  # additional regex patterns

[extraction]
passes = 3
temperatures = [0.2, 0.5, 0.8]
confidence_threshold = 0.6
gate_mode = "strict"  # strict | relaxed — pre-extraction null-hypothesis gate
```
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LLMConfig:
    """LLM provider settings."""

    provider: str = "openai"
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.0
    max_tokens: int = 4096
    timeout: int = 30
    max_retries: int = 2


@dataclass(frozen=True)
class PathsConfig:
    """Filesystem paths."""

    rules: str = "rules"
    trajectories: str = "trajectories"


@dataclass(frozen=True)
class ThresholdsConfig:
    """Replay/promotion thresholds."""

    precision: float = 0.8
    recall: float = 0.5


@dataclass(frozen=True)
class PromotionConfig:
    """Promotion mode."""

    mode: str = "hybrid"  # auto | human-review | hybrid


@dataclass(frozen=True)
class RedactionConfig:
    """Redaction settings."""

    patterns: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ExtractionConfig:
    """Extraction settings."""

    passes: int = 3
    temperatures: tuple[float, ...] = (0.2, 0.5, 0.8)
    confidence_threshold: float = 0.6
    gate_mode: str = "strict"  # strict | relaxed


@dataclass(frozen=True)
class PacksConfig:
    """Pack ecosystem settings."""

    min_safety_score: int = 70


@dataclass(frozen=True)
class WebhookConfig:
    """Promotion webhook settings."""

    enabled: bool = False
    provider: str = "slack"  # slack | discord | github | custom
    url: str = ""
    secret: str = ""
    on_events: tuple[str, ...] = ("promote",)
    max_attempts: int = 3
    backoff: tuple[int, ...] = (1, 5, 30)
    redact: bool = True


@dataclass(frozen=True)
class McpConfig:
    """MCP remote-mode settings."""

    auth_mode: str = "none"  # none | bearer
    tokens: tuple[str, ...] = ()
    rate_capacity: int = 60
    rate_refill_per_min: float = 30.0


@dataclass(frozen=True)
class OtelConfig:
    """OpenTelemetry export settings."""

    enabled: bool = False
    endpoint: str = "http://localhost:4317"
    service_name: str = "cauterule"
    headers: tuple[tuple[str, str], ...] = ()
    batch_size: int = 512
    export_interval_ms: int = 5000
    retry_max: int = 3


@dataclass(frozen=True)
class Config:
    """Top-level configuration."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    thresholds: ThresholdsConfig = field(default_factory=ThresholdsConfig)
    promotion: PromotionConfig = field(default_factory=PromotionConfig)
    redaction: RedactionConfig = field(default_factory=RedactionConfig)
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    packs: PacksConfig = field(default_factory=PacksConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    mcp: McpConfig = field(default_factory=McpConfig)
    otel: OtelConfig = field(default_factory=OtelConfig)


def _parse_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        msg = f"invalid TOML in {path}: {exc}"
        raise ValueError(msg) from exc
    if not isinstance(data, dict):
        return {}
    return data


def _llm_from_dict(data: dict[str, Any]) -> LLMConfig:
    return LLMConfig(
        provider=str(data.get("provider", "openai")),
        model=str(data.get("model", "gpt-4o")),
        api_key=str(data.get("api_key", "")),
        base_url=str(data.get("base_url", "")),
        temperature=float(data.get("temperature", 0.0)),
        max_tokens=int(data.get("max_tokens", 4096)),
        timeout=int(data.get("timeout", 30)),
        max_retries=int(data.get("max_retries", 2)),
    )


def _paths_from_dict(data: dict[str, Any]) -> PathsConfig:
    return PathsConfig(
        rules=str(data.get("rules", "rules")),
        trajectories=str(data.get("trajectories", "trajectories")),
    )


def _thresholds_from_dict(data: dict[str, Any]) -> ThresholdsConfig:
    return ThresholdsConfig(
        precision=float(data.get("precision", 0.8)),
        recall=float(data.get("recall", 0.5)),
    )


def _promotion_from_dict(data: dict[str, Any]) -> PromotionConfig:
    mode = str(data.get("mode", "hybrid"))
    if mode not in {"auto", "human-review", "hybrid"}:
        raise ValueError(f"promotion.mode must be auto|human-review|hybrid, got {mode!r}")
    return PromotionConfig(mode=mode)


def _redaction_from_dict(data: dict[str, Any]) -> RedactionConfig:
    patterns = data.get("patterns", [])
    if not isinstance(patterns, list):
        raise ValueError("redaction.patterns must be a list")
    return RedactionConfig(patterns=tuple(str(p) for p in patterns))


def _extraction_from_dict(data: dict[str, Any]) -> ExtractionConfig:
    passes = int(data.get("passes", 3))
    if passes < 1:
        raise ValueError(f"extraction.passes must be >=1, got {passes}")
    temps_raw = data.get("temperatures", [0.2, 0.5, 0.8])
    if not isinstance(temps_raw, list):
        raise ValueError("extraction.temperatures must be a list")
    temps = tuple(float(t) for t in temps_raw)
    gate_mode = str(data.get("gate_mode", "strict"))
    if gate_mode not in {"strict", "relaxed"}:
        raise ValueError(f"extraction.gate_mode must be strict|relaxed, got {gate_mode!r}")
    return ExtractionConfig(
        passes=passes,
        temperatures=temps,
        confidence_threshold=float(data.get("confidence_threshold", 0.6)),
        gate_mode=gate_mode,
    )


def _packs_from_dict(data: dict[str, Any]) -> PacksConfig:
    score = int(data.get("min_safety_score", 70))
    if not 0 <= score <= 100:
        raise ValueError(f"packs.min_safety_score must be 0-100, got {score}")
    return PacksConfig(min_safety_score=score)


def _webhook_from_dict(data: dict[str, Any]) -> WebhookConfig:
    provider = str(data.get("provider", "slack"))
    if provider not in {"slack", "discord", "github", "custom"}:
        raise ValueError(f"webhook.provider must be slack|discord|github|custom, got {provider!r}")
    events = data.get("on_events", ["promote"])
    if isinstance(events, str):
        events = [events]
    backoff_raw = data.get("backoff", [1, 5, 30])
    return WebhookConfig(
        enabled=bool(data.get("enabled", False)),
        provider=provider,
        url=str(data.get("url", "")),
        secret=str(data.get("secret", "")),
        on_events=tuple(str(e) for e in events),
        max_attempts=int(data.get("max_attempts", 3)),
        backoff=tuple(int(b) for b in backoff_raw),
        redact=bool(data.get("redact", True)),
    )


def _mcp_from_dict(data: dict[str, Any]) -> McpConfig:
    auth = data.get("auth", {})
    auth_mode = str(auth.get("mode", data.get("auth_mode", "none")))
    if auth_mode not in {"none", "bearer"}:
        raise ValueError(f"mcp.auth.mode must be none|bearer, got {auth_mode!r}")
    raw_tokens = auth.get("tokens", data.get("tokens", []))
    tokens = tuple(str(t) for t in raw_tokens) if isinstance(raw_tokens, list) else ()
    rate = data.get("rate_limit", {})
    return McpConfig(
        auth_mode=auth_mode,
        tokens=tokens,
        rate_capacity=int(rate.get("capacity", data.get("rate_capacity", 60))),
        rate_refill_per_min=float(
            rate.get("refill_per_min", data.get("rate_refill_per_min", 30.0))
        ),
    )


def _otel_from_dict(data: dict[str, Any]) -> OtelConfig:
    headers_raw = data.get("headers", {})
    headers = (
        tuple((str(k), str(v)) for k, v in headers_raw.items())
        if isinstance(headers_raw, dict)
        else ()
    )
    batch_size = int(data.get("batch_size", 512))
    interval = int(data.get("export_interval_ms", 5000))
    retry_max = int(data.get("retry_max", 3))
    if batch_size < 1 or interval < 1 or retry_max < 0:
        raise ValueError("otel batch_size/export_interval_ms must be > 0, retry_max >= 0")
    endpoint = str(data.get("endpoint", "http://localhost:4317"))
    if not endpoint.startswith(("http://", "https://")):
        raise ValueError(f"otel.endpoint must be an http(s) URL, got {endpoint!r}")
    service_name = str(data.get("service_name", "cauterule"))
    if not service_name.strip():
        raise ValueError("otel.service_name must be non-blank")
    return OtelConfig(
        enabled=bool(data.get("enabled", False)),
        endpoint=endpoint,
        service_name=service_name,
        headers=headers,
        batch_size=batch_size,
        export_interval_ms=interval,
        retry_max=retry_max,
    )


def _config_from_dict(data: dict[str, Any]) -> Config:
    return Config(
        llm=_llm_from_dict(data.get("llm", {})),
        paths=_paths_from_dict(data.get("paths", {})),
        thresholds=_thresholds_from_dict(data.get("thresholds", {})),
        promotion=_promotion_from_dict(data.get("promotion", {})),
        redaction=_redaction_from_dict(data.get("redaction", {})),
        extraction=_extraction_from_dict(data.get("extraction", {})),
        packs=_packs_from_dict(data.get("packs", {})),
        webhook=_webhook_from_dict(data.get("webhook", {})),
        mcp=_mcp_from_dict(data.get("mcp", {})),
        otel=_otel_from_dict(data.get("otel", {})),
    )


def _env_float(name: str) -> float | None:
    """Parse float env var *name*; None when unset; ValueError when malformed."""
    raw = os.getenv(name)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a float, got {raw!r}") from None


def _env_int(name: str) -> int | None:
    """Parse int env var *name*; None when unset; ValueError when malformed."""
    raw = os.getenv(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an int, got {raw!r}") from None


def _apply_env_overrides(config: Config) -> Config:
    """Apply ``CAUTERULE_*`` environment variables over *config*.

    Supported variables (all optional):
    - ``CAUTERULE_LLM_PROVIDER``
    - ``CAUTERULE_LLM_MODEL`` and ``CAUTERULE_MODEL`` (fallback)
    - ``CAUTERULE_LLM_API_KEY``
    - ``CAUTERULE_LLM_BASE_URL``
    - ``CAUTERULE_LLM_TEMPERATURE`` (float)
    - ``CAUTERULE_LLM_MAX_TOKENS`` (int)
    - ``CAUTERULE_LLM_TIMEOUT`` (int, seconds)
    - ``CAUTERULE_LLM_MAX_RETRIES`` (int)
    - ``CAUTERULE_THRESHOLD_PRECISION`` (float)
    - ``CAUTERULE_THRESHOLD_RECALL`` (float)
    - ``CAUTERULE_EXTRACTION_CONFIDENCE_THRESHOLD`` (float)
    - ``CAUTERULE_EXTRACTION_PASSES`` (int)
    - ``CAUTERULE_EXTRACTION_GATE_MODE`` (strict | relaxed)
    - ``CAUTERULE_PROMOTION_MODE`` / ``CAUTERULE_MODE``
    - ``CAUTERULE_RULES_PATH`` / ``CAUTERULE_RULES``
    - ``CAUTERULE_TRAJECTORIES_PATH``
    - ``CAUTERULE_PACKS_MIN_SAFETY_SCORE`` (int, 0-100)
    """
    llm_provider = os.getenv("CAUTERULE_LLM_PROVIDER")
    llm_model = os.getenv("CAUTERULE_LLM_MODEL") or os.getenv("CAUTERULE_MODEL")
    llm_api_key = os.getenv("CAUTERULE_LLM_API_KEY")
    llm_base_url = os.getenv("CAUTERULE_LLM_BASE_URL")
    llm_temperature = _env_float("CAUTERULE_LLM_TEMPERATURE")
    llm_max_tokens = _env_int("CAUTERULE_LLM_MAX_TOKENS")
    llm_timeout = _env_int("CAUTERULE_LLM_TIMEOUT")
    llm_max_retries = _env_int("CAUTERULE_LLM_MAX_RETRIES")
    threshold_precision = _env_float("CAUTERULE_THRESHOLD_PRECISION")
    threshold_recall = _env_float("CAUTERULE_THRESHOLD_RECALL")
    extraction_confidence = _env_float("CAUTERULE_EXTRACTION_CONFIDENCE_THRESHOLD")
    extraction_passes = _env_int("CAUTERULE_EXTRACTION_PASSES")
    extraction_gate_mode = os.getenv("CAUTERULE_EXTRACTION_GATE_MODE")
    promotion_mode = os.getenv("CAUTERULE_PROMOTION_MODE") or os.getenv("CAUTERULE_MODE")
    rules_path = os.getenv("CAUTERULE_RULES_PATH") or os.getenv("CAUTERULE_RULES")
    trajectories_path = os.getenv("CAUTERULE_TRAJECTORIES_PATH") or os.getenv(
        "CAUTERULE_TRAJECTORIES"
    )

    # Rebuild only sections that have overrides, preserving frozen semantics.
    llm = config.llm
    if (
        llm_provider is not None
        or llm_model is not None
        or llm_api_key is not None
        or llm_base_url is not None
        or llm_temperature is not None
        or llm_max_tokens is not None
        or llm_timeout is not None
        or llm_max_retries is not None
    ):
        llm = LLMConfig(
            provider=llm_provider if llm_provider is not None else llm.provider,
            model=llm_model if llm_model is not None else llm.model,
            api_key=llm_api_key if llm_api_key is not None else llm.api_key,
            base_url=llm_base_url if llm_base_url is not None else llm.base_url,
            temperature=llm_temperature if llm_temperature is not None else llm.temperature,
            max_tokens=llm_max_tokens if llm_max_tokens is not None else llm.max_tokens,
            timeout=llm_timeout if llm_timeout is not None else llm.timeout,
            max_retries=llm_max_retries if llm_max_retries is not None else llm.max_retries,
        )

    thresholds = config.thresholds
    if threshold_precision is not None or threshold_recall is not None:
        thresholds = ThresholdsConfig(
            precision=threshold_precision
            if threshold_precision is not None
            else thresholds.precision,
            recall=threshold_recall if threshold_recall is not None else thresholds.recall,
        )

    extraction = config.extraction
    if (
        extraction_confidence is not None
        or extraction_passes is not None
        or extraction_gate_mode is not None
    ):
        if extraction_gate_mode is not None and extraction_gate_mode not in {"strict", "relaxed"}:
            msg = (
                "CAUTERULE_EXTRACTION_GATE_MODE must be strict|relaxed, "
                f"got {extraction_gate_mode!r}"
            )
            raise ValueError(msg)
        extraction = ExtractionConfig(
            passes=extraction_passes if extraction_passes is not None else extraction.passes,
            temperatures=extraction.temperatures,
            confidence_threshold=extraction_confidence
            if extraction_confidence is not None
            else extraction.confidence_threshold,
            gate_mode=extraction_gate_mode
            if extraction_gate_mode is not None
            else extraction.gate_mode,
        )

    paths = config.paths
    if rules_path is not None or trajectories_path is not None:
        paths = PathsConfig(
            rules=rules_path if rules_path is not None else paths.rules,
            trajectories=trajectories_path if trajectories_path is not None else paths.trajectories,
        )

    promotion = config.promotion
    if promotion_mode is not None:
        if promotion_mode not in {"auto", "human-review", "hybrid"}:
            raise ValueError(
                f"CAUTERULE_PROMOTION_MODE must be auto|human-review|hybrid, got {promotion_mode!r}"
            )
        promotion = PromotionConfig(mode=promotion_mode)

    packs = config.packs
    packs_min_score = _env_int("CAUTERULE_PACKS_MIN_SAFETY_SCORE")
    if packs_min_score is not None:
        if not 0 <= packs_min_score <= 100:
            raise ValueError(
                f"CAUTERULE_PACKS_MIN_SAFETY_SCORE must be 0-100, got {packs_min_score}"
            )
        packs = PacksConfig(min_safety_score=packs_min_score)

    if (
        llm is config.llm
        and paths is config.paths
        and promotion is config.promotion
        and thresholds is config.thresholds
        and extraction is config.extraction
        and packs is config.packs
    ):
        return config

    return Config(
        llm=llm,
        paths=paths,
        thresholds=thresholds,
        promotion=promotion,
        redaction=config.redaction,
        extraction=extraction,
        packs=packs,
    )


def load_config(path: str | Path | None = None) -> Config:
    """Load configuration from ``cauterule.toml`` and environment.

    Args:
        path: Optional explicit path to ``cauterule.toml``. If ``None``,
            searches ``cauterule.toml`` in the current directory.
    """
    toml_path = Path(path) if path is not None else Path("cauterule.toml")
    data = _parse_toml(toml_path)
    config = _config_from_dict(data)
    return _apply_env_overrides(config)


def config_to_dict(config: Config) -> dict[str, Any]:
    """Serialize *config* to a TOML-compatible dict."""
    return {
        "llm": {
            "provider": config.llm.provider,
            "model": config.llm.model,
            "api_key": config.llm.api_key,
            "base_url": config.llm.base_url,
            "temperature": config.llm.temperature,
            "max_tokens": config.llm.max_tokens,
            "timeout": config.llm.timeout,
            "max_retries": config.llm.max_retries,
        },
        "paths": {"rules": config.paths.rules, "trajectories": config.paths.trajectories},
        "thresholds": {
            "precision": config.thresholds.precision,
            "recall": config.thresholds.recall,
        },
        "promotion": {"mode": config.promotion.mode},
        "redaction": {"patterns": list(config.redaction.patterns)},
        "extraction": {
            "passes": config.extraction.passes,
            "temperatures": list(config.extraction.temperatures),
            "confidence_threshold": config.extraction.confidence_threshold,
            "gate_mode": config.extraction.gate_mode,
        },
        "packs": {"min_safety_score": config.packs.min_safety_score},
        "mcp": {
            "auth_mode": config.mcp.auth_mode,
            "rate_capacity": config.mcp.rate_capacity,
            "rate_refill_per_min": config.mcp.rate_refill_per_min,
        },
        "otel": {
            "enabled": config.otel.enabled,
            "endpoint": config.otel.endpoint,
            "service_name": config.otel.service_name,
            "batch_size": config.otel.batch_size,
            "export_interval_ms": config.otel.export_interval_ms,
            "retry_max": config.otel.retry_max,
        },
        "webhook": {
            "enabled": config.webhook.enabled,
            "provider": config.webhook.provider,
            "url": config.webhook.url,
            "on_events": list(config.webhook.on_events),
            "max_attempts": config.webhook.max_attempts,
            "backoff": list(config.webhook.backoff),
            "redact": config.webhook.redact,
        },
    }
