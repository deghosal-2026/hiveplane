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


class JsonFileCheckpointSaver(InMemorySaver):
    """An in-memory checkpoint saver that mirrors every write to a JSON file."""

    def __init__(
        self, path: str | Path, *, serde: SerializerProtocol | None = None
    ) -> None:
        super().__init__(serde=serde)
        self._path = Path(path)
        self._lock = threading.RLock()
        self._load()

    @property
    def path(self) -> Path:
        """Return the file this saver persists to."""
        return self._path

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Store a checkpoint and flush the saver to disk."""
        with self._lock:
            result = super().put(config, checkpoint, metadata, new_versions)
            self._save()
            return result

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Store intermediate writes and flush the saver to disk."""
        with self._lock:
            super().put_writes(config, writes, task_id, task_path)
            self._save()

    def delete_thread(self, thread_id: str) -> None:
        """Delete a thread's checkpoints and flush the saver to disk."""
        with self._lock:
            super().delete_thread(thread_id)
            self._save()

    def _save(self) -> None:
        data = {
            "storage": _storage_to_json(self.storage),
            "writes": _writes_to_json(self.writes),
            "blobs": _blobs_to_json(self.blobs),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_name(f"{self._path.name}.tmp")
        temp.write_text(json.dumps(data), encoding="utf-8")
        temp.replace(self._path)

    def _load(self) -> None:
        if not self._path.is_file():
            return
        data = json.loads(self._path.read_text(encoding="utf-8"))
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
        self.storage = storage

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
        self.writes = writes

        blobs: dict[tuple[str, str, str, Any], tuple[str, bytes]] = {}
        for entry in data.get("blobs", []):
            blob_key = (
                entry["thread"],
                entry["ns"],
                entry["channel"],
                entry["version"],
            )
            blobs[blob_key] = _decode_blob(entry["blob"])
        self.blobs = blobs


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
