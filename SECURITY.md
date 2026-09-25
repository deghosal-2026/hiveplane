# Security Policy

HivePlane is an open-source control plane for operating AI agent fleets. It decides
which agents may call which tools, how much they may spend, when a human must
approve, and whether an agent is certified to touch production. That concentration
of authority makes it a high-value target — security is a first-class concern.

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x (alpha) | ✅ |

Pre-1.0 releases are alpha and receive security fixes on a best-effort basis.

## Reporting a vulnerability

Please **do not** open a public issue for a security vulnerability.

Report privately via GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository (Security → Report a vulnerability), or open a
[security advisory](https://github.com/deghosal-2026/hiveplane/security/advisories/new).

Include, where possible:

- a description of the issue and its impact,
- steps to reproduce or a proof of concept,
- affected version/commit,
- any suggested remediation.

We aim to acknowledge reports within 3 business days and to provide a remediation
timeline after triage. Please give us a reasonable window to ship a fix before public
disclosure; we will credit reporters who wish to be named.

## Threat model

The full threat model lives in
[docs/prd/06-security-baseline.md](docs/prd/06-security-baseline.md). Summary of the
controls HivePlane provides:

- **Deny-by-default tool policy** evaluated at the control-plane boundary.
- **Budget enforcement** before expensive work, per run and per day.
- **Tamper-evident, append-only audit log**; every operator action is attributed.
- **Certification as a security control** — production admission requires a valid,
  unexpired certification; an uncertified agent is an untrusted agent.
- **Signed attestations** verified on every read, using a persistent signing keypair.
- **Model-identity binding** — the runtime model is reported from actual inference and
  checked against the attestation; a mismatch blocks the run (model-swap defense).
- **Sandbox isolation** with resource caps and restricted network egress; no shared
  filesystem with the control plane.
- **Tool-output shaping and injection scanning** before outputs reach agent context.
- **Secret redaction** before persistence; secrets never appear in logs, traces, or
  audit events.

## Handling of secrets

- Never commit credentials, API keys, tokens, `.env` files, or key material.
- `.gitignore` and `.dockerignore` exclude `.env*`, `*.pem`, `*.key`, `*.sqlite3`, and
  local state (`data/`, `.hiveplane/`).
- The attestation signing key lives on a volume at runtime, not in git.
- Publishing uses GitHub OIDC (Trusted Publishing) or a repository secret — never a
  committed token.

## Release security evidence

The v0.1.0 pre-release secret/dependency audit is recorded in
[docs/release/v0.1.0/security-audit.md](docs/release/v0.1.0/security-audit.md)
(trufflehog full-history scan, working-tree audit, and `pip-audit`).
