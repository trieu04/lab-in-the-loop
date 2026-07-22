# Phase 4 Complete: Grounding & Evidence with Deterministic Proof Contracts

**Date**: 2026-07-18 22:39
**Severity**: None (operational close)
**Component**: lab-agent evidence ledger, grounding gates, orchestrator setup/needs-input paths, state audit
**Status**: Resolved (Phase 4 of 9 complete)

## What Happened

Shipped Phase 4 of the Lab-in-the-Loop harness. Every new experiment setup now traces to retrieved evidence via a per-run `EvidenceLedger`, acronyms resolve against an approved dictionary, and insufficiency or ambiguity produce an explicit needs-input flow rather than silent fallback. The implementation moved through two reviewer-caught boundary defects (D1: acronym scope, D2: audit-size capping), both were fixed with targeted regression tests, and the final verification run passed 314 tests with zero critical findings. Commit `7652c7d` sealed the phase with Ruff clean, mypy clean, workflow parity verified, and reviewer score 9.6/10.

## The Brutal Truth

This hurt because the proof was sound but the delivery contract was not. Evidence validation logic was correct — citations checked, ledger looked good, audit summarized cleanly — and tests passed. But the reviewer caught two boundary cases where the tight contract broke: the acronym scanner didn't reach the original idea text (D1), and the audit trimming was done too late (D2). Both were real trust failures, not just edge cases. The fix required admitting that semantic correctness and deterministic machine contracts are different gates. You can have perfect grounding logic and still ship a broken system if the exact bytes being persisted don't match your cap, or if you miss a single code path through a boundary you thought was sealed.

## Technical Details

**D1 Defect (Acronym Boundary Bypass):**
- Unresolved acronym present only in original `idea_text` or retrieved evidence excerpt could be silently omitted by the model and bypass the gate as EXECUTABLE.
- Root cause: `_blocking_terms()` scanned only emitted setup fields via `_source_text()`, not the original idea or retrieved evidence excerpts.
- Fix: Extended `_boundary_text()` to scan `idea_text`, `_source_text(setup)`, and `ledger.excerpts()` together. Gate now requires all three boundaries.
- Verification: Unresolved BIA in idea-only → NEEDS_INPUT. Approved BIA in dictionary → EXECUTABLE (no false positive). Both `generate_setup` and `run_loop` call paths thread `idea_text` correctly.
- Regression tests: 5 new tests in `test_grounding_acronym_boundary.py`.

**D2 Defect (Audit Overflow Blocks Needs-Input Write):**
- Retrieval-heavy ambiguous setup could raise `AuditPayloadTooLargeError` before the needs-input artifact was written, blocking the fail-visible workflow.
- Root cause: `EvidenceLedger.audit_summary()` capped its nested rows; the enclosing payload (rows + decision + reason) was separately capped by `StateStore.append_audit_event()` at 4096 bytes. With 1000 evidence records + long reason, the outer cap fired first.
- Fix: `record_grounding_audit()` now sizes the final canonical payload as a whole and deterministically drops newest evidence rows until payload fits `MAX_PAYLOAD_BYTES`. Raw evidence still excluded; rows contain only `source_id`, `tool`, `content_hash`.
- Verification: 1000 records + 30-term reason records audit successfully at 3975 bytes <= 4096. Repeated poll creates one deduped needs-input Browser artifact and one connector.
- Regression tests: 2 new tests in `test_grounding_audit_overflow.py`.

**Evidence Ledger & Gates:**
- Per-run `EvidenceLedger` captures read-tool results with stable source id, tool, content hash, and a bounded 500-character excerpt.
- Approved acronym dictionary loads as a versioned resource from `resources/acronyms.json`; unknown acronyms remain unresolved, never guessed.
- Citation validation requires all emitted citation ids to exist in the ledger; fabricated citations trigger `INVALID_CITATION` (fail-closed, retryable).
- Insufficient evidence or blocking ambiguity create/deduplicate one `[EXP:Needs Input vNNN]` Browser artifact per stable reason hash; executable setup remains pending.
- Audit payload excludes raw excerpts, tool arguments, credentials, and URLs; trimming preserves decision/reason while dropping evidence rows.

