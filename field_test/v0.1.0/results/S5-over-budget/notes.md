# S5 — over-budget (PASS)

**Scenario:** a run that accumulates priced usage beyond its per-run budget must be
**failed by the control plane before expensive work continues**.

**Run:** 20260925T011411Z.

## Result

**PASS — the `budget-probe` run failed with `run budget exceeded`.** The probe makes
exactly one governed model call; its manifest sets `per_run_usd: 0.000001`, so the priced
usage exceeds the run ceiling and the budget service fails the run at the usage report.

## How the local $0 model got priced

Budget enforcement needs non-zero cost, and the built-in cost table prices `omlx/*` at
zero. Two additions made the local model priceable without a cloud provider:

- **Config**: new `HIVEPLANE_BUDGET__PRICES` /
  `HIVEPLANE_BUDGET__ZERO_COST_PREFIXES` settings (with empty-string fallbacks to the
  defaults); the field-test profile prices
  `omlx/qwen3-4b-instruct-2507/4bit` at 150/600 USD per 1M tokens and drops the prefix
  exemption. Unit-tested in `tests/test_config_budget.py`.
- **Fixture**: the `budget-probe` workload (`field_test/shims/budget_agent.py`) — one
  `ctx.complete()` call so real token usage flows through the budget service.

## Notes

- The block fires at the **usage report**, not at admission: admission checks day/team
  headroom only, and a per-run ceiling can only be judged once cost accrues. This is the
  correct seam — it stops the run the moment the ceiling is crossed.
- Admission-level day/team blocking is covered by the budget unit suite and remains the
  path for repeated over-spend.

## Evidence

`run.json` — the failed run with `failure_reason` containing "budget exceeded";
`spend.json` — the spend snapshot showing the priced attribution.