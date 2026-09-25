# v0.1.0 — Security Audit (#145)

**Date:** 2026-09-25
**Commit:** `ed2872a`
**Scope:** full git history + working tree, dependency audit, ignore-file review.
**Tools:** trufflehog 3.97.4 · pip-audit 2.10.1 · Python 3.12 target.

This is the M24.1 release-gate evidence. The release is blocked unless every item
below is clean or has a documented disposition.

## Results

| Check | Command | Result |
|-------|---------|--------|
| Secret scan (history, verified) | `trufflehog git file://. --only-verified` | **0 findings** |
| Secret scan (history, all) | `trufflehog git file://.` | **0 findings** |
| High-signal pattern grep (tracked, non-fixture) | `git grep -E 'AKIA…\|ghp_…\|sk-…\|BEGIN … PRIVATE KEY\|xox[baprs]-…'` | **0 matches** |
| Secret file paths ever committed | `git log --all --name-only \| grep -E '\.env$\|\.pem$\|\.key$\|id_rsa'` | **none** |
| Dependency audit (declared deps) | `pip-audit .` | **52 deps, 0 vulnerabilities** |
| `.gitignore` / `.dockerignore` | manual review | reviewed; hardened (below) |

## Secret scan detail

`trufflehog git file://.` scanned all 1,353 tracked files across full history. No
verified or unverified credentials were found. A targeted grep for high-signal
patterns (`AKIA…`, `ghp_…`, `sk-…`, PEM private-key headers, Slack tokens) across
tracked files (excluding the public field-test corpora, which intentionally contain
example runbooks) returned no matches.

No `.env`, `*.pem`, `*.key`, `id_rsa`, or keystore file has ever been committed.

## Working-tree / ignore-file audit

The tracked `.env.ci`, `.env.cloud`, `.env.example`, and `.env.local` are **profiles
without secrets**: the cloud profile leaves `HIVEPLANE_MODEL__API_KEY=` blank and
instructs operators to supply a real key from an untracked file. They are safe to
publish.

Changes made during this pass:

- **Third-party agent code untracked.** `field_test/agents/proven/exectrace/agent-github`
  (a copy of `coleam00/ottomator-agents`) and `…/agent-weather` (a public PydanticAI
  weather-agent example) were committed in error. Both were removed from git tracking
  (`git rm --cached`), added to `.gitignore`, and documented in the vendored-repos table
  of `docs/field-test/v0.1.0/field-test-plan.md`. The files remain on disk for local
  runs; they must be re-cloned on demand. All other vendored upstream trees
  (`tooltrust/vendor/`, `evalforge/agents/`, `exectrace/agent-{crew,chatbot,react,mcp}/`)
  were already untracked. **Field-test results and Docker evidence were not affected.**
- **`.dockerignore` hardened** to also exclude `.env*`, `*.pem`, `*.key`, `*.sqlite3`,
  `data/`, and `.hiveplane/`. (The `Dockerfile` already copies only explicit paths, so
  no secret could reach the image; this is defense-in-depth.)

## Dependency audit detail

`pip-audit .` resolves the project's declared dependencies from `pyproject.toml`:
**52 dependencies audited, 0 known vulnerabilities.** Auditing the full development
virtualenv surfaces advisories in unrelated third-party packages pulled in by
field-test agents (e.g. `pillow`, `pypdf`, `torch`); these are not HivePlane
dependencies and do not ship in the release.

## Disposition

- [x] Trufflehog history scan clean
- [x] No secrets in the working tree; `.gitignore` verified
- [x] `pip-audit` clean on production dependencies (no high/critical)
- [x] Third-party public-repo agent code untracked
- [x] Report committed under `docs/release/v0.1.0/`

**No unmitigated findings.** Release gate M24.1: **PASS**.

## See also

- [SECURITY.md](../../../SECURITY.md) — threat model and disclosure policy
- [Release notes](release-notes.md)
- [WBS Part 13](../../wbs/v0.1.0/wbs-v0.1.0-part13-release-readiness.md)
