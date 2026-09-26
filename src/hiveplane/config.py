"""Typed configuration surface for the HivePlane control plane.

Environment variables are prefixed with ``HIVEPLANE_`` and nested sections are
separated with a double underscore, e.g. ``HIVEPLANE_DATABASE__HOST``. Values
are loaded from the process environment, then from a ``.env`` file in the
current working directory, with defaults as a fallback.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from hiveplane.budget.pricing import ModelPrice
from hiveplane.execution.tool_executor import DEFAULT_TOOL_FIXTURES

ENV_PREFIX = "HIVEPLANE_"
ENV_NESTED_DELIMITER = "__"


class DatabaseSettings(BaseModel):
    """PostgreSQL connection settings (system of record, DD-05)."""

    host: str = "localhost"
    port: int = Field(default=5432, ge=1, le=65535)
    name: str = "hiveplane"
    user: str = "hiveplane"
    password: SecretStr = SecretStr("hiveplane")

    @property
    def url(self) -> str:
        """SQLAlchemy URL using the psycopg driver."""
        password = self.password.get_secret_value()
        return f"postgresql+psycopg://{self.user}:{password}@{self.host}:{self.port}/{self.name}"


class RedisSettings(BaseModel):
    """Redis connection settings for queueing and signaling."""

    host: str = "localhost"
    port: int = Field(default=6379, ge=1, le=65535)
    db: int = Field(default=0, ge=0)
    password: SecretStr | None = None

    @property
    def url(self) -> str:
        """Redis URL, including credentials when a password is configured."""
        auth = ""
        if self.password is not None:
            auth = f":{self.password.get_secret_value()}@"
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class OtelSettings(BaseModel):
    """OpenTelemetry export settings (DD-06)."""

    endpoint: str = "http://localhost:4318"
    service_name: str = "hiveplane"
    trace_sampling: float = Field(default=1.0, ge=0.0, le=1.0)
    metrics_enabled: bool = True
    logs_enabled: bool = True


class CertificationThresholds(BaseModel):
    """Pass/fail thresholds scoped to a single target context."""

    min_pass_rate: float = Field(default=0.70, ge=0.0, le=1.0)
    max_critical_failures: int = Field(default=0, ge=0)
    max_p95_latency_ms: int = Field(default=30000, gt=0)
    min_production_runs_survived: int = Field(default=0, ge=0)


class CertificationSettings(BaseModel):
    """Certification thresholds and drift-detection defaults (DD-09..DD-12)."""

    staging: CertificationThresholds = Field(
        default_factory=lambda: CertificationThresholds(
            min_pass_rate=0.70,
            max_critical_failures=2,
            max_p95_latency_ms=60000,
        )
    )
    production: CertificationThresholds = Field(
        default_factory=lambda: CertificationThresholds(
            min_pass_rate=0.85,
            max_critical_failures=0,
            max_p95_latency_ms=30000,
            min_production_runs_survived=50,
        )
    )
    re_cert_interval_days: int = Field(default=14, ge=1)
    drift_threshold_pass_rate: float = Field(default=0.10, ge=0.0, le=1.0)
    max_new_failures: int = Field(default=2, ge=0)
    corpora_dir: str = "examples"
    executor: Literal["none", "reference", "adapter"] = "none"
    signing_key_file: str | None = None

    @model_validator(mode="after")
    def _production_not_weaker_than_staging(self) -> CertificationSettings:
        if self.production.min_pass_rate < self.staging.min_pass_rate:
            raise ValueError(
                "production_threshold must be >= staging_threshold: "
                f"production.min_pass_rate={self.production.min_pass_rate} < "
                f"staging.min_pass_rate={self.staging.min_pass_rate}"
            )
        return self


class SandboxDefaults(BaseModel):
    """Per-run sandbox defaults applied when a manifest omits them (DD-14)."""

    memory_mb: int = Field(default=512, gt=0)
    cpu_cores: float = Field(default=1.0, gt=0)
    wall_clock_s: int = Field(default=300, gt=0)
    egress_mode: Literal["open", "restricted", "none"] = "restricted"
    filesystem: Literal["isolated"] = "isolated"


class SandboxSettings(BaseModel):
    """Execution-sandbox configuration (DD-14)."""

    enabled: bool = True
    defaults: SandboxDefaults = Field(default_factory=SandboxDefaults)


class ExecutionSettings(BaseModel):
    """Run-store configuration (DD-05) and runtime execution selection."""

    store: Literal["memory", "json", "postgres"] = "json"
    data_dir: str = ".hiveplane/runs"
    entrypoints_root: str = "."
    adapter: Literal["none", "raw-worker", "langgraph", "auto"] = "none"
    tool_fixtures: str = DEFAULT_TOOL_FIXTURES
    sandbox_mode: Literal["in-process", "subprocess"] = "in-process"
    base_url: str = "http://127.0.0.1:8100"
    checkpoint_path: str | None = None


class FanoutSettings(BaseModel):
    """Result fan-out delivery defaults (D15)."""

    enabled: bool = True
    timeout_s: float = Field(default=10.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    slack_webhook_url: str | None = None
    generic_webhook_url: str | None = None


class UiSettings(BaseModel):
    """Operator UI settings (M22)."""

    api_url: str = "http://localhost:8100"
    host: str = "0.0.0.0"
    port: int = Field(default=3001, ge=1, le=65535)


class BudgetSettings(BaseModel):
    """Cost-table overrides for budget enforcement (DD-04).

    Defaults keep local/fake providers free; a profile can add prices and
    remove prefix exemptions to exercise budget enforcement without a cloud
    provider (e.g. the field test's over-budget scenario).
    """

    prices: dict[str, ModelPrice] = Field(default_factory=dict)
    #: None keeps the built-in ZERO_COST_PREFIXES; a list replaces them.
    zero_cost_prefixes: list[str] | None = None

    @field_validator("prices", mode="before")
    @classmethod
    def _empty_prices_become_empty_map(cls, value: object) -> object:
        """Treat an unset env var as no price overrides."""
        return {} if value == "" else value

    @field_validator("zero_cost_prefixes", mode="before")
    @classmethod
    def _empty_prefixes_become_none(cls, value: object) -> object:
        """Treat an unset env var as "use the built-in prefixes"."""
        return None if value == "" else value


class ModelSettings(BaseModel):
    """LLM provider selection and credentials (M23, #107)."""
    provider: Literal["local", "cloud", "fake"] = "fake"
    base_url: str | None = None
    api_key: SecretStr | None = None
    default_model: str | None = None
    timeout_s: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=2, ge=0)
    replay_file: str | None = None
    model_aliases: dict[str, str] = Field(default_factory=dict)

    @field_validator("model_aliases", mode="before")
    @classmethod
    def _empty_aliases_become_empty_map(cls, value: object) -> object:
        """Treat an unset env var as no aliases (compose maps it unconditionally)."""
        if value == "":
            return {}
        return value

    @field_validator("default_model", mode="before")
    @classmethod
    def _empty_default_model_becomes_none(cls, value: object) -> object:
        """Treat an unset env var as no default model."""
        if value == "":
            return None
        return value


class ReconcileSettings(BaseModel):
    """Desired-state reconciliation guardrails and cadence (M26)."""
    enabled: bool = False
    poll_interval_s: int = Field(default=60, gt=0)
    allow_destructive: bool = False
    allow_empty: bool = False
    max_destructive_per_run: int = Field(default=10, ge=0)
    require_destructive_confirmation: bool = True
    pinned_fields: list[str] = Field(default_factory=list)

    @field_validator("pinned_fields", mode="before")
    @classmethod
    def _empty_pinned_fields_become_empty_list(cls, value: object) -> object:
        """Treat an unset env var as no pinned fields."""
        return [] if value == "" else value


class RouterSettings(BaseModel):
    """Smart task router guardrails (M30)."""

    enabled: bool = False
    model: str = "router-classifier"
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    margin: float = Field(default=0.15, ge=0.0, le=1.0)
    context: Literal["sandbox", "staging", "production"] = "staging"
    max_candidates: int = Field(default=50, ge=1)


class A2ASettings(BaseModel):
    """Agent2Agent interop feature flag and allow-list (M30-07)."""

    enabled: bool = False
    plane_id: str = "hiveplane"
    allowed_planes: list[str] = Field(default_factory=list)

    @field_validator("allowed_planes", mode="before")
    @classmethod
    def _empty_planes_become_empty_list(cls, value: object) -> object:
        """Treat an unset env var as no registered remote planes."""
        return [] if value == "" else value


class TriggerSettings(BaseModel):
    """Trigger ingest, replay-protection, and rate-limit settings (M27).

    ``secrets`` maps a trigger id to its webhook shared secret. It is a stopgap
    until the secrets store (M45) resolves ``secret_ref``s; secrets never live in
    the trigger document itself.
    """

    enabled: bool = True
    webhook_skew_seconds: int = Field(default=300, gt=0)
    replay_window_seconds: int = Field(default=300, gt=0)
    global_max_per_minute: int = Field(default=600, gt=0)
    global_burst: int = Field(default=0, ge=0)
    max_catch_up: int = Field(default=10, ge=1)
    tick_seconds: int = Field(default=60, gt=0)
    secrets: dict[str, str] = Field(default_factory=dict)

    @field_validator("secrets", mode="before")
    @classmethod
    def _empty_secrets_become_empty_map(cls, value: object) -> object:
        """Treat an unset env var as no configured secrets."""
        return {} if value == "" else value


class Settings(BaseSettings):
    """Root settings object; instantiate via :func:`get_settings`."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_nested_delimiter=ENV_NESTED_DELIMITER,
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["local", "staging", "production"] = "local"
    debug: bool = False
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    otel: OtelSettings = Field(default_factory=OtelSettings)
    certification: CertificationSettings = Field(default_factory=CertificationSettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    fanout: FanoutSettings = Field(default_factory=FanoutSettings)
    ui: UiSettings = Field(default_factory=UiSettings)
    model: ModelSettings = Field(default_factory=ModelSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    reconcile: ReconcileSettings = Field(default_factory=ReconcileSettings)
    triggers: TriggerSettings = Field(default_factory=TriggerSettings)
    router: RouterSettings = Field(default_factory=RouterSettings)
    a2a: A2ASettings = Field(default_factory=A2ASettings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
