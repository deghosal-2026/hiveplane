"""Desired-state source references and change detection (M26-06, D22).

A source is a read-only declaration of where desired state lives: a local
directory or a git repository pinned to a ref. The controller loads it under the
single-writer lock; this module also provides the polling helper and webhook
HMAC verification used to trigger reconcile passes on change.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.fleet.reconcile import SpecSource
from hiveplane.reconcile.loader import DesiredStateLoader, GitSource, LoaderError
from hiveplane.reconcile.models import (
    DesiredSet,
    ReconcileMode,
    ReconcileRun,
    ReconcileStatusView,
)
from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class WebhookSignatureError(Exception):
    """Raised when a reconcile webhook fails HMAC verification."""


class SourceKind(StrEnum):
    """Where a source's desired state lives."""

    DIRECTORY = "directory"
    GIT = "git"


class SourceRef(BaseModel):
    """A declared, read-only desired-state source."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=128)
    kind: SourceKind
    path: str | None = Field(default=None, max_length=2000)
    git: GitSource | None = None
    poll_interval_s: int = Field(default=60, gt=0)
    webhook_secret_ref: str | None = Field(default=None, max_length=253)

    def load(
        self,
        loader: DesiredStateLoader,
        *,
        secret_resolver: Callable[[str], str] | None = None,
    ) -> DesiredSet:
        """Load this source's desired set, read-only."""
        if self.kind is SourceKind.DIRECTORY:
            if self.path is None:
                raise LoaderError(f"directory source {self.source_id!r} has no path")
            return loader.load_directory(
                self.path, source_id=self.source_id, source=SpecSource.GIT
            )
        if self.git is None:
            raise LoaderError(f"git source {self.source_id!r} has no repository")
        return loader.load_git(
            self.git, source_id=self.source_id, secret_resolver=secret_resolver
        )


def verify_webhook_signature(secret: str, body: bytes, signature: str) -> bool:
    """Verify a hex HMAC-SHA256 webhook signature over ``body`` in constant time."""
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class ControllerLike(Protocol):
    """The controller surface the watcher drives (avoids a circular import)."""

    def reconcile(
        self,
        source: SourceRef,
        *,
        mode: ReconcileMode = ...,
        confirmed: bool = ...,
        tenant_id: str = ...,
        secret_resolver: Callable[[str], str] | None = ...,
    ) -> ReconcileRun: ...

    def status(
        self, source_id: str, *, tenant_id: str = ...
    ) -> ReconcileStatusView: ...


class SourceWatcher:
    """Triggers reconcile passes on poll or verified webhook change (M26-06).

    Poll compares the source's resolved revision against the last reconciled
    revision and only acts when it changed. Webhooks verify an HMAC signature
    before acting. Neither path ever writes back to the source.
    """

    def __init__(
        self,
        controller: ControllerLike,
        loader: DesiredStateLoader | None = None,
    ) -> None:
        self._controller = controller
        self._loader = loader or DesiredStateLoader()

    def poll(
        self,
        source: SourceRef,
        *,
        mode: ReconcileMode = ReconcileMode.APPLY,
        confirmed: bool = False,
        tenant_id: str = DEFAULT_TENANT_ID,
        secret_resolver: Callable[[str], str] | None = None,
    ) -> ReconcileRun | None:
        """Reconcile ``source`` when its revision changed since the last pass."""
        desired = source.load(self._loader, secret_resolver=secret_resolver)
        last_revision = self._controller.status(
            source.source_id, tenant_id=tenant_id
        ).last_revision
        if last_revision == desired.revision:
            return None
        return self._controller.reconcile(
            source,
            mode=mode,
            confirmed=confirmed,
            tenant_id=tenant_id,
            secret_resolver=secret_resolver,
        )

    def webhook(
        self,
        source: SourceRef,
        body: bytes,
        signature: str,
        secret: str,
        *,
        mode: ReconcileMode = ReconcileMode.APPLY,
        confirmed: bool = False,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> ReconcileRun:
        """Verify an HMAC webhook signature, then reconcile the source."""
        if not verify_webhook_signature(secret, body, signature):
            raise WebhookSignatureError(
                f"invalid webhook signature for {source.source_id!r}"
            )
        return self._controller.reconcile(
            source, mode=mode, confirmed=confirmed, tenant_id=tenant_id
        )
