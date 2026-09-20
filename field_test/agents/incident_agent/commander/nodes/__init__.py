"""LangGraph node functions for the incident-commander graph.

Each public function in this package is a LangGraph node that takes
``IncidentState`` and returns ``IncidentState``.  Nodes are wired into
the graph by ``graph_builder.py``.

Module-level globals (``_router``, ``_retriever``, ``_config``) are
initialised by the graph builder before the first invocation, serving
as a lightweight dependency-injection mechanism since LangGraph nodes
cannot accept constructor parameters.
"""
