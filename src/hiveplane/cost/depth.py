"""Cost depth: estimates, tier routing, attested cache, forecasts, ROI (M50).

- Pre-admission estimates: p50/p90 cost by workload + task-type from history.
- Model-tier routing: cheap tier in staging, strong tier in production, with a
  per-workload override.
- Attested result cache: key = task input + workload + manifest version + bundle
  hash + config + tier; invalidated on re-certification; hit rate + savings.
- Budget analytics: burn forecasts and overrun probability.
- Fleet ROI: spend vs outcome with ``expensive_low_value`` flags.
- Chargeback: an immutable per-tenant/per-attribution usage ledger.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from hiveplane.cost.models import CostEvent, SpendCapExceededError
from hiveplane.fleet.cost import CostPeriodKind
from hiveplane.tenancy.context import (
    DEFAULT_CONTEXT,
    DEFAULT_TENANT_ID,
    TenantContext,
    context_for_run,
)

if TYPE_CHECKING:
    from hiveplane.cost.service import CostService


class CostDepthError(Exception):
    """Base class for cost-depth failures."""


class CacheMissError(CostDepthError):
    """Raised when a cache lookup misses (no entry, expired, or stale cert)."""


class ModelTier(StrEnum):
    """A routing tier: cheap/local vs strong/hosted."""

    CHEAP = "cheap"
    STRONG = "strong"


DEFAULT_TIER_ROUTING: dict[str, ModelTier] = {
    "staging": ModelTier.CHEAP,
    "sandbox": ModelTier.CHEAP,
    "production": ModelTier.STRONG,
}


class CostEstimate(BaseModel):
    """An expected cost for a task type, with a tolerance band (M50-01)."""

    model_config = ConfigDict(extra="forbid")

    workload_id: str
    task_type: str
    p50_usd: float = Field(ge=0.0)
    p90_usd: float = Field(ge=0.0)
    samples: int = Field(ge=0)
    confidence: str = Field(default="low")

    @property
    def estimate_usd(self) -> float:
        """Return the point estimate (p50)."""
        return self.p50_usd

    @property
    def tolerance_usd(self) -> float:
        """Return the upper tolerance band (p90)."""
        return self.p90_usd


class CostEstimator:
    """Estimates expected cost from per-workload/task-type history (M50-01)."""

    def __init__(self, *, min_samples_for_confidence: int = 5) -> None:
        self._history: dict[tuple[str, str], list[float]] = defaultdict(list)
        self._min_samples = min_samples_for_confidence

    def record(self, workload_id: str, task_type: str, cost_usd: float) -> None:
        """Record an observed cost for a workload/task-type."""
        self._history[(workload_id, task_type)].append(max(0.0, cost_usd))

    def estimate(self, workload_id: str, task_type: str) -> CostEstimate:
        """Return p50/p90 with a confidence based on sample count."""
        samples = self._history.get((workload_id, task_type), [])
        if not samples:
            return CostEstimate(
                workload_id=workload_id, task_type=task_type, p50_usd=0.0, p90_usd=0.0, samples=0
            )
        ordered = sorted(samples)
        return CostEstimate(
            workload_id=workload_id,
            task_type=task_type,
            p50_usd=_percentile(ordered, 0.50),
            p90_usd=_percentile(ordered, 0.90),
            samples=len(ordered),
            confidence="high" if len(ordered) >= self._min_samples else "low",
        )


def _percentile(ordered: list[float], fraction: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    index = fraction * (len(ordered) - 1)
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


class TierRouter:
    """Selects the model tier per environment with per-workload overrides (M50-02)."""

    def __init__(
        self,
        routing: dict[str, ModelTier] | None = None,
        overrides: dict[str, ModelTier] | None = None,
    ) -> None:
        self._routing = dict(routing or DEFAULT_TIER_ROUTING)
        self._overrides = dict(overrides or {})

    def tier_for(self, workload_id: str, environment: str) -> ModelTier:
        """Return the tier for a workload/environment (override wins)."""
        if workload_id in self._overrides:
            return self._overrides[workload_id]
        return self._routing.get(environment, ModelTier.STRONG)

    def select_model(
        self, workload_id: str, environment: str, catalog: dict[ModelTier, str]
    ) -> str:
        """Return the concrete model for a workload/environment."""
        tier = self.tier_for(workload_id, environment)
        if tier not in catalog:
            raise CostDepthError(f"no model configured for tier {tier.value!r}")
        return catalog[tier]


class CacheEntry(BaseModel):
    """An attested cached result, keyed by task + agent version + config (M50-04)."""

    model_config = ConfigDict(extra="forbid")

    cache_key: str = Field(min_length=1)
    workload_id: str = Field(min_length=1)
    manifest_version: int = Field(ge=1)
    attestation_id: str = Field(min_length=1)
    result_ref: str = Field(min_length=1)
    saved_usd: float = Field(default=0.0, ge=0.0)
    hit: bool = False
    created_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)


class CacheLookup(BaseModel):
    """The outcome of a cache lookup (hit + accounting) (M50-05)."""

    model_config = ConfigDict(extra="forbid")

    hit: bool
    entry: CacheEntry | None = None
    saved_usd: float = Field(default=0.0, ge=0.0)


def cache_key(
    *,
    task_input: str,
    workload_id: str,
    manifest_version: int,
    bundle_hash: str,
    config: str,
    tier: ModelTier,
) -> str:
    """Return a deterministic cache key over task + agent version + config + tier."""
    material = "|".join(
        [task_input, workload_id, str(manifest_version), bundle_hash, config, tier.value]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ResultCache:
    """An attested result cache, invalidated on re-certification (M50-04/05)."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        default_ttl_s: int = 86400,
        min_attestation: Callable[[str, str], bool] | None = None,
    ) -> None:
        self._entries: dict[str, CacheEntry] = {}
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ttl = default_ttl_s
        self._min_attestation = min_attestation
        self._hits = 0
        self._lookups = 0
        self._savings = 0.0

    def store(
        self,
        *,
        key: str,
        workload_id: str,
        manifest_version: int,
        attestation_id: str,
        result_ref: str,
        saved_usd: float = 0.0,
        ttl_seconds: int | None = None,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> CacheEntry:
        """Store a result; requires a valid attestation for the agent version."""
        if self._min_attestation is not None and not self._min_attestation(
            attestation_id, workload_id
        ):
            raise CostDepthError("cache store requires a valid attestation")
        now = self._clock()
        entry = CacheEntry(
            cache_key=key,
            workload_id=workload_id,
            manifest_version=manifest_version,
            attestation_id=attestation_id,
            result_ref=result_ref,
            saved_usd=saved_usd,
            created_at=now,
            expires_at=now
            + timedelta(seconds=ttl_seconds if ttl_seconds is not None else self._ttl),
            tenant_id=tenant_id,
        )
        self._entries[key] = entry
        return entry

    def lookup(
        self,
        key: str,
        *,
        manifest_version: int,
        attestation_id: str | None = None,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> CacheLookup:
        """Return a hit when the entry is live, same-tenant, and its attestation holds.

        When an attestation validator is configured the lookup must carry the
        caller's attestation and it must validate; this is what makes a
        re-certified (same manifest version, new attestation) entry a miss.
        """
        self._lookups += 1
        entry = self._entries.get(key)
        now = self._clock()
        if (
            entry is None
            or entry.tenant_id != tenant_id
            or (entry.expires_at is not None and entry.expires_at <= now)
            or entry.manifest_version != manifest_version
        ):
            return CacheLookup(hit=False)
        if self._min_attestation is not None and (
            attestation_id is None
            or not self._min_attestation(attestation_id, entry.workload_id)
        ):
            return CacheLookup(hit=False)
        if attestation_id is not None and entry.attestation_id != attestation_id:
            return CacheLookup(hit=False)
        self._hits += 1
        self._savings += entry.saved_usd
        return CacheLookup(hit=True, entry=entry, saved_usd=entry.saved_usd)

    def invalidate_workload(self, workload_id: str, *, current_version: int) -> int:
        """Evict entries whose manifest version is stale after a re-cert."""
        stale = [
            key
            for key, entry in self._entries.items()
            if entry.workload_id == workload_id and entry.manifest_version != current_version
        ]
        for key in stale:
            del self._entries[key]
        return len(stale)

    @property
    def hit_rate(self) -> float:
        """Return the cache hit rate over all lookups."""
        return (self._hits / self._lookups) if self._lookups else 0.0

    @property
    def savings_usd(self) -> float:
        """Return cumulative savings credited by cache hits."""
        return self._savings


class BurnForecast(BaseModel):
    """A burn forecast with overrun prediction for a period (M50-07)."""

    model_config = ConfigDict(extra="forbid")

    spent_usd: float = Field(ge=0.0)
    elapsed_fraction: float = Field(ge=0.0, le=1.0)
    projected_usd: float = Field(ge=0.0)
    projected_overrun_usd: float = Field(ge=0.0)
    overrun_probability: float = Field(ge=0.0, le=1.0)


def forecast(
    *,
    spent_usd: float,
    elapsed_fraction: float,
    limit_usd: float | None,
) -> BurnForecast:
    """Extrapolate period burn and predict overrun against a cap (M50-07)."""
    projected = spent_usd if elapsed_fraction <= 0 else spent_usd / elapsed_fraction
    overrun = max(0.0, projected - limit_usd) if limit_usd is not None else 0.0
    if limit_usd is None or limit_usd <= 0:
        probability = 0.0
    elif spent_usd >= limit_usd:
        probability = 1.0
    elif projected >= limit_usd:
        probability = min(1.0, projected / limit_usd - 1.0 + 0.5)
    else:
        probability = max(0.0, projected / limit_usd * 0.5)
    return BurnForecast(
        spent_usd=spent_usd,
        elapsed_fraction=elapsed_fraction,
        projected_usd=projected,
        projected_overrun_usd=overrun,
        overrun_probability=probability,
    )


class RoiRow(BaseModel):
    """Spend vs outcome for one workload, with an evidence-backed flag (M50-08)."""

    model_config = ConfigDict(extra="forbid")

    workload_id: str
    spend_usd: float = Field(ge=0.0)
    value_usd: float = Field(ge=0.0)
    completed_tasks: int = Field(ge=0)
    roi: float = 0.0
    expensive_low_value: bool = False
    evidence: list[str] = Field(default_factory=list)


class RoiReport(BaseModel):
    """The fleet ROI report: per-workload rows and flags (M50-08)."""

    model_config = ConfigDict(extra="forbid")

    rows: list[RoiRow] = Field(default_factory=list)
    total_spend_usd: float = Field(default=0.0, ge=0.0)
    total_value_usd: float = Field(default=0.0, ge=0.0)
    fleet_roi: float = 0.0

    @property
    def expensive_low_value(self) -> list[RoiRow]:
        """Return the flagged expensive-but-low-value workloads."""
        return [row for row in self.rows if row.expensive_low_value]


def build_roi(
    outcomes: list[dict[str, Any]],
    *,
    spend_threshold_usd: float = 10.0,
    roi_threshold: float = 1.0,
) -> RoiReport:
    """Flag workloads that are expensive relative to their value/completion."""
    rows: list[RoiRow] = []
    total_spend = 0.0
    total_value = 0.0
    for item in outcomes:
        spend = float(item.get("spend_usd", 0.0))
        value = float(item.get("value_usd", 0.0))
        completed = int(item.get("completed_tasks", 0))
        roi = (value / spend) if spend > 0 else 0.0
        evidence: list[str] = []
        low_value = spend >= spend_threshold_usd and roi < roi_threshold
        if low_value:
            evidence.append(f"spend=${spend:.2f} >= ${spend_threshold_usd:.2f}")
            evidence.append(f"roi={roi:.2f} < {roi_threshold:.2f}")
            if completed == 0:
                evidence.append("no completed tasks")
        rows.append(
            RoiRow(
                workload_id=str(item.get("workload_id", "")),
                spend_usd=spend,
                value_usd=value,
                completed_tasks=completed,
                roi=roi,
                expensive_low_value=low_value,
                evidence=evidence,
            )
        )
        total_spend += spend
        total_value += value
    return RoiReport(
        rows=sorted(rows, key=lambda row: row.workload_id),
        total_spend_usd=total_spend,
        total_value_usd=total_value,
        fleet_roi=(total_value / total_spend) if total_spend else 0.0,
    )


class ChargebackRow(BaseModel):
    """One chargeback line: attributed usage for billing (M50-06)."""

    model_config = ConfigDict(extra="forbid")

    attribution_key: str
    cost_usd: float = Field(ge=0.0)
    events: int = Field(ge=0)


class ChargebackLedger:
    """An immutable per-tenant chargeback export (M50-06)."""

    def __init__(self) -> None:
        self._events: list[CostEvent] = []

    def append(self, event: CostEvent) -> None:
        """Append a usage fact to the ledger (append-only)."""
        self._events.append(event.model_copy(deep=True))

    def export(self, tenant_id: str) -> list[ChargebackRow]:
        """Return usage grouped by tenant/team/workload attribution key."""
        totals: dict[str, tuple[float, int]] = {}
        for event in self._events:
            if event.tenant_id != tenant_id:
                continue
            key = f"{event.tenant_id}/{event.team_id}/{event.workload_id}"
            cost, count = totals.get(key, (0.0, 0))
            totals[key] = (cost + event.cost_usd, count + 1)
        return [
            ChargebackRow(attribution_key=key, cost_usd=cost, events=count)
            for key, (cost, count) in sorted(totals.items())
        ]

    def digest(self, tenant_id: str) -> str:
        """Return a reproducible digest of a tenant's chargeback ledger."""
        payload = json.dumps(
            [row.model_dump(mode="json") for row in self.export(tenant_id)],
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CostAdmission(BaseModel):
    """The cost part of an admission decision: estimate + cap check (M50-01/03)."""

    model_config = ConfigDict(extra="forbid")

    admitted: bool
    reason: str | None = None
    estimate: CostEstimate


def evaluate_admission(
    estimator: CostEstimator,
    service: CostService,
    *,
    tenant_id: str,
    kind: CostPeriodKind,
    workload_id: str,
    task_type: str,
    at: datetime | None = None,
    projected_usd: float = 0.0,
    ctx: TenantContext = DEFAULT_CONTEXT,
) -> CostAdmission:
    """Estimate expected cost and hard-stop admission when a tenant cap is hit."""
    estimate = estimator.estimate(workload_id, task_type)
    projection = max(projected_usd, estimate.p90_usd)
    try:
        service.check_cap(
            tenant_id,
            kind,
            at=at,
            projected_usd=projection,
            ctx=ctx if ctx.scopes(tenant_id) else context_for_run(tenant_id),
        )
    except SpendCapExceededError:
        return CostAdmission(
            admitted=False, reason="tenant_spend_cap_exceeded", estimate=estimate
        )
    return CostAdmission(admitted=True, estimate=estimate)
