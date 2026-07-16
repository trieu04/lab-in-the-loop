# Lab-in-the-Loop Planning Phase Complete

**Date**: 2026-07-16 16:40  
**Status**: Resolved  
**Component**: lab-in-the-loop use-case implementation blueprint  
**Scope**: Full planning; no implementation source code written yet  

---

## Context

Started with the lab-in-the-loop use-case specification (`docs/lab-in-the-loop-use-case-specification.md`) and a blank blueprint. The goal was to build a detailed, phase-ordered implementation plan covering the full scope—scientist workflow, lab operations, governance gates, and integration with real-world constraints.

---

## What Happened

Built a complete 9-phase implementation blueprint at `/home/ntdm/dev/lap-in-the-loop/plans/260716-1253-lab-in-the-loop-use-case-implementation/plan.md`:

- **Phases 1–3**: Harness foundation (test runner, durable state, protocol stubs)
- **Phases 4–6**: Core scientist + lab operations (idea submission, execution, artifact service)
- **Phases 7–9**: Governance + integration (authorization gates, external systems, validation)
- **Estimate**: 55 engineer-days (core, adapters TBD)
- **Structure**: 9 phase files + 12 hydrated engineer-task tasks + 3 critical verification tasks
- **Coverage**: Full spec scope through milestone gates and external API boundaries

Applied all red-team findings, recorded validation log, and fixed the missing plan-activation helper script (`/home/ntdm/.claude/scripts/set-active-plan.cjs`).

---

## Decisions

### Architecture & Approach
- **Harness-first, incremental**: Build the test scaffold and durable state layer before scientist UI. Keeps coupling loose and lets API contracts crystallize early.
- **SQLite for recovery**: Durable journal + checkpoint; simple, file-backed, no external dependency. Aligns with "fail visible" mandate.
- **Structured output by default**: JSON + metadata over freeform text. Makes downstream parsing, validation, and artifact indexing predictable.

### Authorization & Workflow
- **Ordered gates**: in-silico validation → scientist → lab-lead approval → external execution. Clear handoff points, audit trail built in.
- **Mocks + protocols first**: Implement the message contracts and happy-path behavior before wiring real external APIs. Surfaces integration boundaries early.

### Critical Late Requirement
- **Dynamic HTML Browser widgets**: Late discovery that system-generated Note widgets need to be replaced with dynamic, tabbed artifact service backed by canonical JSON + metadata. Kept `{idea: ...}` and human input as Notes to preserve the scientist's voice. This is baked into phases 5–6 (artifact service) and phases 8–9 (integration).

---

## Lessons

**The brutality of late requirements is real.** The artifact service shift came during validation, not in the initial spec read. This surfaced because we did structured red-teaming (asking "what does the scientist see when artifacts arrive?") rather than just reading the spec once. It cost planning time but cost nothing in code yet. That's the whole point of planning.

**Structured validation log works.** Recording every decision, gap, and clarification as it crystallized meant the final plan carries its own reasoning. No guessing what the red-team was thinking; it's written down. This saves rework downstream.

**55 engineer-days feels real.** Not inflated, not wishful. Harness setup is grunt work. Phases 4–6 (core workflow) carry the real complexity: state management, message ordering, persistence semantics. The authorization gates in phases 7–9 are straightforward but require careful integration testing.

---

## Next Steps

1. **Implementation ready to start** — all 9 phases are hydrated with steps, acceptance criteria, and TODOs. No blocking unknowns in the plan itself.

2. **Unresolved gates** (known external boundaries, not blockers):
   - Real API discovery (compute service, artifact store, identity provider)
   - TLS ingress configuration (staging vs. production)
   - Locality constraints (data residency, network topology)
   - Identity federation (how external lab systems auth against the harness)

   These don't block the harness or scientist-facing code; they gate integration testing and production deployment.

3. **First move**: Spawn implementation tasks for phase 1–3 (harness foundation) in parallel with phase 4 research (scientist UI spec from artifact service contract).

---

**Status:** DONE
