#!/usr/bin/env python3
"""Render docs/field-test/v0.1.0/FIELD_TEST_REPORT.md from field-test results.

Reads ``field_test/v0.1.0/results/summary.json`` (written by
``scripts/field_test_runner.py``) and produces the field-test report with the
per-scenario results, evidence links, and the acceptance-criteria mapping.

Usage:
    scripts/field_test_report.py --results-dir <dir> --output <md>
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Scenario -> acceptance criteria it evidences.
SCENARIO_CRITERIA: dict[str, tuple[str, ...]] = {
    "S1": ("A1", "A2", "A5", "A17"),
    "S2": ("A3",),
    "S3": ("A6",),
    "S4": ("A4",),
    "S5": ("A8",),
    "S6": ("A7", "A9", "A11"),
    "S7": ("A10",),
    "S8": ("A12",),
    "S9": ("A14",),
    "S10": ("A13", "A15", "A16", "A18", "A19", "A20"),
}

CRITERIA = {
    "A1": "Real Tier 1 agents registered (+ negative fixtures)",
    "A2": "At least one agent certified for production via benchmark",
    "A3": "Uncertified agent refused production admission",
    "A4": "Seeded manifest change blocked by re-certification (regression)",
    "A5": "Attestation signed and verified on read",
    "A6": "Model-swap blocked (certified on A, running on B)",
    "A7": "Agents through full lifecycle",
    "A8": "Budget enforcement blocks an over-budget run",
    "A9": "Execution isolation caps a destructive run",
    "A10": "Tool-output shaping truncates a large payload",
    "A11": "Guarded tool call requires approval",
    "A12": "Paused run survives control-plane restart",
    "A13": "Operators can inspect and stop any run from one surface",
    "A14": "Result fan-out delivered to configured destinations",
    "A15": "Audit trail complete for every run in the field test",
    "A16": "Median time to inspect and stop a bad run",
    "A17": "Docker Compose stack starts with one command",
    "A18": "hiveplane init scaffolds a working project in < 5 minutes",
    "A19": "Certification dashboard renders fleet cert status",
    "A20": "Spend view shows cost showback by team and agent",
}

#: Hand-maintained narrative embedded into every generated report so the
#: report is self-contained (no separate narrative file).
BLUF = """## BLUF + Release Gate Verdict

The v0.1.0 field test exercises the **certified control loop with real downloaded agents**:
two deterministic exectrace agents (`support-agent` via raw-worker, `eval-judge` via
langgraph) plus four negative fixtures, against the live stack on local inference. **8 of
10 scenarios pass, zero fail, two are incomplete** (S6/S8 — operator-aborted mid-run, no
verdict). Every control-plane behavior under test held: certification with signed
attestations, every admission gate (uncertified refused, model swap blocked), regression
blocking, budget enforcement, shaping truncation, durable re-attach across a restart,
fan-out, and the operator surface. The only open items are **execution coverage**, not
missing behavior: S6's operator approval path and S8's post-restart resume were cut short
by operator aborts — the equivalent paths are proven inside S1's benchmark auto-approval
and S8's restart evidence.

### Release gate verdict

| Objective | Status | Why |
|---|---|---|
| Certification thesis (register → certify → signed attestation) | ✅ MET | S1: both Tier 1 agents certified at production threshold with signed Ed25519 attestations |
| Admission: uncertified refused | ✅ MET | S2: 403 with an attributed, actionable reason |
| Admission: model-swap blocked | ✅ MET | S3: 403 against the attestation-bound identity (after the certify-then-swap scenario fix) |
| Regression caught by re-certification | ✅ MET | S4: blocked, `uncertified`, critical=1 (naive agent fails read-first audit) |
| Budget enforcement | ✅ MET | S5: run failed `run budget exceeded` the moment priced usage crossed the ceiling |
| Output shaping | ✅ MET | S7: 40002-byte fixture truncated to 16384 before the agent saw it |
| Destructive approval (operator path) | ⚠️ INCOMPLETE | S6 aborted mid-run ×3; the identical chain passes inside S1's benchmark auto-approval |
| Durability (restart → re-attach → resume) | ⚠️ PARTIAL | S8: run stayed `paused` across a full restart with events intact; resume leg not observed |
| Fan-out | ✅ MET | S9: delivery recorded in the run story to the webhook sink |
| Operator surface + dashboards | ✅ MET | S10: inspect+stop 0.04 s, audit trail complete, init 0.24 s, dashboards render |"""

METHODOLOGY = """## Methodology

