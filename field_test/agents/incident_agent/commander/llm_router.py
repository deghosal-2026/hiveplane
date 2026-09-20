"""LLM routing, cost tracking, and observability.

Components
----------
- ``LLMRouter`` — select and invoke the right model per task type.
- ``CostTracker`` — accumulate per-node token counts and costs.
- ``LLMObserver`` — persist every LLM call to a JSONL log file.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from incident_commander.config import Config, LLMConfig
from incident_commander.models.state import CostReport, NodeCost

logger = logging.getLogger(__name__)

# A mock callable that accepts (prompt, task) and returns (response, info_dict).
# Note: the second parameter is the task name (e.g. "analysis", "comms"),
# not the model name — this lets mocks return task-appropriate responses.
MockLLM = Callable[[str, str], tuple[str, dict[str, Any]]]


class CostTracker:
    """Accumulates per-node token and cost data for a single session."""

    def __init__(self) -> None:
        """Initialize an empty cost tracker."""
        self._nodes: list[NodeCost] = []

    def record_call(
        self,
        node_name: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        latency_ms: int,
    ) -> None:
        """Record a single LLM call's token usage and cost."""
        total = input_tokens + output_tokens
        self._nodes.append(
            NodeCost(
                node_name=node_name,
                llm_model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total,
                estimated_cost_usd=cost_usd,
                latency_ms=latency_ms,
            )
        )

    def get_report(self, session_id: str = "") -> CostReport:
        """Aggregate all recorded calls into a ``CostReport``."""
        # All values computed from the in-memory _nodes list; no external state
        total_input = sum(n.input_tokens for n in self._nodes)
        total_output = sum(n.output_tokens for n in self._nodes)
        total_cost = sum(n.estimated_cost_usd for n in self._nodes)
        models_used = sorted({n.llm_model for n in self._nodes})
        return CostReport(
            session_id=session_id,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_tokens=total_input + total_output,
            total_estimated_cost_usd=total_cost,
            per_node=list(self._nodes),
            models_used=models_used,
        )


class LLMObserver:
    """Logs every LLM call to a JSONL file for post-hoc analysis.

    Each line in the log file is a JSON object with call metadata.
    The log directory is created automatically if it does not exist.
    """

    def __init__(self, log_dir: str | Path = "~/.incident-commander/logs") -> None:
        """Initialize the observer with a log directory path."""
        self._log_dir = Path(log_dir).expanduser()

    def _ensure_log_dir(self) -> None:
        """Create the log directory (including parents) if it does not exist."""
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def log_call(
        self,
        call_id: str,
        node_name: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: float,
        latency_ms: int,
        prompt: str = "",
        error: str | None = None,
        response: str = "",
    ) -> None:
        """Write one call record to the JSONL log."""
        self._ensure_log_dir()
        record = {
            "call_id": call_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "node_name": node_name,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "estimated_cost_usd": estimated_cost_usd,
            "latency_ms": latency_ms,
            "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest(),
            "error": error,
            "response": response[:2000],
        }
        log_path = self._log_dir / "llm-calls.jsonl"
        try:
            with open(log_path, "a") as f:
                f.write(json.dumps(record) + "\n")
        except OSError as exc:
            logger.warning("Failed to write LLM call log: %s", exc)


class LLMRouter:
    """Routes LLM calls to the appropriate model based on task type.

    Supports a ``mock_llm`` callable for testing without real API calls.
    """

    def __init__(
        self,
        config: Config | None = None,
        mock_llm: MockLLM | None = None,
    ) -> None:
        """Initialize the router with optional config and mock."""
        self._config = config or Config()
        self._mock_llm = mock_llm
        self.cost_tracker = CostTracker()
        self.observer = LLMObserver(log_dir=self._config.log_dir)

    def _resolve_model(self, task: str) -> tuple[str, str | None]:
        """Return (model_name, base_url) for the given task.

        Task types: ``analysis``, ``comms``, ``postmortem``.
        Comms and postmortem fall back to the analysis model when not
        explicitly configured — this allows a single-model setup while
        still supporting differentiated routing for production use.
        """
        llm: LLMConfig = self._config.llm
        if task == "comms" and llm.comms_model:
            return llm.comms_model, llm.comms_base_url
        if task == "postmortem" and llm.postmortem_model:
            return llm.postmortem_model, llm.postmortem_base_url
        return llm.analysis_model, llm.analysis_base_url

    def _estimate_cost(
        self, model: str, input_tokens: int, output_tokens: int
    ) -> float:
        """Compute estimated USD cost from the pricing table."""
        pricing = self._config.llm.model_pricing
        # Pricing is per 1M tokens; unknown models default to free
        # so the cost report never fails due to missing pricing entries.
        entry = pricing.get(model, {"input": 0.0, "output": 0.0})
        cost = (input_tokens / 1_000_000 * entry["input"]) + (
            output_tokens / 1_000_000 * entry["output"]
        )
        return round(cost, 6)

    def generate(
        self,
        prompt: str,
        task: str = "analysis",
        model_override: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Call an LLM and return ``(response_text, info_dict)``.

        The info dict contains call metadata suitable for building an
        ``LLMCall`` record.

        Args:
            prompt: The full prompt to send to the LLM.
            task: One of ``"analysis"``, ``"comms"``, ``"postmortem"``.
            model_override: If set, bypass task-based routing and use
                this model directly.

        Returns:
            A ``(response_text, info)`` tuple where ``info`` contains:
            ``model``, ``input_tokens``, ``output_tokens``,
            ``estimated_cost_usd``, ``latency_ms``, ``call_id``, and
            ``error`` (or ``None``).

        
        """
        model = model_override or self._resolve_model(task)[0]
        call_id = uuid.uuid4().hex[:12]  # short unique ID for JSONL cross-ref
        start = time.monotonic()  # wall-clock for latency measurement

        if self._mock_llm:
            # Mock path: no real API call, tokens come from mock_info.
            response, mock_info = self._mock_llm(prompt, task)
            latency = int((time.monotonic() - start) * 1000)
            input_tokens = mock_info.get("input_tokens", 0)
            output_tokens = mock_info.get("output_tokens", 0)
            error = mock_info.get("error")
        else:
            # Real LLM call via OpenAI-compatible endpoint
            model, base_url = self._resolve_model(task)
            url = f"{base_url}/chat/completions"
            headers = {"Content-Type": "application/json"}
            api_key = os.environ.get("LLM_API_KEY", "")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            try:
                resp = httpx.post(
                    url,
                    headers=headers,
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt[:8000]}],
                        "max_tokens": 1024,
                        "temperature": 0.7,
                    },
                    timeout=300,
                )
                resp.raise_for_status()
                data = resp.json()
                response = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                input_tokens = usage.get("prompt_tokens", 0)
                output_tokens = usage.get("completion_tokens", 0)
                error = None
            except Exception as exc:
                response = ""
                input_tokens = 0
                output_tokens = 0
                error = f"LLM call failed: {exc}"
                logger.warning("LLM call to %s failed: %s", url, exc)
            latency = int((time.monotonic() - start) * 1000)

        # Record cost and log the call for observability
        cost = self._estimate_cost(model, input_tokens, output_tokens)
        self.cost_tracker.record_call(
            node_name=task,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            latency_ms=latency,
        )
        self.observer.log_call(
            call_id=call_id,
            node_name=task,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
            latency_ms=latency,
            prompt=prompt,
            error=error,
            response=response,
        )

        info = {
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": cost,
            "latency_ms": latency,
            "call_id": call_id,
            "error": error,
        }
        return response, info
