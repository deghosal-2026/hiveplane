from __future__ import annotations

from oncall_rag.config import Config
from oncall_rag.retriever import retrieve


def _build_llm_client(config: Config):
    from oncall_rag.llm.omlx import OmlxClient
    from oncall_rag.llm.openai import OpenAIClient
    from oncall_rag.llm.anthropic import AnthropicClient

    if config.llm_provider == "omlx":
        return OmlxClient(endpoint=config.omlx_endpoint, model=config.omlx_model, api_key=config.omlx_api_key or "")
    elif config.llm_provider == "openai":
        if not config.openai_api_key:
            raise ValueError("OPENAI_API_KEY is not set")
        return OpenAIClient(api_key=config.openai_api_key, model=config.openai_model)
    elif config.llm_provider == "anthropic":
        if not config.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        return AnthropicClient(api_key=config.anthropic_api_key, model=config.anthropic_model)
    raise ValueError(f"Unknown LLM provider: {config.llm_provider}")


def query_runbooks(question: str, config: Config) -> str:
    chunks = retrieve(question, config)

    if not chunks:
        return "I don't have a runbook for this."

    context_parts = []
    for c in chunks:
        context_parts.append(f"{c.text}\n(source: {c.source_file}:{c.start_line}-{c.end_line})")
    context = "\n---\n".join(context_parts)

    prompt = f"Context:\n---\n{context}\n---\n\nQuestion: {question}"

    client = _build_llm_client(config)
    try:
        answer = client.generate(prompt)
    except Exception:
        answer = ""

    if answer.strip():
        sources = "\n".join(
            f"  - {c.source_file}:{c.start_line}-{c.end_line}" for c in chunks
        )
        return f"{answer}\n\nSources:\n{sources}"
    else:
        return f"Sources:\n" + "\n".join(
            f"  - {c.source_file}:{c.start_line}-{c.end_line}" for c in chunks
        )
