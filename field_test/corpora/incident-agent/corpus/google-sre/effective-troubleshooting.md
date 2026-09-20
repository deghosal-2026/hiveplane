# Effective Troubleshooting

*Google SRE Book - Chapter 12*

## The Hypothetico-Deductive Method

Troubleshooting follows the scientific method — the hypothetico-deductive approach:

1. Form a hypothesis about the cause of the problem
2. Design a test to validate or invalidate the hypothesis
3. Run the test
4. Repeat until root cause is found

This structured approach prevents jumping to conclusions and ensures progress is measurable.

## Problem Report

A good problem report captures three things:

- **Expected behavior**: What should happen under normal conditions.
- **Actual behavior**: What is actually happening (the symptom).
- **Reproduction steps**: How to reliably trigger the issue.

Without a clear problem report, troubleshooting becomes unfocused and inefficient.

## Triage

When an incident is first reported, **stop the bleeding first**. Do not start root-cause analysis immediately.

- Restore service as quickly as possible (rollback, failover, scale up).
- Document what was done so the state of the system is not lost.
- Only after the immediate issue is contained should you move to diagnosis.

## Examine

Gather data about the system to inform your hypotheses.

- **Metrics**: Time-series data showing changes in traffic, latency, error rates, resource usage.
- **Logging**: Structured logs with severity levels and sampling for high-volume events.
- **Current state exposure**: Endpoints that reveal live system state (e.g., `/healthz`, `/debug/vars`, `/statusz`).

Build these capabilities into your systems before you need them.

## Diagnose

Techniques for narrowing down the root cause:

- **Simplify and reduce**: Remove variables. Disable features one at a time.
- **Divide and conquer**: Isolate the problem by splitting the system. Test each component independently.
- **Ask what/where/why**: What changed? Where in the stack? Why does this component behave differently?
- **"What touched it last"**: The last change to a system is often the cause of an incident.

## Test and Treat

When testing a hypothesis:

- **Mutual exclusion**: Ensure tests are independent so you know which factor caused the result.
- **Test the obvious first**: Check the simplest explanations before pursuing exotic theories.
- **Note side effects**: Your test may affect system behavior in unintended ways.
- **Take clear notes**: Document what you tested, what you observed, and what you concluded. This is invaluable for postmortems.

## Negative Results Are Valuable

A test that disproves a hypothesis is just as useful as one that confirms it. Recording negative results prevents others from retreading the same dead ends. Share them in postmortems and runbooks.

## Common Pitfalls

- **Irrelevant symptoms**: Not every anomaly is related to the incident. Distinguish signal from noise.
- **Misunderstanding system changes**: A change may have subtle or unexpected effects. Read diffs carefully.
- **Latching on to past causes**: The last incident's root cause is not necessarily this incident's root cause.
- **Spurious correlations**: Correlation does not imply causation. Validate with controlled tests.

## Making Troubleshooting Easier

Invest in system design to reduce troubleshooting difficulty:

- **Build observability**: Expose metrics, logs, and traces as first-class features.
- **Well-defined interfaces**: Clear contracts between components make it easier to isolate faults.
- **Consistent request IDs**: Propagate trace IDs across services to follow a request's full path.
- **Log all changes**: Every configuration change, deployment, and operational action should be auditable.
