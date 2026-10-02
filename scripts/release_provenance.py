#!/usr/bin/env python3
"""Emit SLSA-style provenance for a HivePlane release (M59-06)."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

_SUBJECTS = ("Dockerfile.api", "Dockerfile.ui", "deploy/helm/hiveplane/Chart.yaml")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()


def main() -> None:
    """Write a provenance attestation describing the tagged release inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--out", default="provenance.json")
    args = parser.parse_args()

    root = Path.cwd()
    subjects = [
        {"name": name, "digest": {"sha256": _sha256(root / name)}}
        for name in _SUBJECTS
        if (root / name).is_file()
    ]
    provenance = {
        "_type": "https://in-toto.io/Statement/v1",
        "predicateType": "https://slsa.dev/provenance/v1",
        "subject": subjects,
        "predicate": {
            "buildDefinition": {
                "buildType": "https://github.com/deghosal-2026/hiveplane/release@v1",
                "externalParameters": {"tag": args.tag},
            },
            "runDetails": {
                "builder": {"id": "github-actions"},
                "metadata": {
                    "invocationId": args.tag,
                    "startedOn": datetime.now(UTC).isoformat(),
                    "sourceCommit": _git_commit(),
                },
            },
        },
    }
    Path(args.out).write_text(json.dumps(provenance, indent=2) + "\n")
    print(f"wrote {args.out} ({len(subjects)} subjects)")


if __name__ == "__main__":
    main()
