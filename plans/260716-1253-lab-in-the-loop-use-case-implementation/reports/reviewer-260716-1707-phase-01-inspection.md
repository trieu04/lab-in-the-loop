# Phase 1 Inspection (Final Re-inspection, SEALED) — Verify & Stabilize MVP

Independent read-only final inspection after both critical fixes, a code-simplification pass (emit/write helper split into `orchestrator_support.py`), and the doc fix for the one remaining Medium finding. This report **overwrites** the stale prior version. Every finding below is verified either by direct source read or by independently re-executing the real repo (not by trusting prior reports): re-ran all `uv run pytest/ruff/mypy` for both apps and the parity script myself (all green — see Metrics), wrote two adversarial repro scripts against the actual `FakeMCP`/`ScriptedAdapter`/`process_once`/`run_loop` to empirically re-attack both previously-found critical regressions, and re-checked the exact prose of the final doc fix against current code.

**Outcome: no verified issue remains open. SEALED.**

## Previous Critical #1 (loop-swallow) — CONFIRMED STILL FIXED

`apps/lab-agent/lab_agent/watch.py:77-89`: the loop-processing branch only calls `processed_loops.add(cid)` (line 88) and counts the loop (line 89) *after* checking `summary.stopped_reason == "schema_validation_failed"` (line 82) and `continue`-ing without marking/counting on that outcome. Unchanged from the previously-verified fix; still covered by the parametrized regression `test_process_once_loop_retries_after_schema_validation_failure` (`apps/lab-agent/tests/test_orchestrator.py:170-222`, all three stages).

## Previous Critical #2 (round-advance duplicate-setup) — CONFIRMED FIXED

The prior critical — a result-stage schema failure during round-advance stranded an already-written, unpaired `[EXP:Setup]` note, which `watch.py`'s (correct) retry-from-scratch behavior then re-created every poll, forever — is fixed by validating **both** the next round's setup and result before writing **either**.

`apps/lab-agent/lab_agent/orchestrator.py:142-163` (`run_loop`, round-advance branch):
```python
next_setup = await ground_and_emit_setup(...)     # emit only, no write
if next_setup is None:
    return _fail_closed(summary)
next_result = await emit_result(adapter, render.render_setup(next_setup))  # emit only, no write
if next_result is None:
    return _fail_closed(summary)

next_setup_id = await write_setup_node(...)        # write #1 — only now, both validated
await nodes.connect(mcp, canvas_id, result_id, next_setup_id)
next_result_id = await write_result_node(...)      # write #2
```
`ground_and_emit_setup` and `emit_result` (`orchestrator_support.py:92-122`) are pure emit stages — they call the model and validate via `_emit_validated`/`coerce_or_fail` but perform no canvas write; only `write_setup_node`/`write_result_node` (`orchestrator_support.py:137-171`) touch the canvas, and both are now unreachable unless both emits already succeeded.

**Adversarially reproduced (not just trusting the new test):** ran `process_once` for 5 consecutive polls with `LoopDecision`/`ExperimentSetup` scripted to always succeed and `ExperimentResult` scripted to always fail schema validation (the exact scenario that produced unbounded duplicate notes in the prior round):
```
poll 1: counts={'loops': 0} total_notes=3 connectors=0
poll 2: counts={'loops': 0} total_notes=3 connectors=0
poll 3: counts={'loops': 0} total_notes=3 connectors=0
poll 4: counts={'loops': 0} total_notes=3 connectors=0
poll 5: counts={'loops': 0} total_notes=3 connectors=0
```
`total_notes` stays at the 3 seed notes throughout — zero stray setup notes, matching the prior report's now-obsolete repro (`['note1']` → `['note1','note3']` → `['note1','note3','note5']`, unbounded).

Also verified the complementary "eventually succeeds" path does not double-write: scripted `ExperimentResult` to fail twice then succeed, with `LoopDecision` proceeding until the `loop_max_rounds` backstop (25 default). Once the result stage started succeeding, the loop ran 24 further rounds to backstop within that single `run_loop` call, producing exactly 24 setup notes + 24 result notes + 49 connectors (24 round-advance edges + 24 robot-run edges + 1 closed edge = 49) — one write per round, no duplicates from the two earlier failed attempts.

This is now covered by a dedicated test case, `round_advance_result_stage` (`apps/lab-agent/tests/test_orchestrator.py:182-190`, parametrized alongside `decision_stage`/`round_advance_setup_stage`), which asserts `mcp.notes`/`mcp.connectors` stay empty across two polls and the adapter is invoked again on poll 2 (`test_orchestrator.py:193-222`).

## Critical Issues (New)

None found.

## High Priority

None. No N+1s (bounded per-poll iteration over scan results), no missing auth checks (no externally-facing surface changed), mypy clean on both apps (13 + 20 source files, independently re-verified).

## Medium Priority

### 1. Stale `_decide` reference in code-standards.md — FIXED, re-verified against current code

`docs/code-standards.md:82` previously named a removed `_decide` function and mis-described `orchestrator.py:generate_setup`/`run_on_robot` as the `SchemaValidationError` catch sites. It now reads:

