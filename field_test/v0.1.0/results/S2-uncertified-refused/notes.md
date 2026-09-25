# S2 — uncertified-refused (PASS)

**Scenario:** an `uncertified-agent` (never certified) attempts a production run; admission
must refuse it.

**Run:** 20260925T011411Z.

## Result

`POST /runs {workload: uncertified-agent, context: production}` → **403** with the
attributed refusal:

```json
{"detail": "run for workload 'uncertified-agent' refused admission to production:
 certification status 'uncertified' is insufficient for production; requires 'certified'"}
```

The refusal names the workload, the attempted context, the current status, and the
required status — an operator can act on it without reading code.

## Notes

- The admission pipeline (RegistryCertificationGate) fires **before** the run is
  persisted; no run record, no side effects. This is the negative face of the same gate
  S1 exercises positively.
- The fixture is the real support-agent shim under a manifest that is simply never
  certified — the refusal is purely a certification-status decision.

## Evidence

`response.json` — full status + body of the refusal.