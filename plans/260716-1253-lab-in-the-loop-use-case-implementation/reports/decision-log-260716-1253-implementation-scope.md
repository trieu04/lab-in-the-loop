---
title: "Lab-in-the-Loop implementation planning decisions"
description: "Owner-approved scope and architecture decisions for the full use-case implementation blueprint."
created: 2026-07-16
status: confirmed
---

# Lab-in-the-Loop Implementation Planning Decisions

## Context

Source: `docs/lab-in-the-loop-use-case-specification.md`.

Scope mode: **HOLD SCOPE** — plan the complete target specification as a milestone-based master blueprint. Do not silently remove `[Future]` capabilities; external integrations remain gated until their real contracts are available.

## Confirmed Decisions

| Decision | Selected direction | Planning consequence |
|---|---|---|
| Target architecture | Harness-first, incremental | Evolve `lab-agent` into the independent provider-neutral harness. Keep Claude Code/MCP registration as optional interfaces. Avoid rewrite. |
| Contract evolution | Additive by milestone | Preserve MVP compatibility. Add evidence/insufficiency first; use dedicated contracts for in-silico, approvals, execution, analysis, and versioning when their phases begin. |
| Wet-lab authorization | In-silico → scientist → lab lead | Optional preliminary screening may revise/reject design but never authorizes wet lab. Durable post-silico scientist approval and lab-lead approval are mandatory. |
| Manual loop trigger | Same-round or backward edge | `result → setup` targeting a new/future round is a chain edge, not a trigger. Same-round/backward user edges may trigger reconsideration. |
| Invalid structured output | Fail visible, leave pending | Never fabricate required workflow/safety fields. Skip writes/transitions, audit the validation error, retry safely on a later poll. |
| Durable state | SQLite ledger first | Use WAL, unique idempotency keys, canvas/run scoping, and a documented migration boundary for future Postgres/multi-host deployment. |
| Missing external endpoints | Protocols + mocks first | Define typed adapters, dry-run/mock implementations, and shared contract tests. Real adapters require endpoint, credential, SLA, security, and safety readiness gates. |
| Generated canvas artifacts | HTML Browser widgets | Replace system-generated Setup/Result/Closed/Needs Input/validation/analysis/knowledge artifacts. Keep `{idea: ...}` and human input as Notes. |
| HTML/data storage | Dynamic artifact service | Store canonical JSON/metadata in the harness SQLite artifact store; Browser widget holds a stable capability URL rendered as tabbed HTML. |

## Defaults Resolved by Project Conventions

- No provider gateway yet; wrap the existing adapter factory with routing/locality/budget policy. Reconsider LiteLLM/OpenRouter only after a measured provider-count or operations trigger.
- Keep schema/marker ownership inside their natural app boundaries initially; enforce parity with shared contract tests before introducing a new package.
- Administrator/auditor is treated as a system role/capability until a human persona is required by access-control or compliance requirements.
- Baseline audit evidence is tamper-evident application history, not regulated electronic signature. A 21 CFR Part 11 requirement is a later explicit compliance gate.
- Single-host/single-writer-per-canvas is the first production topology; multi-host coordination is deferred to the production-hardening milestone.

## Remaining External Gates

- Real in-silico API and scientific validation owner.
- Flywheel/HPC API, job/event semantics, and data retention rules.
- Robot/lab scheduler API, safety sign-off, abort/compensation behavior.
- Versioned knowledge-store API and conflict-resolution policy.
- Organization-approved provider/data-locality matrix and pricing source.

## Unresolved Questions

None required to write the blueprint. External gates above become explicit milestone entry criteria rather than hidden assumptions.
