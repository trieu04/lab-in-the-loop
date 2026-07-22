---
title: Classification Propagation Bugfix
date: 2026-07-20 05:35
severity: High
component: Lab-agent workflow triggering and Canvus evidence preflight
status: Resolved; live validation pending
---

# Classification Propagation Bugfix

## Context

A live demo produced no visible Canvus change. The durable audit showed why: a missing workflow-trigger classification propagated as an `UNKNOWN` locality denial. That was not the only blocker; the artifact URL and pricing checks were independent failures. The code path crossed `server → register_all → experiments.register`, so the problem was a wiring gap, not a provider verdict.

## What Happened

The callback was wired only to ingestion. Actionable ideas, setups, and loops therefore reached registration without item-level `data_classification`. We repaired propagation for those item types, added bounded `attempt_skipped` audit logs, added two regression-test modules, and updated the demo preflight and recovery documentation.

The uncomfortable part is that a green local path could still leave the demo visibly unchanged. The audit trail carried the useful fact: classification was absent before authorization, and fail-closed behavior did exactly what it was designed to do.

## Decisions

- Keep the pure graph scanner unchanged; fix propagation at the registration boundary.
- Make classification operator-owned and keep unknown values fail-closed.
- Emit bounded `attempt_skipped` logs, not an audit event on every poll.
- Do not add a generic completed-attempt reset.
- Do not invent pricing or expand access-control scope.

These choices preserve the existing safety boundary instead of making the demo look successful by weakening it.

## Verification

Canvus: **137 passed**. Lab-agent: **484 passed**. Ruff, mypy, formatting, compile, parity, and diff checks passed. The hard-evidence gate is **SEALED**. Coverage could not run because the required plugin/executable is absent. No live canvas, provider, or database validation was performed; this is a repaired and verified code path, not a live-success claim.

## Next

The operator must configure the exact canvas mapping, provide a reachable artifact public URL, approve the pricing/version, restart services, inspect current attempt state, and authorize one controlled live retry. A `failed` attempt may retry; a `quarantined` attempt needs a targeted reset; a `completed` attempt requires a new trigger or separately reviewed recovery. The next hand at the bench is operational validation, with the audit inspected before calling the demo fixed.

**Status:** DONE
**Summary:** Repaired missing item-level classification propagation, bounded skip auditing, regression coverage, and operator guidance; local evidence is sealed, while live validation remains intentionally open.
**Concerns/Blockers:** No live canvas/provider/database validation; coverage is unavailable because its plugin/executable is absent; operator configuration and controlled retry are still required.

## Live Demo Follow-up — OpenAI 429

- Live preflight confirmed exact canvas classification `internal`; isolated current-code MCP 8932 was healthy; artifact health was OK; a trusted-service bearer was configured without disclosure; selected model pricing was present with version `demo-local-2026-07-20`.
- The live scanner returned one internal-classified idea and no setup, run, or loop.
- The first cycle hit durable `model_call_exhausted` after three prior provider failures. The operator authorized one extra attempt, changing the model-call cap from 3 to 4. That retry reached OpenAI `POST /v1/chat/completions` and received HTTP 429 on the initial request and SDK retries.
- Post-run state: workflow attempt failed count 4 with `provider_transient`; model intent failed count 4 with safe `provider_error`; zero Setup/Closed/Needs Input artifacts; no canvas write.
- Isolated MCP 8932 was stopped. Pre-existing services were not stopped.

**Next:** Fix the OpenAI quota/rate limit externally before any retry. Do not run again now: the current model intent is exhausted at cap 4 and the workflow attempt is at 4/5. After quota repair, choose one controlled recovery rather than blind retries.
