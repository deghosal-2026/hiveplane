# v0.2.0 — Security Audit

**Date:** 2026-10-01
**Commit:** `e24d6e85` (feat-v0.2.0; scan covers full git history through HEAD)
**Scope:** full git history + working tree, dependency audit, ignore-file review.
**Tools:** trufflehog 3.97.9 · pip-audit 2.10.1 · Python 3.12 target.

This is the v0.2.0 release-gate security evidence. The release is blocked unless every item
below is clean or has a documented disposition.

## Results

| Check | Command | Result |
|-------|---------|--------|
| Secret scan (history, verified) | `trufflehog git file://. --only-verified` | **0 verified findings** |
| Secret scan (history, all) | `trufflehog git file://.` | 7,949 unverified — all fixture/test data (below) |
| High-signal pattern grep (tracked, non-fixture) | `git grep -E 'AKIA…\|ghp_…\|sk-…\|BEGIN … PRIVATE KEY\|xox[baprs]-…' -- ':!field_test'` | **0 matches** |
| Secret file paths ever committed | `git log --all --name-only \| grep -E '\.env$\|\.pem$\|\.key$\|id_rsa$\|\.p12$\|\.keystore$'` | **none** |
| Dependency audit (declared deps) | `pip-audit .` | **0 known vulnerabilities** |
| `.gitignore` / `.dockerignore` | manual review | reviewed (unchanged from v0.1.0 hardening) |

## Secret scan detail

`trufflehog git file://.` scanned the full history: **137,808 chunks / ~386 MB**,
**0 verified secrets**, 7,949 unverified hits. Every unverified hit is vendored
**fixture/test data**, not project material:

| Location | Hits | Nature |
|----------|------|--------|
| `field_test/v0.2.0/**` | 7,933 | Downloaded real-agent sources and captured LLM/HTTP traces used as benchmark evidence |
| `field_test/corpora/**` | 10 | Vendored public-repo commit histories (e.g. Grafana) used as benchmark corpora |
| `field_test/v0.1.0/**` | 5 | Prior-cycle fixtures |
| `tests/test_reconcile_loader.py` | 1 | A deliberately fake git URL credential (`https://x-access-token:s3cret@…`) exercising credential-reference parsing |

None is a live credential; the verified-secret count is zero. The single `tests/` hit is a
synthetic value asserted by the test, and the `field_test/` hits are public upstream data
(the same class of fixture the v0.1.0 audit reviewed).

A targeted grep for high-signal patterns (`AKIA…`, `ghp_…`, `sk-…`, PEM private-key
headers, Slack tokens) across tracked non-fixture files returned **no matches**. No `.env`,
`*.pem`, `*.key`, `id_rsa`, `.p12`, or keystore file has ever been committed.

## Working-tree / ignore-file audit

The tracked `.env.ci`, `.env.cloud`, `.env.example`, and `.env.local` are **profiles without
secrets**: `.env.cloud` leaves `HIVEPLANE_MODEL__API_KEY=` blank, and the example/database
passwords are local placeholders (`hiveplane`, `admin`). They are safe to publish. This
matches the v0.1.0 disposition; `.gitignore`/`.dockerignore` already exclude `.env*`,
`*.pem`, `*.key`, `*.sqlite3`, `data/`, and `.hiveplane/`.

## Dependency audit detail

`pip-audit .` resolves the project's declared dependencies from `pyproject.toml`:
**0 known vulnerabilities.** Auditing a full development virtualenv can surface advisories
in unrelated third-party packages pulled in by field-test agents; those are not HivePlane
dependencies and do not ship in the release.

## Disposition

- [x] Trufflehog history scan clean (0 verified secrets)
- [x] All unverified findings are fixture/test data with a documented disposition
- [x] High-signal grep clean; no secret file has ever been committed
- [x] `.gitignore` verified; tracked `.env.*` profiles contain no secrets
- [x] `pip-audit` clean on production dependencies
- [x] Report committed under `docs/release/v0.2.0/`

**No unmitigated findings.** v0.2.0 release-gate security pass: **PASS**.

## See also

- [v0.1.0 security audit](../v0.1.0/security-audit.md)
- [SECURITY.md](../../../SECURITY.md) — threat model and disclosure policy
- [Release notes](release-notes.md) · [WBS Part 19](../../wbs/v0.2.0/wbs-v0.2.0-part19-field-test-release.md)
