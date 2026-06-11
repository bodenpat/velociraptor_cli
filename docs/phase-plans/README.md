# Phase plans

Pick-up-and-execute plans for the remaining work. Phases 0–3 are **done**
(shipped in `v0.1.0`); these plans cover what is left.

| Phase | Status | Plan |
|---|---|---|
| 0 — Security baseline & scaffolding | ✅ done (`v0.1.0`) | PLAN.md §8; see `docs/architecture.md` §10 |
| 1 — Core library | ✅ done (`v0.1.0`) | PLAN.md §8; `docs/architecture.md` |
| 2 — Triage vertical slice | ✅ done (`v0.1.0`) | as-built: `docs/api-coverage.md`, `docs/architecture.md` |
| 3 — Remaining verbs + full coverage | ✅ done (`v0.1.0`) | as-built: `docs/api-coverage.md`; residual checks in [../../TODO.md](../../TODO.md) |
| **4 — SOAR rollout & playbooks** | ⬜ not started | **[phase-4.md](phase-4.md)** |
| **5 — Native InsightConnect plugin** | ⬜ future | **[phase-5.md](phase-5.md)** |

Phases 2 and 3 need no forward plan — the code exists and is documented as
built. The one piece of forward work they left is the **Phase-3 artifact
parameter verification** checklist, tracked in [../../TODO.md](../../TODO.md)
and referenced from the Phase 4 plan (you must verify each artifact against
the tenant before trusting the verb that uses it in production).

## How to use these plans

Each plan is an ordered task list. Work top to bottom; a task's acceptance
gate must pass before the next task that depends on it. Every task carries a
tag saying **who can do it**:

- **`[agent]`** — doable now by a coding agent in this repo, no tenant or
  human-only system access required. Pure code, docs, scripts, tests.
- **`[tenant]`** — requires access to the live Rapid7 / InsightConnect
  environment (the hosted Velociraptor tenant, the InsightConnect console,
  the orchestrator host, a SOAR service-account key, an enrolled lab host).
- **`[human]`** — a judgement, sign-off, scheduling, or organizational step
  (assigning an owner, running a tabletop, deciding a branch).

Many tasks are **`[agent]` deliverables that a `[tenant]`/`[human]` step then
consumes** — e.g. an agent writes a workflow-spec document, a human recreates
it in the InsightConnect UI. Those are tagged `[agent → tenant]`.

Conventions used throughout: commands are runnable as-is; file paths are
repo-relative; placeholder IDs follow the repo rule (every hex group one
repeated character, e.g. `11111111-2222-3333-4444-555555555555`); the API
key is never on a command line (env / connection only).
