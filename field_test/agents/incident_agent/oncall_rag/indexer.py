from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma


@dataclass
class Chunk:
    text: str
    source_file: str
    start_line: int
    end_line: int


_embeddings_cache: dict[str, Any] = {}


def _walk_markdown(path: str) -> list[Path]:
    root = Path(path)
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {path}")
    files = []
    for f in root.rglob("*.md"):
        if f.is_file() and f.stat().st_size <= 1_000_000:
            files.append(f)
    return files


def _make_embedding_model(model_name: str):
    if model_name not in _embeddings_cache:
        _embeddings_cache[model_name] = HuggingFaceEmbeddings(model_name=model_name)
    return _embeddings_cache[model_name]


def _text_to_chunks(text: str, file: Path, segments: list[str]) -> list[Chunk]:
    chunks = []
    offset = 0
    for seg in segments:
        if not seg.strip():
            offset += len(seg)
            continue
        pos = text.find(seg, offset)
        if pos == -1:
            start = 1
        else:
            start = text[:pos].count("\n") + 1
            offset = pos + len(seg)
        end = start + seg.count("\n")
        chunks.append(Chunk(text=seg, source_file=str(file), start_line=start, end_line=end))
    return chunks


def _split_recursive(text: str, file: Path, chunk_size: int, chunk_overlap: int) -> list[Chunk]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " "],
    )
    return _text_to_chunks(text, file, splitter.split_text(text))


def _split_fixed_size(text: str, file: Path, chunk_size: int, chunk_overlap: int) -> list[Chunk]:
    segments = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            space = text.rfind(" ", start, end + 1)
            if space != -1 and space > start:
                end = space
        segments.append(text[start:end])
        next_start = end - chunk_overlap
        start = next_start if next_start > start else end
    return _text_to_chunks(text, file, segments)


def _split_header(text: str, file: Path, chunk_size: int, chunk_overlap: int) -> list[Chunk]:
    lines = text.split("\n")
    sections = []
    current = []
    for line in lines:
        if re.match(r"^#{1,6}\s", line) and current:
            sections.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        sections.append("\n".join(current))
    if not sections:
        sections = [text]

    merged = []
    buffer = ""
    for sec in sections:
        if len(buffer) + len(sec) <= chunk_size * 1.5 or not buffer:
            buffer += ("\n" if buffer else "") + sec
        else:
            merged.append(buffer)
            buffer = sec
    if buffer:
        merged.append(buffer)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " "],
    )
    final_segments = []
    for sec in merged:
        if len(sec) > chunk_size:
            final_segments.extend(splitter.split_text(sec))
        else:
            final_segments.append(sec)
    return _text_to_chunks(text, file, final_segments)


def _split_file(file: Path, chunk_size: int, chunk_overlap: int, strategy: str) -> list[Chunk]:
    text = file.read_text(encoding="utf-8", errors="replace")
    if strategy == "fixed-size":
        return _split_fixed_size(text, file, chunk_size, chunk_overlap)
    elif strategy == "header-split":
        return _split_header(text, file, chunk_size, chunk_overlap)
    return _split_recursive(text, file, chunk_size, chunk_overlap)


def _file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _get_indexed_files(vectorstore) -> set[str]:
    try:
        existing = vectorstore.get()
        return {m.get("source_file", "") for m in existing.get("metadatas", [])}
    except Exception:
        return set()


def index_documents(
    path: str,
    config,
    rebuild: bool = False,
) -> dict:
    files = _walk_markdown(path)
    if not files:
        return {"files_indexed": 0, "chunks_created": 0}

    embeddings = _make_embedding_model(config.embedding_model)

    persist = config.chroma_path
    if rebuild:
        import shutil
        shutil.rmtree(persist, ignore_errors=True)

    vectorstore = Chroma(
        embedding_function=embeddings,
        persist_directory=persist,
        collection_name="runbooks",
    )

    already_indexed = _get_indexed_files(vectorstore) if not rebuild else set()

    total_chunks = 0
    files_indexed = 0
    skipped = 0
    for file in files:
        if str(file) in already_indexed:
            skipped += 1
            continue
        chunks = _split_file(file, config.chunk_size, config.chunk_overlap, config.chunking_strategy)
        if not chunks:
            continue
        texts = [c.text for c in chunks]
        metadatas = [
            {"source_file": c.source_file, "start_line": c.start_line, "end_line": c.end_line}
            for c in chunks
        ]
        vectorstore.add_texts(texts=texts, metadatas=metadatas)
        total_chunks += len(chunks)
        files_indexed += 1

    return {"files_indexed": files_indexed, "chunks_created": total_chunks, "files_skipped": skipped}
