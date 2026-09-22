"""The token-guarded sandbox channel (M23, #110, D11).

A sandboxed child process cannot share Python objects with the control plane, so
it reaches the boundary over localhost HTTP. Each endpoint requires the per-run
token minted at spawn time; the tool call routes through the :class:`ToolGateway`
(policy + egress + shaping), the model call through the provider seam (identity
check + pricing + usage), and usage/result/state flow to the :class:`RunService`.
"""

from __future__ import annotations

import hmac
import threading
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hiveplane.budget.pricing import CostTable
from hiveplane.core.run import Run, RunState
from hiveplane.core.spec import validate_model_identity
from hiveplane.core.usage import UsageReport
from hiveplane.execution.service import RunService
from hiveplane.execution.tools import ToolCallRequest, ToolCallResult, ToolGateway
from hiveplane.llm.models import CompletionRequest, CompletionResult, Message
from hiveplane.llm.provider import LLMProvider

_RUN_TOKEN_HEADER = "X-HivePlane-Run-Token"


class SandboxChannel:
    """Tracks the per-run token that authorizes the sandbox channel."""

    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}
        self._lock = threading.Lock()

    def mint(self, run_id: str) -> str:
        """Create and store a token for a run."""
        token = f"tok-{uuid4().hex}"
        with self._lock:
            self._tokens[run_id] = token
        return token

    def verify(self, run_id: str, token: str) -> bool:
        """Return True when ``token`` is the run's current token."""
        with self._lock:
            expected = self._tokens.get(run_id)
        return expected is not None and hmac.compare_digest(expected, token)

    def revoke(self, run_id: str) -> None:
        """Drop a run's token so the channel can no longer be used."""
        with self._lock:
            self._tokens.pop(run_id, None)


class ModelCallRequest(BaseModel):
    """A governed model call from the sandboxed child."""

    model_config = ConfigDict(extra="forbid")

    messages: list[Message] = Field(min_length=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)


class ResultRequest(BaseModel):
    """The final agent result, reported back to the control plane."""

    model_config = ConfigDict(extra="forbid")

    result: dict[str, JsonValue]


class UsageRequest(BaseModel):
    """Non-model usage reported by the child (priced server-side)."""

    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)


def _require_token(request: Request, run_id: str) -> None:
    channel = request.app.state.sandbox_channel
    token = request.headers.get(_RUN_TOKEN_HEADER)
    if token is None or not channel.verify(run_id, token):
        raise HTTPException(status_code=401, detail="invalid sandbox token")


router = APIRouter(prefix="/internal/sandbox", tags=["internal.sandbox"])


@router.post("/{run_id}/tool-call", response_model=ToolCallResult)
def sandbox_tool_call(
    run_id: Annotated[str, Path], payload: ToolCallRequest, request: Request
) -> ToolCallResult:
    """Route a tool call through the control-plane boundary."""
    _require_token(request, run_id)
    gateway: ToolGateway = request.app.state.tool_gateway
    return gateway.invoke(run_id, payload)


@router.post("/{run_id}/model", response_model=CompletionResult)
def sandbox_model_call(
    run_id: Annotated[str, Path], payload: ModelCallRequest, request: Request
) -> CompletionResult:
    """Invoke the bound model through the provider seam, recording usage."""
    _require_token(request, run_id)
    run_service: RunService = request.app.state.run_service
    provider: LLMProvider = request.app.state.provider
    cost_table: CostTable = request.app.state.cost_table
    bound = run_service.get(run_id).model_identity
    if bound is None:
        raise HTTPException(status_code=422, detail="run has no bound model identity")
    response = provider.complete(
        CompletionRequest(
            messages=payload.messages,
            model=bound,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
        )
    )
    canonical = validate_model_identity(response.model_identity)
    if canonical != bound:
        raise HTTPException(
            status_code=422,
            detail=f"model identity mismatch: expected {bound!r}, got {canonical!r}",
        )
    cost = cost_table.price(
        canonical, response.usage.input_tokens, response.usage.output_tokens
    )
    run_service.record_usage(
        run_id,
        UsageReport(
            run_id=run_id,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            tool_calls=0,
            cost_usd=cost,
            timestamp=datetime.now(UTC),
            model_identity=canonical,
        ),
    )
    return CompletionResult(
        content=response.content,
        model_identity=canonical,
        usage=response.usage,
        finish_reason=response.finish_reason,
    )


@router.post("/{run_id}/usage", response_model=Run)
def sandbox_usage(
    run_id: Annotated[str, Path], payload: UsageRequest, request: Request
) -> Run:
    """Record non-model usage for server-side budget enforcement."""
    _require_token(request, run_id)
    run_service: RunService = request.app.state.run_service
    return run_service.record_usage(
        run_id,
        UsageReport(
            run_id=run_id,
            input_tokens=payload.input_tokens,
            output_tokens=payload.output_tokens,
            tool_calls=payload.tool_calls,
            cost_usd=payload.cost_usd,
            timestamp=datetime.now(UTC),
            model_identity=run_service.get(run_id).model_identity,
        ),
    )


@router.post("/{run_id}/result", response_model=Run)
def sandbox_result(
    run_id: Annotated[str, Path], payload: ResultRequest, request: Request
) -> Run:
    """Complete the run with the agent's final result."""
    _require_token(request, run_id)
    run_service: RunService = request.app.state.run_service
    return run_service.transition(
        run_id, RunState.COMPLETED, actor="adapter", result=payload.result
    )


@router.get("/{run_id}/control")
def sandbox_control(run_id: Annotated[str, Path], request: Request) -> dict[str, object]:
    """Report the run's state so the child can checkpoint cooperatively."""
    _require_token(request, run_id)
    run_service: RunService = request.app.state.run_service
    run = run_service.get(run_id)
    return {
        "state": run.state.value,
        "paused": run.state is RunState.PAUSED,
        "cancelled": run.state is RunState.CANCELLED,
    }
