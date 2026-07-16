---
name: project-lap-in-the-loop-harness-proposal
description: lap-in-the-loop's harness-first architecture direction is proposed only, not owner-ratified, as of 2026-07-16
metadata:
  type: project
---

A 2026-07-16 architecture-recovery review (recovered from an unrelated read-only `rag-canvus` session, transcript id `958a41ff-c7bc-4992-8559-5bcf9e229f6d`) concluded Lab-in-the-Loop should evolve as an independent, provider-neutral harness/orchestrator (`lab-agent`, evolved), with the Claude Code skill/MCP registration as an optional developer/operator/demo interface only — not the production runtime or source of truth for workflow logic, policy, grounding, or schemas.

This was documented across `README.md`, `docs/system-architecture.md` ("Target harness boundary (proposed)"), `docs/development-roadmap.md`, and `docs/code-standards.md` on 2026-07-16.

**Why:** The source session's `AskUserQuestion` architecture-choice prompt was interrupted and never answered — there is no explicit owner ratification ("approved", "proceed") in the transcript. It is the session's final recommendation, not a decided architecture.

**How to apply:** When writing or updating docs in this repo, keep the harness-first direction framed as "proposed, pending owner confirmation" everywhere it appears. Do not upgrade it to "approved"/"implemented" language unless a later conversation shows explicit owner sign-off (check `docs/project-changelog.md` for a dated entry recording that confirmation before treating it as settled). Do not create `docs/decisions/ADR-*` for this — canonical policy treats ADRs as human-owned, and this decision is not ratified.

Related: this repo's MVP-vs-target-harness distinction — current implementation is `canvus-mcp` + `lab-agent` with OpenAI/Claude adapter factory, OpenAI-compatible `base_url` for compatible backends (no dedicated Ollama/vLLM adapters), mock robot execution, and in-memory (non-durable) loop idempotency. See `docs/system-architecture.md` "Current MVP vs. target harness" for the current authoritative split.
