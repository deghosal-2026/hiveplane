"""Smart task router: certified-catalog routing with guardrails (M30)."""

from __future__ import annotations

from hiveplane.router.catalog import (
    CatalogCandidate,
    CertifiedCatalog,
    RegistryCatalog,
    StaticCatalog,
)
from hiveplane.router.classifier import (
    ClassificationResult,
    ClassifierError,
    LLMTaskClassifier,
    TaskClassifier,
)
from hiveplane.router.engine import RouterEngine, hash_task
from hiveplane.router.models import (
    RefusalReason,
    RouteDecision,
    RouteOutcome,
    RouterCandidate,
    RouteRequest,
    RouterNotConfiguredError,
)
from hiveplane.router.store import (
    InMemoryRouterStore,
    PostgresRouterStore,
    RouterStore,
    build_router_store,
)

__all__ = [
    "CatalogCandidate",
    "CertifiedCatalog",
    "ClassificationResult",
    "ClassifierError",
    "InMemoryRouterStore",
    "LLMTaskClassifier",
    "PostgresRouterStore",
    "RefusalReason",
    "RegistryCatalog",
    "RouteDecision",
    "RouteOutcome",
    "RouteRequest",
    "RouterCandidate",
    "RouterEngine",
    "RouterNotConfiguredError",
    "RouterStore",
    "StaticCatalog",
    "TaskClassifier",
    "build_router_store",
    "hash_task",
]
