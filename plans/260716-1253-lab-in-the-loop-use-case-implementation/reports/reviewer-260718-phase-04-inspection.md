# Phase 4 Grounding/Evidence Final Inspection

## Code Review Summary

### Scope
- Files: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/**`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/**`, and relevant canvus-mcp needs-input marker behavior under `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/**`.
- LOC: key fixed source files remain under the 200-line project guideline: `grounding.py` 170, `evidence.py` 150, `state/audit.py` 152, `state_store.py` 195, `orchestrator_setup.py` 79, `orchestrator.py` 163.
- Focus: re-review of final tree after the two prior high-priority Phase 4 findings were fixed and independently re-tempered.

### Overall Assessment
Final Phase 4 is production-ready for the stated MVP scope. The two prior blocking findings are fixed and reproduced directly as passing behavior:

1. Grounding boundary now scans original idea text, emitted setup text, and all bounded retrieved evidence excerpts. The original `BIA` bypass now yields `NEEDS_INPUT`; an approved `BIA` dictionary entry yields `EXECUTABLE` and does not overblock.
2. Grounding audit now sizes the final canonical audit payload against the public StateStore cap and deterministically drops newest evidence rows until it fits. The 1000-row/long-reason reproduction now records audit successfully and needs-input still creates/deduplicates one Browser artifact.

I found no new critical/high/medium defects. Seal approved: score 9.6/10, criticalCount 0.

### Critical Issues
None.

### High Priority
None remaining.

### Medium Priority
None.

### Low Priority / Deferred Concerns
- Acronym dictionary completeness remains an operational concern: scanning all retrieved evidence is intentionally conservative and can surface unknown uppercase terms in notes/PDF metadata. That matches Phase 4's fail-visible safety posture, but dictionary curation will matter in live use.
- Namespaced tool handling remains a future-adapter concern, not a current defect. The current concrete path uses server-provided tool names consistently for allowlisting, invocation, envelope, and ledger ids; canvus-mcp exposes bare names today.

### Fix Verification

#### D1: Original idea / retrieved evidence acronym boundary
Verified implementation:
- `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/grounding.py` now has `_boundary_text(setup, idea_text, ledger)` scanning `idea_text`, `_source_text(setup)`, and `ledger.excerpts()`.
- `evaluate_grounding(..., idea_text="")` remains backward compatible via a keyword-only default.
- `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/orchestrator_setup.py` passes `idea_text` on initial setup path.
- `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/orchestrator.py` passes `next_idea_text` on next-round path.

Direct reproduction now passes:
```text
{'decision': 'needs_input', 'reason': 'unresolved term(s): BIA', 'blocking_terms': ('BIA',)}
{'approved_decision': 'executable', 'approved_reason': 'sufficient evidence, valid citations', 'approved_terms': ()}
```

Regression tests re-run:
- `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_grounding_acronym_boundary.py` covers idea-only acronym, evidence-only acronym, uncited evidence acronym, approved acronym no-overblock, and omitted `idea_text` fallback.
- `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_orchestrator_grounding_boundary.py` covers initial `generate_setup` and next-round `run_loop` call paths.

#### D2: Audit overflow before needs-input write
Verified implementation:
- `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/state/audit.py` exposes `MAX_PAYLOAD_BYTES` and `payload_size_bytes()` using the same canonical JSON encoding as `append_event()`.
- `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/state_store.py` re-exports that public cap/helper.
- `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/grounding.py` now builds the final audit payload (`decision`, `reason`, `evidence`) and drops newest evidence rows until `payload_size_bytes(payload) <= MAX_PAYLOAD_BYTES`.
- Raw evidence remains excluded: rows still contain only `source_id`, `tool`, and `content_hash`; excerpts are in-memory only through `EvidenceLedger.excerpts()`.

Direct reproduction now passes:
```text
{'audit_size': 3975, 'cap': 4096, 'evidence_rows': 28, 'first': 'browser1', 'second': 'browser1', 'browser_count': 1, 'connector_count': 1}
```

Regression tests re-run:
- `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_grounding_audit_overflow.py` covers 1000 evidence rows + long reason audit success under cap and repeated needs-input dedup.

### Edge Cases Checked
- Concurrency/idempotency: needs-input artifacts still use stable `reason_hash` and discriminator `needs_input/predecessor:{predecessor_id}/reason:{reason_hash}`; repeated write returns the same Browser id and one connector.
- Error boundaries: invalid citation and schema failure remain fail-closed/retryable; needs-input remains handled/completed; audit overflow no longer blocks the needs-input path.
- API contracts: `ExperimentSetup` additions stay optional/defaulted; `evaluate_grounding` adds only keyword-only `idea_text` with default, preserving existing callers.
- Backward compatibility: omitted `idea_text` falls back to setup + evidence scan; legacy minimal setup fixtures still parse.
- Input validation/security: model still only receives read tools; write tools remain blocked; retrieved content is wrapped as `untrusted_data`.
- Data leaks: durable audit excludes raw evidence excerpts, tool arguments, credentials, and capability URLs; trimming preserves decision/reason while dropping evidence rows, not raw data.
- N+1/performance: acronym scan is bounded by ledger `max_records` and `EXCERPT_MAX_CHARS`; default scan volume is small and deterministic.
- Overblocking: approved acronyms from the dictionary do not block, including when present in idea and evidence.

### Positive Observations
- The final fix closes the exact trust-boundary hole: the gate no longer trusts the model to decide which terms it saw are worth flagging.
- Audit sizing is centralized on the Store's public cap instead of duplicating a magic number.
- The evidence/audit split is preserved: evidence excerpts are available for in-memory gating but not persisted.
- Targeted tests reproduce the prior failures at both pure function and orchestrator boundaries.
- The final validation suite grew from 305 to 314 tests with focused regressions rather than broad brittle assertions.

### Validation Commands Re-run
- `cd /home/ntdm/dev/lap-in-the-loop/apps/lab-agent && uv run python ...` direct BIA reproduction -> `NEEDS_INPUT`; approved dictionary -> `EXECUTABLE`.
- `cd /home/ntdm/dev/lap-in-the-loop/apps/lab-agent && uv run python ...` direct audit overflow reproduction -> audit size 3975 <= 4096, one deduped Browser, one connector.
- `cd /home/ntdm/dev/lap-in-the-loop/apps/lab-agent && uv run pytest tests/test_grounding_acronym_boundary.py tests/test_grounding_audit_overflow.py tests/test_orchestrator_grounding_boundary.py -q` -> 9 passed.
- `cd /home/ntdm/dev/lap-in-the-loop/apps/lab-agent && uv run pytest -q` -> 314 passed, 1 warning.
- `cd /home/ntdm/dev/lap-in-the-loop/apps/lab-agent && uv run ruff check lab_agent tests` -> clean.
- `cd /home/ntdm/dev/lap-in-the-loop/apps/lab-agent && uv run mypy lab_agent` -> clean.
- `cd /home/ntdm/dev/lap-in-the-loop/apps/lab-agent && uv run python ../../scripts/check-workflow-contract-parity.py` -> self-test OK, parity OK.
- `cd /home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp && uv run pytest -q tests/test_experiment_widgets.py` -> 8 passed.
- `cd /home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp && uv run ruff check canvus_mcp tests/test_experiment_widgets.py && uv run mypy canvus_mcp` -> clean.
- `git -C /home/ntdm/dev/lap-in-the-loop diff --check` -> clean.

### Metrics
- Type Coverage: mypy clean for lab-agent (`58 source files`) and canvus-mcp marker scope (`14 source files`).
- Test Coverage: lab-agent 314/314 tests passed; focused canvus-mcp marker suite 8/8 passed.
- Linting Issues: 0 Ruff issues in verified scopes.
- Finding counts: critical 0, high 0, medium 0, low/deferred 2.

### Recommended Actions
1. Seal Phase 4.
2. Keep acronym dictionary curation on the operational checklist before live scientific use.
3. Revisit namespaced tool semantics only if/when a future MCP adapter exposes non-bare tool names or mixes bare/namespaced citation ids.

### Unresolved Questions
None.

Decision: SEALED. Score: 9.6/10.
