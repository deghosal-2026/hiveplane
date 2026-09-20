"""Subprocess adapter: invoke an executable agent with JSON on stdin.

The agent contract (spec §"Agent is an executable"): EvalForge launches the
agent command, passes the invocation payload as JSON on stdin, captures
stdout, and requires a clean exit code. Agent stderr is ignored for output
parsing (it is surfaced in error messages when the process fails).

Security: commands run without a shell (``shell=False``) so config-supplied
command strings cannot be chained with shell metacharacters.

Exports:
    SubprocessAdapter: Adapter that invokes an agent as a subprocess.
"""

from __future__ import annotations

import shlex
from typing import Any

from evalforge.adapters.base import Adapter, _inject_fixtures
from evalforge.adapters.subprocess_runner import run_agent_in_subprocess
from evalforge.models.errors import AdapterError


class SubprocessAdapter(Adapter):
    """Invoke an agent as a subprocess; stdin gets the invocation payload JSON.

    The command is specified as a string in config and split via ``shlex.split``
    for safe argument handling. The agent receives the payload on stdin and
    must produce a run envelope on stdout.

    Attributes:
        name: Identifier "subprocess".
    """

    name = "subprocess"

    def _invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> str:
        """Launch the agent subprocess and return its stdout.

        Args:
            payload: The invocation payload dict, serialized as JSON on stdin.
            config: Adapter configuration; requires ``command``, optionally
                ``timeout_seconds``, ``sandbox``, ``env``, ``cwd``,
                ``fixtures``, and ``container_runtime``.

        Returns:
            The raw stdout string from the agent process.

        Raises:
            AdapterError: If ``command`` is missing, the process fails to
                launch, exits with non-zero code, or times out.
        """
        command = config.get("command")
        if not command:
            raise AdapterError("subprocess adapter requires `command` in config")
        args = shlex.split(command)
        _inject_fixtures(payload, config)
        return run_agent_in_subprocess(payload, args, config)
