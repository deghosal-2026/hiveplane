# Docker run artifacts (v0.2.0, checked in as evidence)

`scripts/docker-test-v02.sh` writes **all** of its logs and artifacts into this one
directory (overwritten each run). These artifacts are **evidence for the v0.2.0 release
gate and are committed to the repository** — do not gitignore them.

```
field_test/v0.2.0/docker/
├── run.log              # human-readable runner transcript
├── preflight.log        # local-LLM (Qwen3-4B-Instruct-2507-4bit) reachability probe
├── build.log            # image build / compose build output
├── compose-up.log       # docker compose up output
├── wait-ready.log       # /readyz polling
├── compose-ps.txt       # `docker compose ps` snapshot
├── compose.log          # full service logs (captured on teardown)
├── seed-tools.log       # tool-registry seeding
├── seed-tenants.log     # multi-tenant seed
├── playwright-install.log
├── pytest.log           # full pytest output for tests/docker -m docker
├── junit.xml            # structured per-test results
├── environment.json     # run metadata (git sha, docker version, model, ...)
├── load/                # load-test evidence (throughput, latency, resources)
└── report.md            # per-run detailed report (also copied to docs)
```

The consolidated report is written to
`docs/field-test/v0.2.0/DOCKER_TEST_REPORT.md` after every run, and the load-test numbers to
`docs/field-test/v0.2.0/LOAD_TEST_REPORT.md`.

A run is valid evidence only if it recorded **zero skips** — the suite requires real local
inference and fails (never skips) when the local LLM is unavailable (see the
[docker test plan](../../../docs/field-test/v0.2.0/docker-test-plan.md)).

## Layers

L0 image build · L1 stack health · L2 API v2 contract · L3 UI v2 (Playwright) ·
L4 control loop · L5 defense/governance · L6 autonomy · L7 fleet scale · L8 secrets/RBAC/tenancy ·
L9 cost/reporting/portability · L10 Helm/k3d · L11 load test.
