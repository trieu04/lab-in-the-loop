# Phase 1 — Verify & Stabilize MVP

## Context Links

- Canonical spec: `docs/lab-in-the-loop-use-case-specification.md` (§10.1 exception table, §11, FR-019, BR-002/003, AC-UC-LITL-02-004)
- Roadmap Phase 1–2: `docs/development-roadmap.md`
- Research: `plans/260716-1253-lab-in-the-loop-use-case-implementation/research/researcher-01-260716-1253-durable-workflow-core.md` (§1, §3, §6)
- Scout: `plans/260716-1253-lab-in-the-loop-use-case-implementation/reports/scout-260716-1253-codebase-map.md` (findings #1, #3, #9)
- Decision log: `plans/260716-1253-lab-in-the-loop-use-case-implementation/reports/decision-log-260716-1253-implementation-scope.md`

## Overview

- Priority: P1 (foundation — blocks all later phases)
- Status: complete
- Effort: 3d
- Description: Establish a green local baseline for both apps and fix three correctness defects that would otherwise corrupt canvas state or violate documented safety policy, plus reconcile stale git-state docs. No new capability — stabilization only.

## Key Insights

- The orchestrator's round-advance edge `result_N → setup_{N+1}` is graph-isomorphic to a user loop trigger `result_N → setup_N`; `detect_experiment_loops` cannot tell them apart and re-detects the generated edge on the next poll, pairing a stale result with a newer setup (scout #1). `_round_of()` already exists and is the fix signal.
- `orchestrator_support.coerce()` fabricates missing required fields (`rationale=""`, `proceed=False`), directly contradicting `docs/code-standards.md` "fail visibly and leave the canvas state pending" (scout #3).
- Tests never re-derive the workflow snapshot from created notes/connectors — `FakeMCP.call_tool("scan_experiment_workflow")` returns a static fixture, hiding the loop-detection bug.
- Docs claim "no commits exist yet, all files untracked" while commit `298e234` exists (scout #9) — factual correction, owner-approved.

## Requirements

- Verify the existing MVP path and markers: FR-LITL-002, FR-LITL-006, FR-LITL-007, FR-LITL-009 (mock), FR-LITL-011, FR-LITL-012, FR-LITL-014 (current adapters), FR-LITL-021, FR-LITL-022, and AC-UC-LITL-01/02 MVP criteria.
- FR-LITL-019 (idempotency, no duplicate setup/result/loop processing), BR-LITL-001, BR-LITL-002, BR-LITL-003, BR-LITL-004, BR-LITL-005 (read/write separation, process-once, scientific caution, mock labeling, setup shape), AC-UC-LITL-02-004 (no duplicate on repeated scan).
- Structured-output policy from `docs/code-standards.md` § "Structured output" (fail visible, leave pending).
- Roadmap Phase 1 success criteria (tests/lint/mypy pass or documented) and Phase 2 no-duplicate criterion.

## Architecture / Data Flow

```
scan_experiment_workflow → loops[]  ── round-filter: keep only round(dst) <= round(src) ──► actionable triggers
model emit → coerce_or_fail() ─ valid ─► nodes.create_node (write)
                              └ invalid ─► raise → caller skips write, logs, leaves canvas pending → next poll retries
```

No new modules. Behavior of the detector narrows (forward edges excluded); write path becomes fail-closed.

## Related Code Files

- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/experiments.py` — `detect_experiment_loops`: exclude edges where `_round_of(dst_w) > _round_of(src_w)`.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/orchestrator_support.py` — replace defensive-fill `coerce` with `coerce_or_fail` raising a new `SchemaValidationError`; keep `version`/`LoopSummary`.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/orchestrator.py` — `generate_setup`, `run_on_robot`, `_decide`: on `SchemaValidationError`, skip node creation, log structured warning, propagate a "pending" outcome (no write).
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/watch.py` — tolerate the skipped-write outcome without counting a success; keep polling.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/fakes.py` — add a live-recompute mode to `FakeMCP` that rebuilds the snapshot from `self.notes`/`self.connectors` via real `canvus_mcp.experiments.scan_workflow` + `ConnectorIndex`.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/tests/test_experiments.py` — add forward-edge exclusion case.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_orchestrator.py` — malformed-output-no-write tests + live-rescan regression.
- Create: `/home/ntdm/dev/lap-in-the-loop/scripts/check-workflow-contract-parity.py` — verify canvus-mcp marker defaults, lab-agent titles/render first lines, and documented marker contract stay aligned without introducing runtime package coupling.
- Modify: `/home/ntdm/dev/lap-in-the-loop/docs/experiment-workflow.md` — document `round(dst) <= round(src)` loop rule.
- Modify: `/home/ntdm/dev/lap-in-the-loop/docs/code-standards.md` — note `coerce_or_fail` is the enforcement point for fail-visible policy.
- Modify: `/home/ntdm/dev/lap-in-the-loop/docs/development-roadmap.md`, `/home/ntdm/dev/lap-in-the-loop/README.md`, `/home/ntdm/dev/lap-in-the-loop/docs/project-changelog.md` — reconcile git-state text with commit `298e234`.

## Implementation Steps

1. `uv sync --extra dev` in `apps/canvus-mcp` and `apps/lab-agent`; run the full baseline (pytest/ruff/mypy) and record any pre-existing failures before touching code.
2. Add the round-filter to `detect_experiment_loops`: skip a candidate when `_round_of(dst_w) > _round_of(src_w)`. Same-round (`==`) and backward (`<`) edges still pass.
3. Add forward-edge exclusion test in `test_experiments.py` (a graph with both `result1→setup1` and `result1→setup2`; assert only the same-round edge is returned).
4. Introduce `SchemaValidationError` and `coerce_or_fail(model_cls, parsed)` (raise on `ValidationError`, no fill). Remove the defensive-fill branch. Keep the old name as a thin alias only if any non-orchestrator caller exists (none found).
5. Update the three `orchestrator.py` call sites to catch `SchemaValidationError`, emit `log.warning("schema_validation_failed", payload=..., stage=...)`, and return without writing a node/connector.
6. Extend `FakeMCP` with live-recompute; add the regression test: run `run_loop` two rounds, re-run `process_once` against the recomputed snapshot, assert `counts["loops"] == 0` and no duplicate `[EXP:Closed]`.
7. Add the cross-app contract-parity script and run it from local validation/CI. Fail if marker prefixes, required note first lines, or configured defaults drift.
8. Ensure a schema/provider failure returns from the current cycle without an immediate inner-loop retry. Phase 2 adds durable backoff/quarantine across polls.
9. Reconcile git-state prose in roadmap/README/changelog to reflect that `298e234` exists (do not claim uncommitted state).
10. Update `experiment-workflow.md` and `code-standards.md`; re-run the full validation matrix; add a changelog entry.

## Todo List

- [x] Baseline recorded (both apps: pytest/ruff/mypy)
- [x] Round-filter added to `detect_experiment_loops` + test
- [x] `coerce_or_fail` + `SchemaValidationError`; defensive fill removed
- [x] Three orchestrator call sites fail-closed with structured logs
- [x] `FakeMCP` live-recompute mode + loop-regression test
- [x] Malformed-output → no-write/no-inner-loop-retry tests for setup/result/decision
- [x] Cross-app marker/render/documentation parity check added
- [x] Git-state docs reconciled (roadmap/README/changelog)
- [x] Workflow + code-standards docs updated; changelog entry

## Success Criteria / Validation

- `cd apps/canvus-mcp && uv run pytest -q && uv run ruff check canvus_mcp tests && uv run mypy canvus_mcp`
- `cd apps/lab-agent && uv run pytest -q && uv run ruff check lab_agent tests && uv run mypy lab_agent`
- New tests prove: forward edge excluded; malformed output creates no note/connector or immediate retry; two-round loop does not re-trigger on rescan.
- `python scripts/check-workflow-contract-parity.py` passes and detects an intentionally mismatched marker in its own test fixture.
- No duplicate `[EXP:Setup]`/`[EXP:Result]`/`[EXP:Closed]` in the live-rescan regression.
- Docs contain no "no commits yet" claim.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Round-filter over-blocks a legitimate backward re-trigger | Low | Med | Rule keeps `<=`; only `>` excluded. Test both same-round and backward edges. |
| Fail-closed `coerce` changes behavior for a hidden caller | Low | Med | Grep confirms only 3 orchestrator call sites; alias retained if any other caller surfaces. |
| Pre-existing baseline failures block the phase | Med | Med | Step 1 records them first; document root cause rather than mask (roadmap Phase 1 allows documented failures). |

## Security Considerations

- Fail-visible policy removes fabricated `rationale`/`summary` notes that could look human-reviewed on a shared canvas — a real integrity improvement (BR-003, NFR-001).
- Structured warning logs must not echo secrets; log parsed model payloads only, never credentials.
- No new state, no new external surface; trust boundaries unchanged.

## Next Steps / Dependencies

- Blocks: Phase 2 (durable core builds on the fail-visible write path and the green baseline).
- Depends on: none.
- Docs impact: minor (workflow, code-standards, roadmap, README, changelog).
