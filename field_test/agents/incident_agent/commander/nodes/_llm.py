"""Shared LLM router instance for graph nodes.

The graph builder calls ``init_llm_router`` once during construction.
Individual node modules import ``get_llm_router`` to access the
configured router (or mock) without explicit wiring.
"""

from __future__ import annotations

from incident_commander.llm_router import LLMRouter

# Module-level singleton — set once by the graph builder.
# LangGraph nodes can't use constructor injection, so we use a module-level
# global as a simple dependency-injection workaround.
_router: LLMRouter | None = None


def init_llm_router(router: LLMRouter) -> None:
    """Set the module-level LLM router."""
    global _router  # noqa: PLW0603
    _router = router


def get_llm_router() -> LLMRouter:
    """Return the module-level LLM router.

    Raises:
        RuntimeError: If ``init_llm_router`` was never called.

    """
    if _router is None:
        # Only raised if the graph builder skipped initialization — a
        # programming error that should be caught in CI, not at runtime.
        raise RuntimeError(
            "LLM router not initialized — call init_llm_router() first"
        )
    return _router