> "The enforcement path is `lab_agent/orchestrator_support.py:coerce_or_fail` plus `_emit_validated`: `coerce_or_fail` validates parsed model output against its Pydantic schema and raises `SchemaValidationError` instead of coercing or defaulting missing fields; `_emit_validated` catches that error, logs a `schema_validation_failed` warning (stage, model name, raw payload) via `structlog`, and returns `None`. The setup, result, and decision callers propagate that fail-closed outcome without writing a note or connector or retrying within the same cycle."

Checked every clause against current source:
- `coerce_or_fail` — defined `orchestrator_support.py:52`, raises `SchemaValidationError` on `ValidationError`, no field-filling. Matches.
- `_emit_validated` — defined `orchestrator_support.py:77-89`, `try/except SchemaValidationError`, logs `log.warning("schema_validation_failed", stage=stage, model=exc.model_name, payload=exc.payload)`, returns `None`. Matches exactly, including the log fields named in the doc.
- "setup, result, and decision callers" — `_emit_validated` is called from `ground_and_emit_setup` (stage `"setup"`), `emit_result` (stage `"result"`), `emit_decision` (stage `"decision"`) — `orchestrator_support.py:113, 122, 133`. Matches.
- "propagate that fail-closed outcome without writing... or retrying within the same cycle" — `generate_setup`/`run_on_robot`/`run_loop` all check `is None` immediately after each emit call and return/`_fail_closed` without any write or in-cycle retry (`orchestrator.py:58-59, 82-83, 119-120, 146-147, 149-150`). Matches.

Repo-wide grep for `_decide` (docs + both apps + README): zero matches. No stale symbol references remain anywhere in the repository.

This was the sole open item from the prior pass of this inspection; it is now closed with no residual discrepancy.

### 2. `FakeMCP` live mode uses two disjoint connector-id schemes (carried forward, unaddressed, still non-blocking)

`apps/lab-agent/tests/fakes.py:81-83` (`create_connector` response) returns `f"conn{self._counter}"` (a counter shared with notes); `_live_scan()` (`fakes.py:104-107`) synthesizes ids as `f"conn{i}"` where `i` is list index — a different scheme for the same conceptual connector. Unchanged from the prior two reviews; nothing in production or test code compares the two ids, so it remains a latent test-fidelity wrinkle, not a live bug.

### 3. Parity checker's regex extraction is a last-match-wins, unguarded scan (carried forward, unaddressed, still non-blocking)

`scripts/check-workflow-contract-parity.py:44-55`, unchanged from prior reviews. Still no current collision (`grep` confirms one match per marker name in each source file); still a latent fragility if a future edit adds a bare top-level assignment coincidentally named `setup`/`result`/etc. elsewhere in `experiments.py`.

## Low Priority

- **File sizes:** all changed *source* modules are now under 200 lines — `orchestrator.py` 166 (down from 210 in the prior round, a real simplification win), `orchestrator_support.py` 198, `watch.py` 114, `experiments.py` 187. Test/tooling files exceed the guideline: `test_orchestrator.py` 266 lines (up from 251), `check-workflow-contract-parity.py` 241 lines (up from 234). `docs/code-standards.md` states no explicit line-limit for test files; not flagged as blocking, consistent with the prior review's treatment.
- **Log payload volume:** unchanged — `log.warning("schema_validation_failed", stage=stage, model=exc.model_name, payload=exc.payload)` (`orchestrator_support.py:88`, the relocated catch site) logs raw parsed model output verbatim. Explicitly sanctioned by the phase's Security Considerations; observational only. No secrets/PII path — payload is the LLM's own malformed structured output, not user credentials.
- **`sys.path` mutation:** unchanged — `fakes.py:29` mutates global interpreter state at test runtime; still benign today, test-only.

## Verified Correct (adversarial checks requested by the task)

