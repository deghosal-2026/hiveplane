"""request-triage: deterministic LangGraph demo agent for agent-exec-trace.

This package exposes a compiled LangGraph that triages support requests.  The graph
is fully deterministic: given the same seed, it always produces the same outcome,
making it a reliable fixture for SDK tests, analytics service validation, and demo
replays.

Exports:
    DEFAULT_VERSION: the version label attached to each run.
    TriageState: the TypedDict that defines the graph's state shape.
    build_graph: factory that returns a compiled ``CompiledStateGraph``.
"""

from request_triage.graph import DEFAULT_VERSION, TriageState, build_graph

__all__ = ["DEFAULT_VERSION", "TriageState", "build_graph"]
