"""Configuration: Config and LLMConfig models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    """LLM routing configuration."""

    analysis_model: str = "ollama/qwen2.5-coder:7b"  # primary model for root-cause analysis
    analysis_base_url: str = "http://localhost:11434/v1"  # Ollama OpenAI-compatible endpoint
    # Task-specific overrides: None means fall back to analysis_model/analysis_base_url
    # in LLMRouter._resolve_model(), enabling a single expensive model for analysis
    # while using a cheaper model for routine comms and postmortem generation.
    comms_model: str | None = None
    comms_base_url: str | None = None
    postmortem_model: str | None = None
    postmortem_base_url: str | None = None

    # Per-1M-token USD pricing used for cost estimation; input = prompt tokens,
    # output = completion tokens. Unknown models default to free (0.0).
    model_pricing: dict[str, dict[str, float]] = Field(
        default_factory=lambda: {
            "ollama/qwen2.5-coder:7b": {"input": 0.0, "output": 0.0},  # local model, free
            "gpt-4o-mini": {"input": 0.15, "output": 0.60},
            "gpt-4o": {"input": 2.50, "output": 10.00},
            "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
        }
    )


class Config(BaseModel):
    """Top-level configuration."""

    # "simulate" auto-approves drafts; "run" requires human approval gates
    mode: Literal["simulate", "run"] = "simulate"
    llm: LLMConfig = Field(default_factory=LLMConfig)

    # Minutes between stakeholder updates, keyed by incident severity.
    # The cadence_timer_node reads this dict to decide when to skip drafting
    # (if not enough time has elapsed since the last update).
    cadence: dict[str, int] = Field(
        default_factory=lambda: {
            "SEV1": 5,
            "SEV2": 15,
            "SEV3": 30,
        }
    )

    # Minimum RAG confidence to surface a suggestion without manual review
    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    # Look-back window for correlating deploys (PRs) to the alert timestamp
    deploy_correlation_window_minutes: int = Field(default=30, ge=1)

    # Qdrant vector DB for RAG runbook/historical-incident retrieval.
    # None disables RAG retrieval entirely — the graph still runs but skips
    # the retrieve_runbooks node outcomes.
    qdrant_url: str | None = None
    qdrant_collection: str = "runbooks"

    # GitHub PAT for fetching merged PRs via REST API;
    # None falls back to unauthenticated requests (rate-limited).
    github_token: str | None = None

    # Tilde paths expanded at runtime by Path.expanduser() in SessionManager
    session_dir: str = "~/.incident-commander/sessions"
    log_dir: str = "~/.incident-commander/logs"

    output_format: Literal["markdown", "json"] = "markdown"
