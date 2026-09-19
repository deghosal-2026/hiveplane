# D17: LLM Provider Design

> Status: proposed (M23, #104). This document defines the LLM provider seam. It is the
> prerequisite for real agents, real certification, and model-identity enforcement.

## Problem

HivePlane governs agent runs, but there is no way for an agent to invoke a model. The
`WorkerContext` handed to a workload exposes tool calls, usage reporting, and pause/cancel — but
no model call. The example agents are deterministic stubs with hardcoded outputs and synthetic
usage. As a result:

- model identity is a self-reported string, so the T11 model-swap defense is not meaningful;
- token usage and cost are invented by the agent, not observed from inference;
- the trace-linked debug context cannot show "model calls" (PRD 05:70);
- the `rubric` benchmark check has no evaluator model to call.

The PRD requires real agents (`07-success-metrics.md`: "three real agents") and a benchmark that
"must be real" (`08-risks.md`). This design closes that gap without turning HivePlane into a
model framework (DD-02): it integrates with existing providers, it does not replace them.

## Goals

- One provider seam that supports **local** (Ollama/OMLX-style OpenAI-compatible endpoint),
  **cloud** (OpenAI), and **fake/replay** (deterministic, offline, CI-safe) backends.
- Model calls route through the control-plane boundary, so they are governable, metered, traced,
  and identity-checked.
- Usage and cost are captured from the provider response, not supplied by the agent.
- The runtime model identity is observed from inference and checked against the certification
  binding (T11).
- CI runs hermetically with no network and no secrets.

## Non-Goals

- Building, training, or hosting models.
- Replacing provider SDKs with a new abstraction layer beyond what governance requires.
- Provider-specific advanced features (function calling formats, multimodal, streaming UI).
  Streaming may be added later behind the same seam.

## Provider Abstraction

```
LLMProvider (Protocol)
  complete(request: CompletionRequest) -> CompletionResponse

CompletionRequest
  messages: list[Message]            # system/user/assistant
  model: str                         # canonical provider/family/version
  temperature: float = 0.0
  max_tokens: int | None = None
  timeout_s: float | None = None
  metadata: dict[str, JsonValue]     # run_id, workload, purpose

CompletionResponse
  content: str
  model_identity: str                # reported by the provider, not the caller
  usage: TokenUsage                  # input_tokens, output_tokens
  finish_reason: str
  raw: dict[str, JsonValue] | None   # provider payload, redacted before persistence
```

The seam is provider-agnostic. Concrete implementations:

| Provider | Backend | Network | Secrets | Use |
|----------|---------|---------|---------|-----|
| `local` | Ollama / OMLX OpenAI-compatible `/v1/chat/completions` | localhost | none | local field test, offline dev |
| `cloud` | OpenAI API | yes | API key | cloud field test, production |
| `fake` | In-process deterministic replay | none | none | CI, unit tests, benchmarks |

### Fake / replay provider

- Returns canned completions keyed by a hash of the request (model + messages + params), so the
  same input always yields the same output.
- Supports a fixture/replay file for realistic benchmark behavior: a corpus task can map to a
  recorded completion so certification is deterministic without a live model.
- Reports deterministic token counts derived from the prompt/response length.
- Never touches the network; usable in CI with zero secrets.

## Configuration

New `ModelSettings` section on the root settings object (`hiveplane.config`), following the
existing `HIVEPLANE_<SECTION>__<FIELD>` convention:

| Env var | Default | Meaning |
|---------|---------|---------|
| `HIVEPLANE_MODEL__PROVIDER` | `fake` | `local` \| `cloud` \| `fake` |
| `HIVEPLANE_MODEL__BASE_URL` | provider default | OpenAI-compatible base URL (e.g. `http://ollama:11434/v1`) |
| `HIVEPLANE_MODEL__API_KEY` | none | Secret; required for `cloud` |
| `HIVEPLANE_MODEL__DEFAULT_MODEL` | none | Fallback canonical identity when a manifest omits one |
| `HIVEPLANE_MODEL__TIMEOUT_S` | `60` | Per-call timeout |
| `HIVEPLANE_MODEL__MAX_RETRIES` | `2` | Transient-failure retries |
| `HIVEPLANE_MODEL__REPLAY_FILE` | none | Fixture file for the fake provider |

Per-workload overrides come from the manifest (`spec.model`); the manifest identity wins over the
environment default and is the identity bound into the attestation.

## Agent Invocation Seam

`WorkerContext` gains one method:

```
ctx.complete(prompt_or_messages, *, temperature=None, max_tokens=None) -> CompletionResult
```

`CompletionResult` (agent-facing) carries `content`, `model_identity`, `usage`, and
`finish_reason`. The seam:

1. Resolves the provider and the model identity for the run.
2. Calls `LLMProvider.complete()`.
3. **Captures usage** from the response and reports it through the existing `RunReporter`
   (`UsageReport` already carries `model_identity`), so budget enforcement and cost attribution
   work unchanged.
4. **Verifies model identity**: the provider-reported identity is canonicalized and compared
   against the run's bound identity (manifest → attestation). A mismatch raises a
   `ModelIdentityMismatchError`, records a security event, and fails the run with
   `refused: model_swap` (T11).
5. Emits a `model_call` telemetry span with model, tokens, cost, latency, and truncated
   prompt/response. Secrets are redacted before persistence (DD-07).
6. Calls `ctx.checkpoint()` before and after the call so pause/cancel is honored between calls.

Agents must use `ctx.complete()`; direct provider/SDK access is forbidden by the adapter
contract and checked by the conformance suite.

## Model-Identity Enforcement (T11)

Today the identity is self-reported at submission. With the seam:

- **At admission** (unchanged): the submitted identity is checked against the attestation.
- **At call time** (new): the provider-reported identity is checked per call. For `router`
  strategies, every call is checked; for `fixed`/`tiered`, the bound model is enforced.
- A mismatch is a security event and blocks the run. This makes the model-swap defense real:
  the control plane no longer trusts the caller's claim about which model ran.

Identity canonicalization reuses `canonical_model_identity` / `validate_model_identity`
(`hiveplane.core.spec`). Providers that report an alias must map it to the canonical
`provider/family/version` form.

## Pricing and Budget Integration

`CostTable` currently raises `UnknownModelPriceError` for any identity not in `DEFAULT_PRICES`
(only two OpenAI models and one Anthropic model today). Local and fake models must price
cleanly:

- add price entries for local/fake identities (typically zero-cost);
- allow a per-environment price override map (`HIVEPLANE_BUDGET__PRICES` or a fixture) so the
  field test can price local models at zero without lying about cloud models;
- unknown models still fail loudly (the loud-failure behavior is intentional and tested).

Usage flows: provider response → `UsageReport` (with `model_identity`) → `RunReporter` →
`BudgetService` → spend/cost attribution. No new usage path is introduced.

## Telemetry

- Span name `model_call`, child of the run's `execution` span.
- Attributes: `model_identity`, `input_tokens`, `output_tokens`, `cost_usd`, `latency_ms`,
  `finish_reason`, `provider`, `run_id`, `workload`, `purpose`.
- Prompt/response are attached as truncated attributes only; secrets are redacted.
- The run-story renderer must include model calls (#124).

## Error Handling

| Condition | Behavior |
|-----------|----------|
| Provider unreachable / timeout | Retry up to `MAX_RETRIES`, then fail the run with an attributed reason |
| Provider auth error | Fail fast; never log the key |
| Model identity mismatch | Security event + `refused: model_swap` (do not retry) |
| Unknown model price | Fail loudly (existing `UnknownModelPriceError` semantics) |
| Usage missing from response | Fail the call (usage is required for budget integrity) |

## Testing Strategy

- Unit: each provider against a mocked transport; fake provider determinism.
- Contract: a shared provider-conformance suite — same request, same response shape, usage
  always present, identity always reported.
- Integration: a run that calls `ctx.complete()` end-to-end with the fake provider, asserting
  usage/cost/identity flow and a `model_call` span.
- Security: a run where the provider reports a different model → run blocked, security event.
- CI: only the fake provider is exercised; no network, no secrets.

## Open Questions

- Streaming responses — out of scope for v0.1.0; the seam is request/response.
- Per-call identity for `tiered` strategies (which model is "bound" when a tier is chosen).
- Whether provider credentials live in config only or also in the `llm_provider_configs` table.
- Prompt/response retention policy for traces (truncation length, redaction guarantees).

## See Also

- [Runtime adapter design](runtime-adapter-design.md) (D6) — adapter contract, agent seams
- [Certification pipeline design](certification-pipeline-design.md) (D10) — pinned model, rubric
- [State store design](state-store-design.md) (D7) — `llm_provider_configs`
- [Budget enforcement design](budget-enforcement-design.md) (D5) — pricing, usage events
- [PRD 06: Security baseline](../prd/06-security-baseline.md) — T11, T6
- [Design decisions](design-decisions.md) — DD-02, DD-10
