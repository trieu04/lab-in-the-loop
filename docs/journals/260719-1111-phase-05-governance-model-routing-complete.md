# Phase 5 Complete: Governed Model Routing Holds

**Date**: 2026-07-19 11:11
**Severity**: None (operational close)
**Component**: lab-agent governance, model gateway, provider adapters, durable budget/intents
**Status**: Resolved (Phase 5 of 9 complete)

## What Happened

Phase 5 of `plans/260716-1253-lab-in-the-loop-use-case-implementation` is complete. Provider-neutral task-stage routing now sits above the existing adapters; locality, endpoint, and pricing policy fail closed; usage is recorded as exact or explicitly estimated; restart-safe budget reservations and provider intents survive process loss; retries and reconciliation are typed; stop conditions produce explicit closures; durable errors use safe categories. OpenAI’s canonical SDK base is `/v1`, not a guessed host root. The plan is now 5/9 complete: 26d delivered, 29d remaining.

The work is committed locally as `b1d9312 feat: add governed model routing`. No push was made.

## The Brutal Truth

The design mostly held, but only after inspection made us look where the first pass did not. We had persisted raw provider-error text, undercounted tools and schemas in reservations, allowed unknown pricing to look harmless when no cost cap existed, and briefly broke legacy OpenAI-key configurations by stripping `/v1`. The final defect was caught by a real SDK `httpx.MockTransport`, not by our constructor-level test. That stings because the broken URL looked canonical in a code review. The relief is earned: cycle 3 sealed the work at 9.7/10.

## Technical Details

- Final proof: lab-agent **406** tests, Canvus **37**, governance matrix **131 × 4** with no flakes; endpoint/factory/adapter focused set **15**.
- Ruff, mypy, workflow parity, builds, documentation checks, and `git diff --check` passed.
- The transport regression now proves `https://api.openai.com/v1/chat/completions`, not `https://api.openai.com/chat/completions`.

## What We Tried

We kept the existing adapter factory and chose a small policy/gateway layer instead of introducing a gateway service or provider-specific branches in orchestration. We labeled missing usage as estimated, reserved against durable per-run/per-canvas totals, and refused unknown locality or pricing before dispatch. Review cycles forced the redaction, estimate, pricing, retry, and endpoint corrections.

## Root Cause Analysis

The failures came from trusting local abstractions instead of the actual contracts at their edges: exception text was treated as useful persistence, payload size was approximated, “no cost cap” was mistaken for “price does not matter,” and an SDK base URL was validated only as a string. The `/v1` omission was a compatibility regression hidden by an incomplete test.

## Lessons Learned

Test the boundary that performs the side effect: serialize the full request estimate, deny unknown prices, persist enums rather than provider text, and exercise the real SDK URL builder with a transport. Fail-closed policy is only credible when every default and override is tested, including legacy configuration.

## Next Steps

Phase 6 carries the next move: resumable multimodal ingestion. Before production use, the organization still owns provider/locality approval, maintained pricing, live endpoint/SDK validation, and invoice reconciliation. The remaining unstaged evidence/reports, agent memory, Phase 4 journal, and modified stale canonical `temper-results.json` are intentional intermediate or stale artifacts, not part of committed Phase 5 scope.

## Unresolved Questions

None.

---

**Status:** DONE
**Summary:** Phase 5 governance and model routing are sealed locally at reviewer cycle 3, with endpoint behavior proven through the real OpenAI SDK transport path and all stated local gates passing.
**Concerns/Blockers:** External provider approval, pricing maintenance, live endpoint/SDK validation, and invoice reconciliation remain open operational gates; no commit or push was performed here.
