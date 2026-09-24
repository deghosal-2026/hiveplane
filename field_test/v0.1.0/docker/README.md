# Docker run artifacts (checked in as evidence)

Everything a `scripts/docker-test.sh` run produces lands here, under a
timestamped directory. These artifacts are **evidence for the v0.1.0 release
gate and are committed to the repository** — do not gitignore them.

```
field_test/v0.1.0/docker/
├── latest -> 20260923T174500Z/      # symlink to the most recent run
├── <run-id>/
│   ├── run.log            # human-readable runner transcript
│   ├── preflight.log      # local-LLM reachability probe
│   ├── build.log          # image build / compose build output
│   ├── compose-up.log     # docker compose up output
│   ├── wait-ready.log     # /readyz polling
│   ├── compose-ps.txt     # `docker compose ps` snapshot
│   ├── compose.log        # full service logs (captured on teardown)
│   ├── pytest.log         # full pytest output for tests/docker -m docker
│   ├── junit.xml          # structured per-test results
│   ├── environment.json   # run metadata (git sha, docker version, model, ...)
│   └── report.md          # per-run detailed report (also copied to docs)
```

The consolidated report is written to
`docs/field-test/v0.1.0/DOCKER_TEST_REPORT.md` after every run.

A run is valid evidence only if it recorded **zero skips** — the suite requires
real local inference and fails (never skips) when the local LLM is unavailable.