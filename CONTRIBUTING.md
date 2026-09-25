# Contributing to HivePlane

Thanks for your interest. HivePlane is an open-source control plane for operating AI
agent fleets, and contributions of all kinds are welcome — bug reports, docs, workloads,
adapters, and code.

## Getting started

```bash
git clone https://github.com/deghosal-2026/hiveplane.git
cd hiveplane
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Postgres-backed tests run only when a database is reachable; without one they skip. To
run the full suite as CI does, start Postgres (see `docker compose up -d postgres`) and
set the `HIVEPLANE_DATABASE__*` variables (see `.env.example`).

## Quality gates

Every change must pass:

```bash
make test    # pytest (deterministic suite)
make cov     # pytest with coverage report (>= 92%)
make lint    # ruff
make type    # mypy (strict)
make check   # lint + type + cov
```

CI (`.github/workflows/ci.yml`) runs the same gates on Python 3.12 and 3.13 against a
Postgres service, plus the operator UI browser tests and a Docker image build.

## Making a change

1. Open an issue describing the problem or feature (or comment on an existing one).
2. Branch from `main`.
3. Keep changes focused; add tests for behavior changes.
4. Run the quality gates locally.
5. Open a pull request with a clear description and link the issue.

Commit messages use a conventional prefix (`feat`, `fix`, `docs`, `test`, `ci`,
`security`, `chore`) with an optional scope, e.g. `fix(registry): reject empty tool ids`.

## Adding a workload

See [`docs/workloads/CONTRIBUTING.md`](docs/workloads/CONTRIBUTING.md) for the workload
manifest format, corpus requirements, and validation steps.

## Security

Do **not** open a public issue for a vulnerability — follow the process in
[SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).
