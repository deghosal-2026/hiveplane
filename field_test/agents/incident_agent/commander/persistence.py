"""Session persistence — save, load, and list incident sessions.

Each session is stored as a JSON file in the session directory.
The ``SessionManager`` also provides a LangGraph-compatible checkpointer
for resumable multi-turn incident workflows.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages incident session persistence to disk.

    Sessions are stored as individual JSON files in the configured
    session directory.  Each file is named ``<thread_id>.json``.

    Args:
        session_dir: Directory path for session files.  Created
            automatically if it does not exist.

    """

    def __init__(self, session_dir: str | Path = "~/.incident-commander/sessions") -> None:
        """Initialize the session manager, creating the directory if needed."""
        self._session_dir = Path(session_dir).expanduser()
        self._session_dir.mkdir(parents=True, exist_ok=True)

    # ── Session CRUD ──────────────────────────────────────────────────

    def _path(self, thread_id: str) -> Path:
        """Return the file path for a given session thread ID."""
        return self._session_dir / f"{thread_id}.json"

    def save_session(self, thread_id: str, state: dict[str, Any]) -> None:
        """Persist a session state dict to disk as JSON.

        Args:
            thread_id: Unique session identifier.
            state: Serialisable dict of the full session/state data.

        """
        path = self._path(thread_id)
        try:
            with open(path, "w") as f:
                # default=str handles non-serializable types (datetimes, enums)
                # by falling back to their string representation instead of failing.
                json.dump(state, f, indent=2, default=str)
        except OSError as exc:
            logger.error("Failed to save session %s: %s", thread_id, exc)
            raise

    def load_session(self, thread_id: str) -> dict[str, Any]:
        """Load a previously saved session from disk.

        Args:
            thread_id: Unique session identifier.

        Returns:
            The deserialised session state dict.

        Raises:
            KeyError: If no session with the given ID exists.

        """
        path = self._path(thread_id)
        if not path.exists():
            raise KeyError(f"Session not found: {thread_id}")
        try:
            with open(path) as f:
                data: dict[str, Any] = json.load(f)
            return data
        except json.JSONDecodeError as exc:
            # Corrupt JSON is raised as ValueError (not JSONDecodeError)
            # to keep the public API using built-in exception types.
            raise ValueError(
                f"Corrupt session file for {thread_id}: {exc}"
            ) from exc
        except OSError as exc:
            logger.error("Failed to load session %s: %s", thread_id, exc)
            raise

    def delete_session(self, thread_id: str) -> None:
        """Delete a saved session.

        Args:
            thread_id: Unique session identifier.

        Raises:
            KeyError: If no session with the given ID exists.

        """
        path = self._path(thread_id)
        if not path.exists():
            raise KeyError(f"Session not found: {thread_id}")
        path.unlink()

    def list_sessions(self) -> list[str]:
        """Return all stored session thread IDs.

        Returns:
            A sorted list of thread IDs (without the ``.json`` suffix).

        """
        ids: list[str] = []
        # Scan session directory for .json files, excluding dotfiles
        for child in sorted(self._session_dir.iterdir()):
            if child.suffix == ".json" and not child.name.startswith("."):
                ids.append(child.stem)  # filename without extension
        return ids

    def export_session(self, thread_id: str) -> dict[str, Any]:
        """Alias for ``load_session`` — exports session as a dict.

        Args:
            thread_id: Unique session identifier.

        Returns:
            The full session state as a JSON-compatible dict.

        """
        return self.load_session(thread_id)

    def get_checkpointer(self, thread_id: str) -> _SessionCheckpointer:
        """Return a simple checkpointer for the given session.

        The returned checkpointer wraps this session manager so that
        LangGraph nodes can save/load checkpoint data without importing
        persistence directly.

        Args:
            thread_id: Unique session identifier.

        """
        return _SessionCheckpointer(self, thread_id)


class _SessionCheckpointer:
    """Lightweight checkpointer backed by a ``SessionManager``.

    Provides ``get()`` and ``set()`` methods for checkpointing partial
    graph state during multi-node execution.
    """

    def __init__(self, manager: SessionManager, thread_id: str) -> None:
        """Initialize the checkpointer with a session manager and thread ID."""
        self._manager = manager
        self._thread_id = thread_id
        self._cache: dict[str, Any] = {}

    def get(self) -> dict[str, Any]:
        """Return the current checkpoint state (from disk or cache)."""
        if self._cache:
            return self._cache
        try:
            return self._manager.load_session(self._thread_id)
        except KeyError:
            return {}

    def set(self, state: dict[str, Any]) -> None:
        """Persist a checkpoint to disk."""
        self._cache = state
        self._manager.save_session(self._thread_id, state)