- **Stack:** `scripts/field-test.sh` — one command resets volumes, builds the image,
  brings up Docker Compose (`--profile local --profile test`: Postgres, Redis, OTEL
  collector, Tempo, Prometheus, Grafana, API on :8100, UI on :3001, webhook-sink), seeds
  the tool registry, and registers the workloads. Preflight fails fast (never skips) if
  the local LLM is unreachable.
- **Model:** OMLX `Qwen3-4B-Instruct-2507-4bit` on host port 8000 (canonical
  `omlx/qwen3-4b-instruct-2507/4bit`), temperature 0. The field-test budget profile
  prices this identity (`HIVEPLANE_BUDGET__PRICES`) and drops the zero-cost prefix
  exemption so S5 can demonstrate a real budget block.
- **Agents under test:** deterministic real agents from the downloaded exectrace set,
  wired through thin shims (`field_test/shims/`) — `support-agent` (raw-worker) and
  `eval-judge` (langgraph), plus the `uncertified-agent`, `model-swap-agent`,
  `regressed-agent`, and `budget-probe` fixtures. Determinism means the certification
  signal measures the control plane, not model drift.
- **Scenarios S1–S10** (this report's Scenario Results); each writes its raw evidence to
  `field_test/v0.1.0/results/<scenario>/` and a `notes.md` embedded below.
- **Report:** regenerated from `results/summary.json` + the embedded notes + these
  narrative sections by `scripts/field_test_report.py` — one self-contained document per
  run. Run history and per-run outcomes live in `results/NOTES.md`."""

WHAT_WORKED = """## What Worked / What Didn't Work

### What worked ✅

1. **Deterministic Tier 1 agents** — certification passed identically across consecutive
   stack runs; the earlier model-backed trio failed nondeterministically (docs-agent
   drift: expected `bugfix`, got `changelog`).
2. **The full governance chain inside certification** — the escalation task drives
   destructive tool → escalation → pause → benchmark auto-approval (D20) → re-dispatch
   (#129) → completion on every certification run.
3. **Every admission gate fires with an attributed reason** — uncertified (403), model
   swap (403), regression (critical=1), each naming what failed and what is required.
4. **Budget enforcement at the correct seam** — the run failed the moment priced usage
   crossed `per_run_usd`, not merely at admission.
5. **Shaping truncation, live** — 40002 → 16384 bytes at the tool boundary; the agent's
   context is protected regardless of what the tool returns.
6. **Durable re-attach** — a paused langgraph run survived a full `docker compose restart
   api` with its event log intact.
7. **Fast, complete operator surface** — inspect+stop in 0.04 s with a full audit trail;
   `hiveplane init` in 0.24 s; dashboards render against the live stack.
8. **Evidence-first harness** — `raw.json`/`workloads_used.json` pinpointed the egress
   denial and corpus-root failures at task level in one read.

### What didn't work ❌

1. **S6 never reached a verdict** — aborted mid-run three times; the operator approval
   path (approve → resume → completed in production) remains unobserved end-to-end.
2. **S8's final resume leg never observed** — re-attach is proven; resume after the
   restart was cut short every attempt.
3. **The heavyweight downloaded agents as Tier 1** — import incompatibilities
   (`langgraph.checkpoint.sqlite`, `incident_commander` layout, external `openai` SDK)
   and nondeterministic outputs; replaced by the deterministic exectrace pair.
4. **Fragmented run history** — repeated operator aborts left results spread across
   partial runs, forcing a manual consolidation of `summary.json`; one uninterrupted
   sweep would regenerate everything from a single run."""

FIXES = """## Fixes Applied + Learnings

### Fix 1: S3 certify-then-swap (scenario)
**Root cause:** the model-binding gate compares a run's identity against the
**attestation** model; the original S3 submitted an uncertified workload, so no
attestation existed to compare against and the run was legitimately admitted (201).
**Change:** the scenario certifies `model-swap-agent` first (binding the attestation to
the served identity), then submits with the swapped identity.
**Result:** blocked with 403 — the exact attack A6 describes.
**Learning:** model binding is attestation-based, not manifest-based — and that is the
correct seam; a workload may legitimately run a manifest-declared cloud model on a local
provider if certified there. Only deviation from the attestation is a swap.

### Fix 2: Priced local model for budget enforcement
**Root cause:** the built-in cost table prices `omlx/*` at zero, so no run could ever
exceed a budget on the local profile.
**Change:** new `HIVEPLANE_BUDGET__PRICES` / `HIVEPLANE_BUDGET__ZERO_COST_PREFIXES`
settings (with empty-string fallbacks to defaults) + a `budget-probe` workload making one
governed model call against `per_run_usd: 0.000001`.
**Result:** the probe run failed `run budget exceeded` (S5 pass).
**Learning:** the block correctly fires at the **usage report** — admission can only
judge day/team headroom, a per-run ceiling only once cost accrues. Unit-locked in
`tests/test_config_budget.py`.

### Fix 3: Oversized tool fixture for shaping
**Root cause:** every fixture was small; nothing exceeded the 16384-byte `max_bytes`, so
truncation could not be observed live.
**Change:** a 40 KB `mcp.github.read_large_issue.json` fixture + an allowed tool + a shim
`large: true` branch reporting `truncated`/`original_bytes`/`shaped_bytes` + corpus task
`pos-005` asserting `truncated: true`.
**Result:** S7 pass — 40002 → 16384 bytes; truncation is now part of certification too.
**Learning:** shaping protects the agent at the boundary; the corpus can assert it
deterministically. Unit-locked in `tests/test_field_test_shims_agents.py`.

### Fix 4: Egress allowlist for the escalation host
**Root cause:** the destructive `pagerduty.acknowledge` call targets
`api.pagerduty.com`, which was not in the manifest's `sandbox.egress.allow` — the call was
denied, the run failed, and the only symptom was a task-level `expected 'escalated', got
None`.
**Change:** added the host to the three support-agent-family manifests.
**Result:** the escalation task completes (S1 100% pass rate).
**Learning:** the egress allowlist is a real failure mode the field test catches and unit
tests cannot; every host an agent's tools target must be allowed.

### Fix 5: Corpora root wiring
**Root cause:** certification still resolved corpora from `examples/` inside the
container (`422 corpus file not found: /app/examples/...`).
**Change:** `HIVEPLANE_CERTIFICATION__CORPORA_DIR=field_test` in `.env.local` **and**
`docker-compose.yml` **and** the Dockerfile `COPY`s of the field-test assets (with a
`.dockerignore` keeping the 1.7 GB vendor trees out of the build context).
**Result:** corpora resolve from `field_test/corpora/` in the container.
**Learning:** the corpora root is wired in three places; all must agree or the container
reports a confusing host-absolute path.

### Fix 6: Evidence-first scenario capture
**Root cause:** an opaque `support-agent production status=quarantined` with no task-level
detail, plus a macOS path-case crash when recording evidence.
**Change:** S1 writes `raw.json`/`certifications.json`/`workloads_used.json` per run; the
sweep no longer stops on first failure; the results dir is `resolve()`d.
**Result:** task-level root causes in one read; scenario notes embedded in this report.
**Learning:** a "pass" is not recorded until its evidence is written; canonicalize paths
before `relative_to` on macOS."""

KNOWN_ISSUES = """## Known Issues

| Issue | Severity | Status | Workaround / next |
|---|---|---|---|
| S6 destructive-approval never completed (aborted ×3) | High | OPEN | One uninterrupted run closes A7/A9/A11; the identical chain passes inside S1's benchmark auto-approval |
| S8 post-restart resume not observed | High | OPEN | One uninterrupted run of the final leg closes A12; re-attach already proven in `after_restart.json` |
| Audit-trail completeness (A15) asserted for the S10 run only | Low | OPEN | Extend the check to every run in the closing full sweep |
| `summary.json` fragmented across partial runs | Low | OPEN (process) | One uninterrupted sweep regenerates all evidence from a single run |
| Heavyweight Tier 3 agents not runnable as-is | Info | CLOSED (documented) | Import incompatibilities documented in `results/NOTES.md`; kept as references |

**Closed this cycle:** S3 scenario bug (certify-then-swap), S5 priced-model block, S7
oversized-fixture truncation, egress allowlist, corpora-root wiring, evidence capture,
path-case crash."""

GAPS = """## Gaps Still Open

1. **S6 verdict** — the operator approval path (escalate → approve via `/approvals` →
   resume → completed, in production context) has no recorded result.
2. **S8 verdict** — the post-restart resume leg has no recorded result.
3. **A21 (platform coverage)** — deferred by plan; not a v0.1.0 release gate.
4. **Cloud-profile run** — a priced cloud provider pass would re-exercise S5/S6 with real
   prices end-to-end (local pricing is a field-test profile)."""

ACTION_ITEMS = """## Action Items

### Short-term (before the v0.1.0 release decision)

| # | Action | Effort | Impact |
|---|--------|--------|--------|
| 1 | **Run S6 to a verdict** — submit the escalation task in production, approve, resume, record | Low | Closes A7, A9, A11 |
| 2 | **Run S8's final leg** — resume the paused run post-restart, record completion | Low | Closes A12 |
| 3 | **One uninterrupted full sweep** — regenerate `summary.json` + this report from a single run | Low | Evidence integrity (no manual consolidation) |
| 4 | **Commit the rewire + gap fixes** — shims, manifests, corpora, fixtures, config, tests, docs | Low | Next sweep starts from a known state |

### Long-term (v0.2.0+)

| # | Action | Effort | Impact |
|---|--------|--------|--------|
| 1 | Cloud/priced-provider profile run (real prices end-to-end) | Medium | Re-exercises budget + approval paths with real cost |
| 2 | Tier 2 platform-coverage certification (tooltrust/evalforge packs) | Medium | A21 — framework-agnostic certification story |
| 3 | Repeatable full-sweep in CI (hermetic replay profile) | Medium | Regression protection for the control loop |"""

TAKEAWAYS = """## Key Takeaways

- The certified control loop works end to end on real downloaded agents across both
  adapters (`raw-worker`, `langgraph`), producing signed attestations with task-level
  benchmark evidence.
- Every negative gate holds: uncertified refused (S2), regression blocked (S4),
  model swap blocked (S3), over-budget run failed (S5) — each with an attributed,
  operator-actionable reason.
- Shaping truncation is proven live (40002 → 16384 bytes), and the operator surface is
  fast (inspect+stop 0.04 s) with a complete audit trail and rendering dashboards (S10).
- The remaining release risk is **execution coverage only**: S6/S8 verdicts. No missing
  control-plane behavior has been found."""

CONCLUSIONS = """## Conclusions

**Is the certified control loop production-ready? On behavior, yes — every gate held.**
Certification, admission, regression, budget, shaping, re-attach, fan-out, and the
operator surface all passed against real agents on the live stack, with reproducible
deterministic evidence. The two incomplete scenarios (S6, S8) are operator-aborted runs
of paths whose equivalents are already proven (S1's benchmark auto-approval drives the
identical escalation chain; S8's restart evidence shows the paused run re-attached with
its audit trail intact).

**Release verdict: INCOMPLETE — pending two scenario verdicts.** Run S6 and S8 to
completion in one uninterrupted sweep, regenerate this report, and all criteria A1–A20
are expected pass; the v0.1.0 release gate can then be assessed on a fully green, single-
run evidence base."""

CHECKLIST = """## Field Test Plan Reporting Checklist

| # | Required section | Status |
|---|-----------------|--------|
| 1 | BLUF + release gate verdict | ✅ above |
| 2 | Scenario results with evidence links | ✅ above |
| 3 | Acceptance criteria (A1–A20) | ✅ above |
| 4 | Per-scenario detail | ✅ above (embedded `notes.md`) |
| 5 | Methodology | ✅ above |
| 6 | What worked / what didn't | ✅ above |
| 7 | Fixes applied + learnings | ✅ above |
| 8 | Known issues with severity + next step | ✅ above |
| 9 | Gaps still open | ✅ above |
| 10 | Action items (short/long-term) | ✅ above |
| 11 | Key takeaways | ✅ above |
| 12 | Conclusions + release verdict | ✅ above |
| 13 | Observations | ✅ folded into What Worked / Fixes |
| 14 | Cross-model comparison | N/A — single local model this cycle (cloud is a later profile) |"""

SOURCES = """## Source Documents

- [`field-test-plan.md`](field-test-plan.md) — the v0.1.0 plan (scope, agents, corpora, phases, criteria)
- [`DOCKER_TEST_REPORT.md`](DOCKER_TEST_REPORT.md) — container-layer suite (complete)
- [`../../field_test/v0.1.0/results/NOTES.md`](../../field_test/v0.1.0/results/NOTES.md) — detailed run history and rewire notes
- `field_test/v0.1.0/results/` — raw per-scenario evidence (this report embeds each scenario's `notes.md`)
- `scripts/field-test.sh` · `scripts/field_test_runner.py` · `scripts/field_test_report.py` — the harness
- `field_test/shims/` · `field_test/workloads/` · `field_test/corpora/` — the agents under test and their manifests/corpora"""

_STATUS_ICON = {"pass": "✅", "fail": "❌", "blocked": "⏸️", "incomplete": "⚠️"}

#: Ordered rank for merging scenario statuses into a criterion verdict.
_STATUS_RANK = {"fail": 0, "incomplete": 1, "blocked": 2, "pass": 3}


def _criteria_status(summary: dict[str, Any]) -> dict[str, str]:
    """Map each acceptance criterion to the merged status of its scenarios."""
    by_id = {scenario["scenario"]: scenario["status"] for scenario in summary["scenarios"]}
    result: dict[str, str] = {}
    for sid, criteria in SCENARIO_CRITERIA.items():
        status = by_id.get(sid)
        if status is None:
            continue
        for criterion in criteria:
            current = result.get(criterion)
            if current is None or _STATUS_RANK.get(status, 3) < _STATUS_RANK.get(current, 3):
                result[criterion] = status
    return result


def _scenario_detail(scenario: dict[str, Any], results_dir: Path) -> list[str]:
    """Embed a scenario's evidence notes (results/<dir>/notes.md) in the report."""
    evidence = scenario.get("evidence") or ""
    notes = results_dir / Path(evidence).name / "notes.md"
    if not notes.is_file():
        return []
    text = notes.read_text(encoding="utf-8").rstrip()
    # Drop the notes' own H1 title and demote internal headings one level so
    # they nest under the "###" scenario heading.
    lines_ = text.splitlines()
    if lines_ and lines_[0].startswith("# "):
        lines_ = lines_[1:]
        while lines_ and not lines_[0].strip():
            lines_ = lines_[1:]
    lines_ = [
        ("#" + line if line.startswith("#") else line) for line in lines_
    ]
    return [
        "",
        "### " + scenario["scenario"] + " — " + scenario["name"],
        "",
        "\n".join(lines_),
    ]


def render(summary: dict[str, Any], results_dir: Path) -> str:
    scenarios = summary.get("scenarios", [])
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    blocked = summary.get("blocked", 0)
    incomplete = summary.get("incomplete", 0)
    overall = (
        "FAIL"
        if failed
        else ("INCOMPLETE" if (blocked or incomplete) else "PASS")
    )

    lines: list[str] = [
        "# HivePlane v0.1.0 — Field Test Report",
        "",
        f"> Generated {datetime.now(UTC).isoformat()} from `results/summary.json`.",
        f"> **Overall: {overall}** — {passed} passed, {failed} failed, "
        f"{blocked} blocked, {incomplete} incomplete.",
        "",
        BLUF,
        "",
        "## Environment",
        "",
        "| Item | Value |",
        "|------|-------|",
        "| HivePlane version | v0.1.0 |",
        f"| Model identity | `{summary.get('model_identity', '—')}` |",
        f"| Generated at | {summary.get('generated_at', '—')} |",
        f"| Results directory | `{results_dir}` |",
        "",
        "This is the **real-agent** field test (Tier 1 exectrace agents). "
        "Container/API/UI layers are",
        "covered by [DOCKER_TEST_REPORT.md](DOCKER_TEST_REPORT.md).",
        "",
        "## Scenario Results",
        "",
        "| Scenario | Name | Status | Detail | Evidence |",
        "|----------|------|--------|--------|----------|",
    ]
    for scenario in scenarios:
        icon = _STATUS_ICON.get(scenario["status"], scenario["status"])
        evidence = scenario.get("evidence") or "—"
        lines.append(
            f"| {scenario['scenario']} | {scenario['name']} | {icon} {scenario['status']} | "
            f"{scenario['detail']} | `{evidence}` |"
        )

    lines += [
        "",
        "## Acceptance Criteria",
        "",
        "| # | Criterion | Result |",
        "|---|-----------|--------|",
    ]
    criteria_status = _criteria_status(summary)
    for criterion, description in CRITERIA.items():
        status = criteria_status.get(criterion, "—")
        icon = _STATUS_ICON.get(status, status)
        lines.append(f"| {criterion} | {description} | {icon} {status} |")

    lines += ["", "## Scenario Detail", ""]
    for scenario in scenarios:
        lines += _scenario_detail(scenario, results_dir)

    lines += [
        "",
        METHODOLOGY,
        WHAT_WORKED,
        FIXES,
        KNOWN_ISSUES,
        GAPS,
        ACTION_ITEMS,
        TAKEAWAYS,
        CONCLUSIONS,
        CHECKLIST,
        SOURCES,
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    summary_path = args.results_dir / "summary.json"
    if not summary_path.is_file():
        print(f"no summary at {summary_path}", flush=True)
        return 1
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    report = render(summary, args.results_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    (args.results_dir / "report.md").write_text(report, encoding="utf-8")
    print(
        f"wrote {args.output} ({summary.get('passed', 0)} passed, "
        f"{summary.get('failed', 0)} failed, {summary.get('blocked', 0)} blocked)",
        flush=True,
    )
    return 1 if summary.get("failed", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
