"""Pipeline API: specs, submissions, timelines, and node retry (M29)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hiveplane.api.deps import (
    get_pipeline_engine,
    get_pipeline_store,
    get_tenant_context,
)
from hiveplane.core.run import AdmissionContext
from hiveplane.pipelines.engine import (
    PipelineEngine,
    PipelineRunNotFoundError,
    PipelineSpecNotFoundError,
)
from hiveplane.pipelines.models import PipelineRunHeader, PipelineTimeline
from hiveplane.pipelines.spec import PipelineSpec
from hiveplane.pipelines.store import PipelineStore
from hiveplane.tenancy import TenantContext

router = APIRouter(tags=["pipelines"])

StoreDep = Annotated[PipelineStore, Depends(get_pipeline_store)]
EngineDep = Annotated[PipelineEngine, Depends(get_pipeline_engine)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


class SubmitPipelineRequest(BaseModel):
    """A pipeline submission: optional run inputs and target context."""

    model_config = ConfigDict(extra="forbid")

    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    context: AdmissionContext = AdmissionContext.STAGING


def _require_spec(store: PipelineStore, pipeline_id: str, ctx: TenantContext) -> PipelineSpec:
    spec = store.get_spec(pipeline_id, ctx=ctx)
    if spec is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"pipeline {pipeline_id!r} not found"
        )
    return spec


@router.post("/pipelines", response_model=PipelineSpec, status_code=status.HTTP_201_CREATED)
def create_pipeline(spec: PipelineSpec, store: StoreDep, ctx: TenantDep) -> PipelineSpec:
    """Register or replace a validated pipeline spec."""
    store.save_spec(spec, ctx=ctx)
    return spec


@router.get("/pipelines", response_model=list[PipelineSpec])
def list_pipelines(store: StoreDep, ctx: TenantDep) -> list[PipelineSpec]:
    """List registered pipeline specs."""
    return store.list_specs(ctx=ctx)


@router.get("/pipelines/{pipeline_id}", response_model=PipelineSpec)
def get_pipeline(pipeline_id: str, store: StoreDep, ctx: TenantDep) -> PipelineSpec:
    """Return a pipeline spec."""
    return _require_spec(store, pipeline_id, ctx)


@router.post("/pipelines/{pipeline_id}/runs", response_model=PipelineTimeline)
def submit_pipeline_run(
    pipeline_id: str,
    request: SubmitPipelineRequest,
    store: StoreDep,
    engine: EngineDep,
    ctx: TenantDep,
) -> PipelineTimeline:
    """Submit a pipeline run and return its timeline."""
    spec = _require_spec(store, pipeline_id, ctx)
    header = engine.submit(spec, inputs=request.inputs, context=request.context, ctx=ctx)
    return engine.timeline(header.pipeline_run_id, ctx=ctx)


@router.get("/pipeline-runs", response_model=list[PipelineRunHeader])
def list_pipeline_runs(
    store: StoreDep, ctx: TenantDep, pipeline_id: str | None = None
) -> list[PipelineRunHeader]:
    """List pipeline runs, optionally filtered by pipeline."""
    return store.list_runs(pipeline_id, ctx=ctx)


@router.get("/pipeline-runs/{pipeline_run_id}", response_model=PipelineTimeline)
def get_pipeline_run(
    pipeline_run_id: str, engine: EngineDep, ctx: TenantDep
) -> PipelineTimeline:
    """Return a pipeline run's parent state and node timeline."""
    try:
        return engine.timeline(pipeline_run_id, ctx=ctx)
    except PipelineRunNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "/pipeline-runs/{pipeline_run_id}/nodes/{node_id}/retry",
    response_model=PipelineTimeline,
)
def retry_pipeline_node(
    pipeline_run_id: str,
    node_id: str,
    engine: EngineDep,
    ctx: TenantDep,
) -> PipelineTimeline:
    """Retry a failed pipeline node and return the updated timeline."""
    try:
        header = engine.retry_node(pipeline_run_id, node_id, ctx=ctx)
    except PipelineRunNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except PipelineSpecNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return engine.timeline(header.pipeline_run_id, ctx=ctx)
