---
title: "Lab-in-the-Loop blueprint red-team review"
description: "Adversarial review across security, failure modes, assumptions, and scope complexity."
created: 2026-07-16
status: applied
---

# Lab-in-the-Loop Blueprint Red-Team Review

## Context

Target: `plans/260716-1253-lab-in-the-loop-use-case-implementation/`.

Four reviewer subagents were attempted in parallel, but the Agent safety-classifier was unavailable. The same four lenses were completed in the main thread: Security Adversary, Failure Mode Analyst, Assumption Destroyer, Scope & Complexity Critic. User approved applying all adjudicated findings.

## Summary

- Findings: 12
- Severity: 3 Critical, 5 High, 4 Medium
- Disposition: 11 Accept, 1 Accept (modified), 0 Reject
- Core estimate revised: 31d → 48d after red-team, then → 55d after the owner-added Browser artifact phase; real adapters remain TBD after API discovery.

## Findings

| # | Finding | Severity | Disposition | Applied To |
|---|---|---|---|---|
| 1 | Claim-before-completion can permanently lose work after crash | Critical | Accept | Phase 2 attempt lifecycle + expiring lease |
| 2 | Canvas/provider side effects and audit are not atomic | Critical | Accept | Phase 2 durable intent/outbox + reconciliation |
| 3 | Canvas/free-text identity cannot authorize wet lab | Critical | Accept | Phase 7 IdentityProvider readiness gate |
| 4 | Poll retries can create provider/cost storms | High | Accept | Phase 2 backoff, max attempts, quarantine; Phase 4 budgets |
| 5 | Tenant context does not isolate process-global credentials | High | Accept | Phase 9 one credential domain per process pair |
| 6 | Insufficient-evidence clarification can duplicate every poll | High | Accept | Phase 4 explicit marker, reason hash, graph dedup |
| 7 | Model allowlist does not authenticate MCP mutation callers | High | Accept | Phase 6 transport/trusted-client/operator authorization |
| 8 | SQLite audit lacks migration, integrity, and recovery contract | High | Accept | Phase 2 migrations, hash chain, integrity check, backup drill |
| 9 | 31d core estimate is not credible | Medium | Accept | Plan and phase estimates revised to 48d |
| 10 | Execution phase combines three external domains with different readiness | Medium | Accept (modified) | Phase 8 sub-milestones + separate real-adapter child plans |
| 11 | Cross-app marker/schema drift lacks an executable parity gate | Medium | Accept | Phase 1 parity script; Phase 9 CI gate |
| 12 | Escaping evidence is insufficient prompt-injection control | Medium | Accept | Phase 4 structural data/instruction separation + adversarial tests |

## Supplemental Browser-Artifact Scope Review

The later owner change replacing generated Notes with dynamic HTML Browser artifacts was reviewed inline. Phase 3 now explicitly covers capability-URL leakage, XSS, artifact-store/canvas drift, service reachability, cross-canvas authorization, and dry-run/mirror-first migration damage.

## Key Risks Closed

- Restart no longer treats a leased attempt as completed.
- Ambiguous external/canvas effects are reconciled before retry.
- Human authorization requires verified identity and current proposal/result hashes.
- Retries are bounded, auditable, and quarantined instead of hammering providers.
- Mutating MCP tools receive an authorization boundary independent of model allowlists.
- Safety audit data has schema/version/integrity/backup requirements.

## Unresolved Questions

None for blueprint completion. Real integration child plans remain blocked on API, identity, locality, safety, retention, and SLA discovery.