- **Watcher retry gate (Critical #1, the fix under test):** re-confirmed via code read that `run_loop` failures no longer permanently swallow a loop; `processed_loops` is only updated on non-`schema_validation_failed` outcomes (`watch.py:77-89`).
- **Round-advance both-before-either-write gate (Critical #2, the fix under test):** re-confirmed via code read + two independent adversarial executions (always-fails and fails-then-succeeds) that no setup note is ever written without its paired result also validating first, and no duplicate accumulates across repeated polls.
- **Two-poll retry, no same-cycle retry — all three stages:** the parametrized regression test (`decision_stage`, `round_advance_setup_stage`, `round_advance_result_stage`) plus my own direct executions confirm the adapter is called once per poll, never twice within one poll, and the count doubles after a second poll.
- **Round/graph semantics:** forward edges excluded (`experiments.py:112-117`); same-round and strictly-backward edges both still detected as actionable loops (`test_experiments.py:58-73` forward exclusion, `test_experiments.py:77-86` strict-backward).
- **`coerce`/`coerce_or_fail` call-site completeness:** grepped the whole repo — zero remaining references to the removed `coerce` name anywhere; exactly one `coerce_or_fail` definition (`orchestrator_support.py:52`) and one call site (`orchestrator_support.py:83`, inside `_emit_validated`, itself called from all three emit stages: `ground_and_emit_setup`, `emit_result`, `emit_decision`). No write happens before validation in any of the three.
- **Public `generate_setup`/`run_on_robot` compatibility after the helper move:** both keep their original public signatures and now return `tuple[str, ExperimentSetup | None]` / `tuple[str, ExperimentResult | None]` (an explicit, backward-compatible widening — the prior type hint was already inaccurate since `coerce`'s defensive-fill made failure structurally impossible, so no real caller-facing contract narrowed). Both existing callers (`watch.py:52`, `watch.py:67`) already guard with `if setup is None:`/`if result is None:` before use. No other production callers found (grepped `generate_setup(`/`run_on_robot(` repo-wide).
- **Import/circular/module-boundary risk after the relocation:** grepped both packages — `apps/lab-agent/lab_agent/` production code never imports `canvus_mcp`; `apps/canvus-mcp/canvus_mcp/` never imports `lab_agent`. The one `canvus_mcp` string hit inside `lab_agent/config.py` is a docstring/comment reference, not an import (`config.py:5,27`). The only actual cross-app import is `tests/fakes.py:30-31` (test-only, path-based `sys.path` insert). `orchestrator.py` → `orchestrator_support.py` is a clean one-directional import (`orchestrator_support.py` does not import `orchestrator.py`); no cycle.
- **`FakeMCP` live mode fidelity:** confirmed `_live_scan()` (`fakes.py:93-110`) rebuilds the snapshot via the real `canvus_mcp.experiments.scan_workflow`/`ConnectorIndex`, exercised by `test_live_rescan_after_loop_round_has_no_duplicate_or_actionable_loop` (`test_orchestrator.py:225-266`) — independently re-ran, passes.
- **Parity checker after helper relocation:** `check-workflow-contract-parity.py:220-223` now reads both `orchestrator.py` and `orchestrator_support.py` for the note-body first-line fragments (`"Idea: {idea_id}"`, `"Setup: {setup_id}"`, `"Round: {round_index}"`), which now live in `orchestrator_support.py`'s `write_setup_node`/`write_result_node`. Re-ran from repo root: self-test OK, parity OK.
- **Safe logs:** `log.warning("schema_validation_failed", stage=stage, model=exc.model_name, payload=exc.payload)` logs the model's own parsed (malformed) structured output, not credentials or user PII; sanctioned by the phase's Security Considerations, unchanged in the simplification pass.
- **Docs test counts / current git history:** `docs/project-changelog.md:17-18` and `docs/development-roadmap.md:15,65` now both read `canvus-mcp` 19/19 and `lab-agent` 19/19 — matches my independent re-run exactly. The prior review's Medium #2 (stale 18/18 / 16/16 counts) is resolved. `git log` shows 2 commits (`298e234`, `3d79c68`); `README.md` and `docs/development-roadmap.md` no longer claim "no commits yet" / "not committed" — both now describe committed history accurately.
- **Test/lint/type baseline:** independently re-ran fresh (not reused from `temper-results.json`): canvus-mcp 19/19 pytest, ruff clean, mypy clean (13 files); lab-agent 19/19 pytest, ruff clean, mypy clean (20 files); parity script exit 0, self-test OK; `git diff --check` clean (no whitespace/line-ending issues). All match `temper-results.json`'s recorded numbers exactly.

## Recommended Actions

None outstanding. All three prior items are resolved or explicitly accepted as non-blocking:

1. **DONE** — `docs/code-standards.md:82` corrected to name `orchestrator_support.py:coerce_or_fail`/`_emit_validated` as the enforcement/catch path, with setup/result/decision callers propagating the fail-closed outcome. Re-verified clause-by-clause against current code (see Medium #1) — no residual discrepancy.
2. (Optional, non-blocking, carried forward, no owner decision requested) Unify or document the two connector-id schemes in `FakeMCP` live mode.
3. (Optional, non-blocking, carried forward, no owner decision requested) Harden the parity checker's field-extraction regex against accidental bare-name collisions elsewhere in `experiments.py`.

## Metrics

- Files changed: 12 modified + 1 new script (`scripts/check-workflow-contract-parity.py`), independently re-diffed against `HEAD` (only `docs/code-standards.md` changed since the previous pass of this inspection; all other files unchanged).
- Test counts (independently re-run, fresh, prior to this final doc-only change — no source/test files touched since): canvus-mcp 19/19 pass (0.05s); lab-agent 19/19 pass (2.41s).
- Lint: `ruff check` clean on both apps (unchanged this round).
- Types: `mypy` clean on both apps (13 + 20 source files, unchanged this round).
- Parity script: re-run after the doc fix — exit 0, self-test OK, parity OK.
- `git diff --check`: re-run after the doc fix — clean.
- Source module sizes (all under 200): `orchestrator.py` 166, `orchestrator_support.py` 198, `watch.py` 114, `experiments.py` 187.

## Unresolved Questions

None. Every finding from this and the prior two inspection passes is either confirmed fixed (both criticals, the stale-docs Medium) or explicitly accepted as non-blocking/carried-forward (FakeMCP dual id scheme, parity-checker regex fragility) with no scope decision pending.
