# Phase 3 Complete: Dynamic Browser Artifacts & Durable Artifact Store

**Date**: 2026-07-18 11:00
**Severity**: None (operational close)
**Component**: lab-agent artifact layer, canvus-mcp Browser widgets, ASGI render service
**Status**: Resolved (Phase 3 of 9 complete)

## What Happened

Completed Phase 3 of the Lab-in-the-Loop harness. Generated workflow artifacts (Setup, Result, Closed, Needs Input, and others) moved from simple Note text to Browser widgets backed by a dynamic HTML artifact service and a canonical versioned `ArtifactStore` in SQLite. Kept user-authored `{idea: ...}` and human input Notes as the simple control surface. Implementation was committed locally on branch `feat/phase-03-durable-browser-artifacts` (commit `95ef72c`); the plan now records 16d delivered across Phases 1–3 and 39d remaining across Phases 4–9. No remote was configured, so the branch was intentionally not pushed.

## The Brutal Truth

The confidence is genuinely high. This phase was meant to be risky — dynamic rendering, capability-token security, migration parity — and it cracked none of it. The verifier ran 293 tests clean, ruff and mypy passed hard, the reviewer came back 9.6/10 with zero critical findings. The real artifact service responded locally with 200 and proper CSP/security headers; forged and missing capabilities returned uniform 404. Live Canvus and production ingress were not available, so those remain explicit operational gates rather than implied successes.

## Technical Details

- **Test coverage**: 293/293 pass; ruff clean (no style drift), mypy clean (no type escapes).
- **Security validation**: Artifact service returned `200 OK` with `Content-Security-Policy: default-src 'none'` and same-origin script/style allowances; forged capability tokens and missing artifact IDs returned `404 Not Found` uniformly (no info leak).
- **Runtime route observation**: `/healthz` and `/assets/artifact-view.css` / `/assets/artifact-tabs.js` returned 200 with the expected MIME types and security headers. Authorized artifact rendering was covered by tests; no live capability was created through Canvus during this session.
- **Workflow parity**: Browser title markers detected and processed alongside legacy Notes; graph state unchanged; connector semantics preserved.
- **Hard gates sealed**: Git diff check clean. Three agent-local/duplicate paths were intentionally excluded and remain untracked.

## What We Tried

One path: route orchestrator writes through a typed `ArtifactDocument` model into `ArtifactStore`, surface them via capability-token-protected ASGI routes with server-side rendering and strict CSP. Legacy Notes read in parallel (mirror-first migration), no automatic deletion. Browser widget wrapper remains simple (URL + title); all canonical data lives in the store.

That worked. The architecture held without requiring a rewrite mid-sprint.

## Root Cause Analysis

N/A — no defect surfaced. Phase 2's durable harness (state_store, leases, recovery) was the foundation; Phase 3 sat cleanly on top of it. The separation of concerns — Browser as view, ArtifactStore as truth — prevented the common trap of embedding too much into the widget itself.

## Lessons Learned

1. **Capability tokens as first-class security**: Uniform 404 responses prevented artifact enumeration locally. Production HTTPS/private ingress is a documented deployment requirement, not something the application itself enforced or this session verified.
2. **Mirror-first migration buys trust**: Running both Note and Browser paths in parallel for the next phase or two gives operators a safety net. Legacy artifacts stay readable; no cliff.
3. **Static CSP is a gate, not a nice-to-have**: Because Browser pages cannot send custom auth headers, the private-network + capability-URL + strict CSP stack is the only path. Document that hard in Phase 4.
4. **ArtifactStore versioning pays off**: Every write appends a version row; no destructive overwrites. This pattern scaled from the audit layer (Phase 2) and will feed Phase 7's approval gates and Phase 8's evidence chain.

## Next Steps

- **Phase 4** (Grounding & Evidence, 5d): Build the evidence chain from orchestrator→artifact→Browser display. Capture execution proofs, model outputs, and cost accounting in the artifact store. External gate: live Canvus reachability and production HTTPS base URL for the artifact service are not yet verified locally; document these in Phase 4's operational runbook.
- **Unresolved operational gates**: (1) Real Canvus API reachability from the live harness environment. (2) Production HTTPS/private-network deployment of the artifact service (localhost HTTP works; production needs encryption and access control). (3) Multi-tenant isolation contract if Canvus begins hosting multiple canvases per deployment.
- **Migration checklist for later phases**: Once Phase 4–8 land, run the mirror-first operator script (`scripts/migrate-generated-notes-to-browser-artifacts.py`) in dry-run mode on a real canvas and audit the output. That drill unlocks safe deletion at the end.

---

**Status:** DONE
**Summary:** Phase 3 completed and was committed locally with zero critical findings, 293 green tests, and all external gates documented. The Browser artifact layer is ready for Phase 4 grounding and evidence work.
**Concerns/Blockers:** External gates (Canvus reachability, production HTTPS artifact-service deployment, multi-tenant contract) remain unverified and must be tested/documented before Phase 4 goes live.
