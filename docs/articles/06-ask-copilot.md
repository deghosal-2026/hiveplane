# `ask`: A Copilot That Can't Break Your Fleet

**Status:** draft · **Pillar:** read-only NL operator surface

## Thesis

Most "AI copilots for infra" are a foot-gun: a natural-language interface wired to mutating
APIs. `ask` is the opposite — it answers live-state questions *read-only by construction*, is
itself under budget and certification, and every query is attributed and tenant-scoped.

## Audience

Operators who want fast answers ("why did that run fail?", "what is team X spending?") without
handing an LLM the keys to production.

## Outline

1. **Read-only by construction.** No mutation method exists on the service; mutating requests
   get a confirmation prompt instead of an action.
2. **Deterministic intent routing.** It routes to known intents (run failure, team spend,
   approval attribution, workload health, fleet status) — no free-form SQL, no guessing.
3. **Scoped and attributed.** Every query is scoped to the caller's tenant and recorded.
4. **It eats its own dog food.** `ask` ships as a first-class workload with a fixed
   five-question benchmark corpus and a `run` entrypoint — under budget and cert like any agent.
5. **Why this matters.** The safest way to add an LLM to a control plane is to bound what it
   can do, then hold it to the same evidence standard as everything else.

## Evidence to link

- Field test gate: "`ask` answers 5 live-state questions, itself under budget + cert"
- [User Guide — Incident Mode, `ask` & CLI Depth](../USER_GUIDE.md)
- CLI: `hiveplane ask "<question>"`
