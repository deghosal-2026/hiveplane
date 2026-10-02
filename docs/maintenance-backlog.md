# Maintenance-Mode Backlog

v0.2.0 is the final big release. The project now runs in **maintenance mode**: no further
feature releases, only documentation, community, and security work. This is the backlog the
maintainer works from. Items are tracked as GitHub issues; this file is the index.

## Docs

- [ ] Keep the [User Guide](USER_GUIDE.md), [Architecture Tour](architecture-tour.md),
      [Tutorials](tutorials/), and [Operator Runbook](runbooks/operator-runbook.md) current
      with any patch releases.
- [ ] Fill in remaining design docs referenced but not yet written (grep `docs/design/` for
      stubs).
- [ ] Translate the getting-started tutorial (community contribution welcome).
- [ ] Publish the six-part article series ([`docs/articles/`](articles/)).

## Community

- [ ] Seed `good first issue` / `help wanted` labels from the backlog.
- [ ] Add a `CONTRIBUTORS.md` and a code-of-conduct reference if the community grows.
- [ ] Stand up a discussion board for operator questions.
- [ ] Collect adoption writeups and link them from the README.

## Security

- [ ] Watch `pip-audit` and Dependabot; ship patch releases for high/critical advisories only.
- [ ] Rotate the release signing keys per the key-rotation runbook on any compromise.
- [ ] Re-run the `trufflehog` + `pip-audit` audit for every patch release
      (template: [v0.2.0 security audit](release/v0.2.0/security-audit.md)).
- [ ] Keep [SECURITY.md](../SECURITY.md) disclosure contacts current.

## Known deferred items (documented)

- **Homebrew formula** — deferred by decision for v0.2.0; the formula scaffold ships under
  `deploy/homebrew/` for a future tap.
- **Federation** — behind `HIVEPLANE_FEDERATION__ENABLED` (default off); no further work
  planned in maintenance mode.
- **Docker/GHCR image publishing** — `release.yml` builds/signs images on `v*` tags; verify on
  the next patch tag.