**Quality Gates:**
- Test coverage: 314/314 pass (305 baseline + 9 new D1/D2 regression tests). No flakes, all deterministic.
- Linting: Ruff clean (0 issues), mypy clean (58 source files).
- Workflow parity: `scripts/check-workflow-contract-parity.py` verified orchestrator/canvus-mcp marker alignment.
- Git diff: No trailing whitespace or format drift.

## What We Tried

One path: build evidence capture into the read-tool loop, validate citations before setup writes, create a deterministic audit contract keyed by decision/reason/evidence rows, and use backward-compatible optional fields on `ExperimentSetup` so legacy MVP notes remain readable. New defect surface: acronym scanner scope and audit-size capping. The first attempt missed both because the reviewer asked adversarial questions: "What if the idea text itself has an acronym?" and "What happens to the audit when you have 1000 rows plus a long reason?"

Both were answered correctly in the second iteration. The architecture held.

## Root Cause Analysis

**D1:** Tunnel vision on `_source_text()` as the complete boundary. The model reads the idea before the setup; the original idea is the user's input. The boundary scanner must include it. Test coverage was broad but the specific path (idea-only acronym) was missing because we didn't ask: "Could an acronym hide in the input?"

**D2:** Two separate capping layers that were not composed. `EvidenceLedger.audit_summary()` capped its own rows. `StateStore.append_audit_event()` capped the full payload. In the presence of 1000 rows, the outer cap fired first, and the needs-input write never happened. The fix centralized sizing on the outer contract: canonical JSON encoding, one MAX_PAYLOAD_BYTES, and deterministic row dropping to fit.

Both required the reviewer to probe the exact boundary inputs and think about what breaks when you scale: 1000 evidence rows, 30 unresolved terms, one needs-input artifact. The model was never involved; these were system-level contracts.

## Lessons Learned

1. **Semantic correctness is not delivery correctness.** Evidence logic can be sound (citations exist, ledger is valid) and still ship broken if the exact bytes don't fit the machine contract (audit cap, schema encoding). Adversarial review must probe boundary inputs, not just happy paths.

2. **Deterministic delivery gates require exact contracts.** A 4096-byte cap is not a suggestion; audit trimming must happen before write, not after. Version one: "We have a ledger audit summary." Version two: "We have a ledger audit summary that canonically serializes to ≤4096 bytes."

3. **Boundary assumptions hide in plain sight.** The acronym scanner assumed: "Setup fields are the boundary." Missing: original idea, retrieved evidence excerpts. The audit sizer assumed: "Ledger rows cap themselves." Missing: the full payload cap. Both look reasonable in isolation; both fail when tested adversarially.

4. **Regression tests must cover the failure mode, not just the fix.** `test_grounding_acronym_boundary.py::test_needs_input_when_unresolved_acronym_only_in_idea_text` directly reproduces the D1 failure. If acronym scanning regresses, that test fails. Same for D2 audit overflow.

## Next Steps

- **Phase 5** (governance and model routing, 5d): Acronym dictionary ownership and wiki/KG/vector endpoints remain external gates not verified locally. Phase 4 hard contract is approved dictionaries only, with no heuristic expansion; Phase 5 defines the governing policy.
- **Acronym dictionary ownership**: Organization domain owner should populate `resources/acronyms.json` with domain-specific terms before production deployment.
- **Evidence retention policy**: Phase 4 bounds per-run ledger in memory and durable audit to metadata only. Phase 5+ should define long-term evidence archival if needed.
- **Unresolved operational gates**: (1) Dictionary completeness and curation before scientific use. (2) External retrieval endpoints (wiki, KG, vector) and their reachability from production harness. (3) Evidence tabs in Browser artifacts wired to full provenance metadata (Phase 5+).

---

**Status:** DONE
**Summary:** Phase 4 completed with evidence ledger, citation validation, acronym grounding, and bounded audit. Two reviewer-caught boundary defects (D1: acronym scope, D2: audit-size capping) were fixed and regression-tested. Final verification: 314/314 tests pass, Ruff/mypy/parity clean, reviewer sealed 9.6/10, zero critical findings. Commit 7652c7d ready for integration.
**Concerns/Blockers:** None. External dictionary curation and retrieval-source policy remain Phase 5 decisions; documented as future gates in roadmap.
