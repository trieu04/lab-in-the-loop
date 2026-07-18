# Phase 4 Adversarial Diagnostic — Grounding & Evidence

Read-only investigation, independent of tester. Full suite baseline: `uv run pytest -q` → 301 passed, 0 failed (no regressions from this review).

## Confirmed Defect

### D1 — Acronym re-scan omits 4 of 9 free-text setup fields (FR-LITL-016 / BR-LITL-003 gap)

`grounding._source_text()` (`apps/lab-agent/lab_agent/grounding.py:46-47`):

```python
def _source_text(setup: ExperimentSetup) -> str:
    return " ".join([setup.rationale, setup.hypothesis, *setup.steps, *setup.conditions, *setup.inputs])
```

Only scans `rationale`, `hypothesis`, `steps`, `conditions`, `inputs`. `ExperimentSetup` (models/experiment.py) has 4 more free-text list fields never included: `success_criteria`, `parameters`, `expected_readouts`, `constraints`.

`_blocking_terms()`'s own docstring claims: "Re-verifies both the model's self-reported `ambiguity_flags` and any acronym-like term the model didn't flag at all — the dictionary is the ground truth, never the model's own `resolved` claim." That guarantee is false for the 4 omitted fields: if an unresolved acronym-like term appears ONLY in `success_criteria`/`parameters`/`expected_readouts`/`constraints` and the model does not self-flag it in `ambiguity_flags`, the gate never detects it and the setup can reach EXECUTABLE.

Reproduced (non-mutating probe, `evaluate_grounding` called directly, empty acronym dictionary):

```python
setup = ExperimentSetup.model_validate({
    "rationale": "Mix samples per protocol.",
    "steps": ["combine reagents"], "inputs": ["reagent A"],
    "success_criteria": ["PCR yield exceeds threshold"],   # unresolved acronym, NOT self-flagged
    "evidence_status": "sufficient",
    "citations": [{"source_id": real_ledger_id}],
})
verdict = evaluate_grounding(setup, ledger, EMPTY_DICT)
# actual: decision=EXECUTABLE, reason="sufficient evidence, valid citations", blocking_terms=()
# expected per BR-LITL-003/plan item 7: NEEDS_INPUT, blocking_terms=("PCR",)
```

Confirmed this is not covered by the existing suite: `test_grounding.py`'s "unflagged acronym" regression test (`test_unflagged_acronym_like_term_is_still_detected_and_blocks`) only puts the term in `rationale`, which the current scan does cover — masking the gap.

**Fix scope (for implementer):** include all free-text fields in `_source_text`, e.g. `*setup.success_criteria, *setup.parameters, *setup.expected_readouts, *setup.constraints`; add a regression test with the acronym confined to one of the 4 omitted fields.

## Concerns (not confirmed defects — flagging for judgment, no repro of exploitability)

**C1 — Citation coverage is "≥1 valid" not "every factual claim cited."** `EvidenceLedger.validate_citations` requires only one resolvable citation to pass; a setup with 6 steps and 1 citation is EXECUTABLE. This matches the plan's stated deterministic scope (membership check, not claim-level coverage) — enforcement of "cite every factual claim" is prompt-only (`prompts.SETUP_SYSTEM`), not code-gated. NFR-LITL-004 ("every setup cites the internal evidence used") is only partially guaranteed by code; the rest rests on the model following instructions. Worth a product decision on whether stricter (e.g., per-section citation) is in scope, not a bug in what was built.

**C2 — Tool-name allowlist checks the bare (namespace-stripped) name but executes the original name.** `tool_bridge.execute_tool_calls` gates on `_bare_name(call.name) in READ_TOOLS` then calls `mcp.call_tool(call.name, ...)` with the *un-stripped* name. Checked the only live MCP server (`apps/canvus-mcp`) — it registers flat, non-namespaced tool names (`get_note`, `create_note`, etc.), so no tool today has a namespaced name whose bare suffix collides with a `READ_TOOLS` entry while being a different (write) capability. Not exploitable now; would become a real gap if/when a namespaced or multi-server adapter is introduced (the test suite's own `mcp__canvus__get_note` fixtures anticipate this). Recommend re-verifying this check before any namespaced-tool adapter ships.

## Reviewed — no defect found

- **Citation membership / empty / mixed citations**: `validate_citations` correctly requires ≥1 citation and rejects zero, fully-fabricated, and mixed valid/fabricated sets — verified against `test_grounding.py` + `test_evidence.py` and re-confirmed via direct probe.
- **Evidence-sufficiency precedence**: order in `evaluate_grounding` is evidence_status → blocking terms → citations, matching the module docstring and plan item 7 exactly (blocking term wins over valid citations; unset/insufficient status short-circuits before any citation check). Confirmed via `test_blocking_term_wins_even_with_valid_citations`.
- **Acronym collision handling**: `AcronymDictionary.resolve` only returns `True` for exactly one candidate; `resources/acronyms.json`'s `CT` entry (two candidates) is confirmed unresolved by `test_resolve_colliding_candidates_stays_unresolved`. The gate never trusts the model's self-reported `resolved`/`expansion` — always re-derives from the dictionary (subject to D1's field-coverage gap).
- **Untrusted-data injection isolation**: write tools are structurally blocked in `execute_tool_calls` regardless of any embedded "instructions" in retrieved content (verified by `test_untrusted_data_envelope_does_not_grant_embedded_instructions` + `test_execute_tool_calls_runs_allowed_and_blocks_writes`); schema is Pydantic-enforced independent of model claims; citations independently re-validated against the ledger. `artifact_render.py` escapes all rendered values via `html.escape` and sets a restrictive CSP (`script-src 'self'`, no inline), so injected text in evidence cannot execute in the rendered artifact.
- **Read/write tool authorization**: `READ_TOOLS` frozenset is locked by `test_read_tools_allowlist_unchanged`; blocked calls never reach the MCP session and are never recorded in the ledger (`test_ledger_excludes_blocked_writes_and_error_payloads`).
- **Reason-hash dedup across retries/restarts**: `compute_reason_hash` is a pure function of the reason string; blocking-term lists are sorted before joining into the reason so the hash is stable across runs with the same underlying facts. `test_needs_input_setup_converges_on_restart_same_reason` and `test_calling_twice_converges_on_one_widget_and_one_connector` both confirm single-widget/single-connector convergence.
- **Ledger bounds/redaction**: `content_hash`/`source_id` are computed over full content before the 500-char excerpt truncation (no ID drift from truncation); `audit_summary()` emits only `source_id`/`tool`/`content_hash`, verified to exclude URLs/tokens/credentials (`test_audit_summary_excludes_excerpt_arguments_and_url`) and stays under `AUDIT_BYTE_CAP` even with 1000 records (`test_audit_summary_stays_under_byte_cap_with_many_records`). `record_grounding_audit` also truncates `reason` to 200 chars.
- **Max-step termination**: `run_tool_loop` caps at exactly `max_steps` model turns even against an adapter that always requests tools (`test_run_tool_loop_stops_at_max_steps_when_model_never_yields` — exactly 3 calls for `max_steps=3`, never more).

## Unresolved Questions

- Is D1 (missing `success_criteria`/`parameters`/`expected_readouts`/`constraints` in the acronym re-scan) already tracked, or should it block Phase 4 sign-off? It is a real gap against BR-LITL-003's "never silent guessed expansion" requirement.
- Is C1 (citation coverage is membership-only, not per-claim) intended scope for this phase, or deferred to a later phase?
