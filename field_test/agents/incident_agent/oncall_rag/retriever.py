from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma


@dataclass
class RetrievedChunk:
    text: str
    source_file: str
    start_line: int
    end_line: int
    score: float


_embeddings_cache: dict[str, Any] = {}
_vectorstore_cache: dict[tuple[str, str], Any] = {}


def _get_embeddings(model_name: str):
    if model_name not in _embeddings_cache:
        _embeddings_cache[model_name] = HuggingFaceEmbeddings(model_name=model_name)
    return _embeddings_cache[model_name]


def _get_vectorstore(config) -> Any:
    key = (config.chroma_path, "runbooks")
    if key not in _vectorstore_cache:
        _vectorstore_cache[key] = Chroma(
            embedding_function=_get_embeddings(config.embedding_model),
            persist_directory=config.chroma_path,
            collection_name="runbooks",
        )
    return _vectorstore_cache[key]


def retrieve(query: str, config) -> list[RetrievedChunk]:
    vectorstore = _get_vectorstore(config)
    results = vectorstore.similarity_search_with_score(query, k=config.top_k)
    chunks = []
    for doc, score in results:
        meta = doc.metadata
        chunks.append(
            RetrievedChunk(
                text=doc.page_content,
                source_file=meta.get("source_file", "unknown"),
                start_line=int(meta.get("start_line", 0)),
                end_line=int(meta.get("end_line", 0)),
                score=score,
            )
        )
    return chunks
