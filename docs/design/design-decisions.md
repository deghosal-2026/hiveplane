# Design Decisions

Centralized log of significant HivePlane design decisions, referenced as DD-NN.

| DD | Decision | Status |
|----|----------|--------|
| DD-01 | The workload manifest is the stable contract; control-plane concepts must not leak runtime specifics | accepted |
| DD-02 | Runtimes sit behind adapters; HivePlane must not become a framework wrapper | accepted |
| DD-03 | Policy is evaluated at the control-plane boundary, visible in config — not hidden in agent code | accepted |
| DD-04 | Budgets are enforced per run and per day, at the point of task admission and tool call | accepted |
| DD-05 | PostgreSQL is the system of record for desired state, run state, and audit history | accepted |
| DD-06 | Telemetry is OpenTelemetry-native; traces, metrics, and logs share run correlation IDs | accepted |
| DD-07 | Every operator action is auditable and attributable | accepted |
| DD-08 | The system must be useful locally (Docker Compose) before it claims scale | accepted |
| DD-09 | Production admission requires valid, unexpired certification; uncertified agents are refused at the gate | accepted |
| DD-10 | Certifications are signed attestations binding benchmark version, model identity, and environment | accepted |
| DD-11 | Manifest changes require re-certification before promotion; regressions are blocked | accepted |
| DD-12 | Drift is measured against the agent's own certification baseline, not a global standard | accepted |
| DD-13 | Tool outputs are shaped at the boundary (filter, truncate, budget), not inside the agent | accepted |
| DD-14 | Destructive runs execute in an isolated sandbox with resource caps and restricted egress | accepted |
| DD-15 | Certification is necessary but not sufficient — budget, policy, sandbox, and approvals remain enforced at runtime | accepted |

## Format

Each new decision adds a row above and a section below:

```markdown
## DD-NN: <title>

**Context:** ...
**Decision:** ...
**Consequences:** ...
**Alternatives considered:** ...
```
