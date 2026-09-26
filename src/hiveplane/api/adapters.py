"""Runtime adapter introspection API (M31, D25)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from hiveplane.adapters.base import (
    Adapter,
    AdapterCapabilities,
    capabilities_of,
    conformance_version_of,
)
from hiveplane.api.deps import get_adapter_catalog

router = APIRouter(tags=["adapters"])

CatalogDep = Annotated[Mapping[str, Adapter], Depends(get_adapter_catalog)]


class AdapterInfo(BaseModel):
    """An adapter's identity, contract version, and capabilities."""

    model_config = ConfigDict(extra="forbid")

    name: str
    contract_version: str
    conformance_version: str
    capabilities: AdapterCapabilities


def _info(name: str, adapter: Adapter) -> AdapterInfo:
    capabilities = capabilities_of(adapter)
    return AdapterInfo(
        name=name,
        contract_version=capabilities.contract_version,
        conformance_version=conformance_version_of(adapter),
        capabilities=capabilities,
    )


@router.get("/adapters", response_model=list[AdapterInfo])
def list_adapters(catalog: CatalogDep) -> list[AdapterInfo]:
    """List the configured runtime adapters and their capabilities."""
    return [_info(name, adapter) for name, adapter in sorted(catalog.items())]


@router.get("/adapters/{name}", response_model=AdapterInfo)
def get_adapter(name: str, catalog: CatalogDep) -> AdapterInfo:
    """Return one adapter's contract version and capabilities."""
    adapter = catalog.get(name)
    if adapter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"adapter {name!r} not configured")
    return _info(name, adapter)
