# S10 — operator-surface (PASS)

**Scenario:** the operator criteria in one sweep — inspect and stop a live run from one
surface (A13), a complete audit trail (A15), fast intervention (A16), `hiveplane init`
scaffolding (A18), and the certification dashboard + spend view (A19/A20).

**Run:** direct runner invocation against the live stack (2026-09-25).

## Result

**PASS:**
- **inspect + stop: 0.04 s** — a paused `eval-judge` run (ambiguous solution →
  human-review interrupt) was inspected (`GET /runs/{id}`, `/events`, `/story`) and
  stopped (`POST /runs/{id}/stop` → `cancelled`) in 0.04 s — well under the 2-minute A16
  target.
- **audit trail complete** — the stopped run's event log contains `admission`,
  `state_change`, and `operator_action` entries (A15).
- **`hiveplane init` scaffolded a project in 0.24 s** (A18) — manifest, corpus, README
  created via `python -m hiveplane.cli init`.
- **dashboards render** — `/certifications` and `/spend` on the UI (:3001) both returned
  200 (A19/A20).

## Notes

- The paused-run stop exercises the cooperative cancel path across the interrupt:
  `PAUSED → CANCELLED` is a legal transition and the operator event is recorded.
- The stop endpoint is the same surface the API/CLI/UI expose — one surface, one action.

## Evidence

`operator_surface.json` — inspected run, events, story, stop result, timings;
`init.json` — scaffold timing + output; `views.json` — UI dashboard/spend reachability +
API spend snapshot.