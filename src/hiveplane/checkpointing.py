"""Durable LangGraph checkpointing for recovered runs (M23, #122).

LangGraph's default ``InMemorySaver`` loses every checkpoint when the process
exits, so a paused graph cannot resume after a control-plane restart (or a
container recreate). :class:`JsonFileCheckpointSaver` keeps LangGraph's
in-memory checkpoint logic but flushes it to a JSON file on every write, so the
same thread id resumes from its last node in a new process.

``default_checkpointer`` selects the saver from configuration: a
``JsonFileCheckpointSaver`` when ``execution.checkpoint_path`` is set (the
Docker profiles point it at a mounted volume), otherwise an ``InMemorySaver``
for tests and ephemeral local runs.
"""

from __future__ import annotations

import base64
import json
import threading
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
)
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.base import SerializerProtocol

from hiveplane.config import get_settings

_EncodedBlob = list[str]


def _thread_id(config: RunnableConfig) -> str:
    """Return the thread id from a LangGraph config, defaulting to ``default``."""
    configurable = config.get("configurable") or {}
    return str(configurable.get("thread_id", "default"))


class JsonFileCheckpointSaver(InMemorySaver):
    """An in-memory checkpoint saver that mirrors each thread to its own file.

    ``path`` is a directory; every thread (run) persists to ``<thread_id>.json``
    so one run's supersteps never rewrite another run's state, and a terminal
    run's file is deleted by :meth:`delete_thread` (#507).
    """

    def __init__(
        self, path: str | Path, *, serde: SerializerProtocol | None = None
    ) -> None:
        super().__init__(serde=serde)
        self._path = Path(path)
        self._lock = threading.RLock()
        self._path.mkdir(parents=True, exist_ok=True)
        self._load()

    @property
    def path(self) -> Path:
        """Return the directory this saver persists to."""
        return self._path

    def _thread_path(self, thread_id: str) -> Path:
        safe = quote(thread_id, safe="")
        return self._path / f"{safe}.json"

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Store a checkpoint and flush its thread to disk."""
        with self._lock:
            result = super().put(config, checkpoint, metadata, new_versions)
            self._save_thread(_thread_id(config))
            return result

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Store intermediate writes and flush its thread to disk."""
        with self._lock:
            super().put_writes(config, writes, task_id, task_path)
            self._save_thread(_thread_id(config))

    def delete_thread(self, thread_id: str) -> None:
        """Delete a thread's checkpoints and remove its per-thread file."""
        with self._lock:
            super().delete_thread(thread_id)
            self._thread_path(thread_id).unlink(missing_ok=True)

    def _thread_data(self, thread_id: str) -> dict[str, Any]:
        storage = _storage_to_json(self.storage)
        return {
            "storage": {thread_id: storage[thread_id]} if thread_id in storage else {},
            "writes": [
                entry
                for entry in _writes_to_json(self.writes)
                if entry["thread"] == thread_id
            ],
            "blobs": [
                entry
                for entry in _blobs_to_json(self.blobs)
                if entry["thread"] == thread_id
            ],
        }

    def _save_thread(self, thread_id: str) -> None:
        data = self._thread_data(thread_id)
        if not (data["storage"] or data["writes"] or data["blobs"]):
            self._thread_path(thread_id).unlink(missing_ok=True)
            return
        self._path.mkdir(parents=True, exist_ok=True)
        target = self._thread_path(thread_id)
        temp = target.with_name(f"{target.name}.tmp")
        temp.write_text(json.dumps(data), encoding="utf-8")
        temp.replace(target)

    def _load(self) -> None:
        if not self._path.is_dir():
            return
        for path in sorted(self._path.glob("*.json")):
            self._merge(json.loads(path.read_text(encoding="utf-8")))

    def _merge(self, data: dict[str, Any]) -> None:
        storage: defaultdict[str, dict[str, dict[str, tuple[Any, Any, str | None]]]] = (
            defaultdict(lambda: defaultdict(dict))
        )
        for thread_id, namespaces in data.get("storage", {}).items():
            for checkpoint_ns, checkpoints in namespaces.items():
                for checkpoint_id, saved in checkpoints.items():
                    storage[thread_id][checkpoint_ns][checkpoint_id] = (
                        _decode_blob(saved[0]),
                        _decode_blob(saved[1]),
                        saved[2],
                    )
            self.storage[thread_id] = storage[thread_id]

        writes: defaultdict[tuple[str, str, str], dict[tuple[str, int], tuple[Any, ...]]] = (
            defaultdict(dict)
        )
        for entry in data.get("writes", []):
            key = (entry["thread"], entry["ns"], entry["checkpoint"])
            for saved in entry["entries"]:
                writes[key][(saved[0], saved[1])] = (
                    saved[2],
                    saved[3],
                    _decode_blob(saved[4]),
                    saved[5],
                )
            self.writes[key] = writes[key]

        for entry in data.get("blobs", []):
            blob_key = (
                entry["thread"],
                entry["ns"],
                entry["channel"],
                entry["version"],
            )
            self.blobs[blob_key] = _decode_blob(entry["blob"])


def default_checkpointer() -> BaseCheckpointSaver[str]:
    """Build the configured checkpoint saver.

    Returns a durable file-backed saver when ``execution.checkpoint_path`` is
    set, otherwise the in-memory saver (tests, ephemeral local runs).
    """
    path = get_settings().execution.checkpoint_path
    if path:
        return JsonFileCheckpointSaver(path)
    return InMemorySaver()


def _encode_blob(blob: tuple[str, bytes]) -> _EncodedBlob:
    return [blob[0], base64.b64encode(blob[1]).decode("ascii")]


def _decode_blob(encoded: _EncodedBlob) -> tuple[str, bytes]:
    return (encoded[0], base64.b64decode(encoded[1]))


def _storage_to_json(storage: Any) -> dict[str, Any]:
    return {
        thread_id: {
            checkpoint_ns: {
                checkpoint_id: [
                    _encode_blob(checkpoint_blob),
                    _encode_blob(metadata_blob),
                    parent,
                ]
                for checkpoint_id, (checkpoint_blob, metadata_blob, parent) in checkpoints.items()
            }
            for checkpoint_ns, checkpoints in namespaces.items()
        }
        for thread_id, namespaces in storage.items()
    }


def _writes_to_json(writes: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for (thread_id, checkpoint_ns, checkpoint_id), saved in writes.items():
        entries.append(
            {
                "thread": thread_id,
                "ns": checkpoint_ns,
                "checkpoint": checkpoint_id,
                "entries": [
                    [task_id, idx, task_id, channel, _encode_blob(blob), task_path]
                    for (task_id, idx), (_, channel, blob, task_path) in saved.items()
                ],
            }
        )
    return entries


def _blobs_to_json(
    blobs: dict[tuple[str, str, str, Any], tuple[str, bytes]],
) -> list[dict[str, Any]]:
    return [
        {
            "thread": thread_id,
            "ns": checkpoint_ns,
            "channel": channel,
            "version": version,
            "blob": _encode_blob(blob),
        }
        for (thread_id, checkpoint_ns, channel, version), blob in blobs.items()
    ]
