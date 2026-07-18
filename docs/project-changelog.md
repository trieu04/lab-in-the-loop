# Project Changelog

## 2026-07-18 — Phase 4: grounded setup evidence and ambiguity gates

### Added

- Per-run bounded evidence ledger for setup grounding: successful read-tool results get deterministic source ids, content hashes, and bounded in-memory excerpts; durable audit records only ids/tool/hash plus decision/reason.
- Additive `ExperimentSetup` fields: `hypothesis`, `success_criteria`, `constraints`, `confidence`, `citations`, `evidence_status`, and `ambiguity_flags`, all defaulted for legacy parsing.
- Explicit grounding outcomes before setup writes: executable, Needs Input for insufficient evidence/ambiguity, invalid citation, or schema failure.
- Dictionary-backed acronym boundary scan over original idea text, emitted setup fields, and every bounded retrieved evidence excerpt; unknown or colliding acronym-like terms are not guessed.

### Changed

- The model-facing tool loop now receives only read tools and wraps successful read results as `untrusted_data`; write tools stay orchestrator-only.
- Setup writes now require `evidence_status='sufficient'` and citations that resolve against the current run's ledger. Invalid/fabricated citations write nothing and remain retryable/backoff/quarantine-eligible.
- Insufficient-evidence and unresolved-ambiguity cases create one `[EXP:Needs Input]` Browser artifact, deduplicated by predecessor plus reason hash.
- Grounding audit uses the same 4096-byte payload cap as the store and trims newest evidence rows to fit; raw excerpts, tool arguments, credentials, and capability URLs are not persisted.

### Verified

- Final Phase 4 temper/review sealed on 2026-07-18: `lab-agent` 314/314 tests passed, focused `canvus-mcp` Needs Input marker suite 8/8 passed, `ruff`/`mypy` clean, workflow contract parity clean, and reviewer score 9.6/10 SEALED.

### Not claimed

- No real wiki, knowledge-graph, vector DB, Flywheel, in-silico, robot, or wet-lab integration is claimed. Future retrieval sources are adapters or external gates.
- No autonomous wet-lab execution or approval workflow transition is implemented.
- Acronym dictionary completeness remains an operational/domain-owner responsibility before live scientific use.

## 2026-07-18 — Phase 3: generated artifacts use Browser widgets

### Added

- Generated Setup/Result/Closed artifacts and generated Needs Input status/prompt artifacts are now documented as capability-protected HTML Browser widgets backed by canonical, versioned `ArtifactStore` records in the shared SQLite state DB. Legacy generated Notes remain readable during migration.
- The artifact service contract is documented: private bind by default, operator-configured public base URL, `/healthz`, same-origin CSS/JS under CSP, token hash storage, revocation/rotation, cross-canvas scope checks, and in-place Browser repair through `update_browser`.
- Mirror-first legacy migration is documented: the migration script defaults to read-only dry-run, `--apply` creates Browser mirrors/connectors, and original generated Notes/connectors are not deleted, archived, or edited.

### Changed

- Replaced stale documentation claims that runtime-generated Setup/Result/Closed outputs are Notes. User-authored `{idea: ...}` and human-authored approval/review/input responses remain Notes. Historical passages about legacy canvases and earlier phases remain marked as legacy/historical.
- Roadmap status now lists the generated artifact Browser service as delivered for local/source gates, with operational deployment gates still pending: live Canvus public-base reachability and production HTTPS/private-ingress/TLS verification.

### Verified

- Final Phase 3 temper/review sealed on 2026-07-18: 293/293 tests passed (37 `canvus-mcp`, 256 `lab-agent`), `ruff`/`mypy` clean for both apps, workflow contract parity clean, and reviewer score 9.6/10 SEALED.

### Not claimed

- No live production deployment is claimed.
- No external Canvus reachability or TLS termination has been verified by these documentation edits.
- Generated Needs Input Browser artifacts are request/status infrastructure only; no approval workflow transitions, approve/reject UI, or wet-lab gate enforcement are claimed.
- Capability URLs are bearer secrets and must not be logged, audited, printed, or pasted into model context.

## 2026-07-17 — Phase 2 critical-defect fixes: closed-note duplicate window and MCP error-payload phantom success

Fixes two critical defects raised by the Phase 2 final review (`reviewer-260717-phase-02-inspection.md`): both violated the plan's acceptance requirement that every canvas note/connector side effect reconcile across crashes without duplicates.

### Fixed

- **Closed-note crash-recovery duplicate window.** `[EXP:Closed]` notes had no defining connector at creation time (the `result -> closed` edge is drawn *after* the note), so `canvas_probe.probe_note_by_tag`'s only recovery path — `check_widget_connections` on a known source widget — had nothing to probe: a crash between "closed note created" and "connector drawn" could not be recovered and would duplicate the note on retry. Fixed by classifying closed notes as their own `closeds` bucket in `scan_experiment_workflow` (`apps/canvus-mcp/canvus_mcp/experiments.py`: `ExpMarkers.closed`, `_is_closed` checked before `_is_robot` in classification order since closed notes have no `widget_type` restriction), tagging closed-note titles the same way setup/result notes already are (`apps/lab-agent/lab_agent/durable_writes.py:write_closed_node_durable` now tags-then-probes via the `closeds` bucket instead of the removed `probe_closed_note`), and configuring the marker end to end (`apps/canvus-mcp/canvus_mcp/config.py:mcp_exp_closed_marker`, `apps/canvus-mcp/canvus_mcp/tools/experiments.py`). Recovery for all three note kinds (setup/result/closed) is now connector-independent.
- **MCP error-payload phantom success.** `MCPClient.call_tool` (`apps/lab-agent/lab_agent/mcp_client.py`) does not raise on a tool-level failure — a failed `create_note`/`create_connector` still returns normally, as a `{"error": ...}` JSON string. `nodes.create_node`/`nodes.connect` (`apps/lab-agent/lab_agent/nodes.py`) previously read `res.get("id", "")` from that response, silently yielding an empty id that `recovery.reconcile_or_execute` would mark permanently `RECONCILED` — a phantom success that could never be retried. Fixed by making both write helpers fail closed via a new `_parse_checked`/`_require_id`/`_call_checked` chain: malformed JSON, a non-object response, an explicit `error` key, or a missing/empty `id` all raise `nodes.MCPToolError` instead of returning `""`. The existing exception handling in `recovery.reconcile_or_execute` (already present, unmodified) then marks the intent `failed` and re-raises, which `watch._process_trigger`'s catch-all turns into a durably retryable/backoff/quarantine-eligible `workflow_attempts` failure — no new special-case code was needed anywhere else in the stack. Read paths (`nodes.read_note_text`) are unchanged and stay lenient (an unreadable note is "nothing to read", not a failure).

### Added

- `apps/lab-agent/tests/fakes.py:FakeMCP.fail_next_as_error_payload`: queues a non-raising `{"error": ...}` JSON response, distinct from the existing `fail_next` (which raises `RuntimeError`, standing in for a transport-level crash) — simulates the real MCP server's actual tool-level-failure response shape.
- `apps/lab-agent/tests/test_nodes.py` (new, 19 tests): unit coverage of `create_node`/`connect`/`read_note_text` against both `FakeMCP` and a local `_RawMCP` stub — success paths, the empty-endpoint `connect` short-circuit, error-payload failures, transport-exception failures, and malformed-JSON/non-object/missing-id/empty-id failures, plus proof `read_note_text` stays lenient on all failure shapes.
- `apps/lab-agent/tests/test_durable_recovery_integration.py`: end-to-end regressions for the closed-note fix (`test_closed_note_crash_before_connector_recovers_without_duplicate_across_restart` — a pre-landed closed note recovered via the `closeds` bucket across two simulated restarts, no duplicate note/connector, attempt completed exactly once) and the error-payload fix (`test_process_once_retries_after_setup_note_error_payload_without_duplicate`, `test_process_once_retries_after_setup_connector_error_payload_without_duplicate` — an error payload on `create_note`/`create_connector` leaves the intent and attempt failed/retryable, with no phantom effect; the next due retry succeeds exactly once).
- `apps/canvus-mcp/tests/test_experiments.py`: two new regressions proving a closed note is enumerable via the `closeds` bucket without any connector, and is not misclassified as a robot.
- `apps/lab-agent/tests/test_canvas_probe.py`: replaced the obsolete `probe_closed_note` tests with `probe_note_by_tag` coverage of the `closeds` bucket. The broader crash/restart scenarios now live in the split `test_durable_recovery_integration.py` and `test_durable_operations_integration.py` modules.

### Verified

- `apps/lab-agent`: 136/136 tests pass (up from 115), `ruff check lab_agent tests` clean, `mypy lab_agent` clean.
- `apps/canvus-mcp`: 21/21 tests pass (up from 19), `ruff check canvus_mcp tests` clean, `mypy canvus_mcp` clean.
- `python3 scripts/check-workflow-contract-parity.py` passes (self-test OK; the `closed` marker is now checked alongside `robot`/`setup`/`result`).

### Documentation

- `docs/system-architecture.md`: updated the `canvas_probe.py` module-table entry and the side-effect-intent-recovery bullet to describe connector-independent recovery for setup/result/closed notes alike; removed the "known residual limitation" paragraph and the matching "Current limitations" bullet describing the closed-note duplicate window, since it is now fixed.
- `docs/experiment-workflow.md`: replaced stale session-local `processed_loops` wording with durable `workflow_attempts`/side-effect-intent recovery, documented the `closeds` bucket for terminal notes, and added fail-closed `MCPToolError` write behavior to the failure table.
- `docs/code-standards.md`: documented that `create_note`/`create_connector` write helpers fail closed on malformed/error/missing-id responses and that schema-validation failures are retried through the durable attempt ledger.
- `docs/setup-and-operations.md`: clarified that durable-harness environment variables have safe defaults and can be omitted from `.env` unless overriding behavior.
- `docs/lab-in-the-loop-use-case-specification.md`: refreshed status notation, FR/BR/NFR rows, acceptance criteria, and coverage counts to current results (`canvus-mcp` 21/21, `lab-agent` 136/136), while keeping live Canvus E2E demo status pending.

## 2026-07-16 — Phase 2: Establish durable harness core

### Added

- `apps/lab-agent/lab_agent/state_store.py` and `lab_agent/state/*` (`connection.py`, `attempts.py`, `attempts_retry.py`, `leases.py`, `intents.py`, `audit.py`, `edges.py`, `models.py`): a local SQLite (WAL-mode) durable ledger, opened/migrated via versioned checksum-tracked SQL migrations (`lab_agent/migrations/001_durable_harness.sql`, applied by `connection.py`'s migration runner). Provides: `workflow_attempts` (`pending → running → completed | failed | quarantined`, full-jitter exponential backoff, quarantine after `max_attempts`, lifecycle split across `attempts.py`/`attempts_retry.py` for the line budget), `canvas_leases` (single-writer-per-canvas, keyed by a per-process `runtime_instance_id`), `side_effect_intents` (outbox rows for crash-safe canvas writes), `orchestrator_edges` (recorded connector ids by kind/round), and a hash-chained `audit_events` log (payloads capped at 4096 bytes, ids/hashes/reasons only — never note text, model payloads, or credentials).
- `lab_agent/recovery.py`: `idempotency_key`/`input_hash` and the generic `reconcile_or_execute` outbox algorithm (persist intent → live-probe for a prior run's already-completed effect → execute only if not found → mark executed → reconcile).
- `lab_agent/canvas_probe.py`: production `live_probe` callables (`probe_note_by_tag`, `probe_connector`, `probe_closed_note`) built on existing canvus-mcp read tools (`scan_experiment_workflow`, `check_widget_connections`) — no dependency on test-only internals. Fail closed on any MCP/parse error. Setup/result notes carry a short discriminator tag (`tagged_title`) so a crash-recovered note can be found by idempotency key, not just by content.
- `lab_agent/durable_writes.py`: `write_setup_node_durable`/`write_result_node_durable`/`write_closed_node_durable` — wires the outbox pattern around the real `nodes.create_node`/connector calls, replacing direct calls from `orchestrator_support.py`.
- `lab_agent/intent_audit.py`: `reconcile_with_audit` (adds `intent_reconciled`/`intent_failed` audit events) and `connect_durable` (durable connector creation, records `orchestrator_edges`).
- `lab_agent/runtime.py`: process-wide `RuntimeContext` (durable `StateStore` + a fresh UUID4 `runtime_instance_id` per process, never derived from config). `build_runtime_context` fails closed (`RuntimeStartupError`) on a missing/un-migratable/corrupt ledger file or a failed integrity/audit-chain check, before any canvas work. `release_lease_with_audit` releases the canvas lease and appends `canvas_lease_released` on every clean CLI shutdown path (`once` and `watch`, including Ctrl-C).
- `lab_agent/admin.py` and four new CLI subcommands (`lab-agent integrity`, `list-quarantined`, `reset`, `backup`): operator-facing integrity verification, quarantined-attempt listing/reset, and consistent hot backup (safe under WAL) — each appends its own operator audit event. No unsafe restore-overwrite command; a restore drill is a documented procedure (open the backup directly and verify integrity/audit chain), not a destructive CLI action.
- `lab_agent/config.py`: six new `Settings` fields (`state_db_path`, `canvas_lease_ttl_seconds`, `attempt_lease_ttl_seconds`, `retry_base_seconds`, `retry_max_seconds`, `max_attempts`), all with safe defaults, no new dependencies.
- `apps/lab-agent/tests/fakes.py`: `FakeMCP.fail_next` (queues deterministic tool-call failures, standing in for a mid-flight crash) and a `_check_widget_connections` handler that faithfully mirrors canvus-mcp's real `check_widget_connections` tool response shape (reusing the real `canvus_mcp.ragcluster` functions via the existing path-based import), so crash-recovery probes are tested against production-accurate responses.
- New test modules: `tests/test_canvas_probe.py` (15 tests), `tests/test_admin.py` (7 tests), `tests/test_runtime.py` (7 tests), `tests/test_durable_harness_integration.py` (9 tests — real on-disk SQLite + `FakeMCP`, no mocks of the harness itself: crash-before-write retry with no duplicate, crash-after-write-before-reconcile recovery via live probe for both notes and connectors, a second runtime blocked by a live canvas lease, lease expiry allowing a new owner, end-to-end quarantine → operator reset → successful retry, backup+restore preserving completed-attempt dedup, and audit-chain verification after a full cycle). No sleeps anywhere — determinism comes from injected `Clock`/`RandomSource` callables and `FakeMCP.fail_next`.

### Changed

- `lab_agent/watch.py`, `lab_agent/orchestrator.py`, `lab_agent/orchestrator_support.py`: hard-switched from the in-memory `processed_loops` set to the durable `workflow_attempts` ledger as the sole source of truth for "has this trigger already been handled" — `processed_loops` no longer exists as a competing/fallback dedup mechanism. `process_once`/`watch` now acquire-or-renew the canvas lease once per cycle before any work; a live foreign owner causes a safe, zero-write skip (`canvas_lease_denied` audit event), not a crash.
- `apps/lab-agent/tests/fakes.py`, `tests/test_orchestrator.py`: updated to construct/pass the durable `StateStore` + `runtime_instance_id` through `process_once`/`watch` call sites instead of the retired in-memory set.

### Verified

- `apps/lab-agent`: 114/114 tests pass (up from 19 at the end of Phase 1; +95 new/updated across durable-harness unit and integration coverage), `ruff check lab_agent tests` clean, `mypy lab_agent` clean.
- `apps/lab-agent/tests/test_durable_harness_integration.py` specifically proves: no duplicate note/connector across a simulated crash at each of the "before write", "after write, before reconcile", and "after reconcile" boundaries; a second concurrently-started runtime is denied the canvas lease rather than racing; an expired lease is reclaimable by a new owner; a quarantined attempt is visible via `list-quarantined`, resettable via `reset`, and successfully retried afterward; and a backup taken mid-run, restored to a fresh `StateStore`, prevents the restored process from re-doing already-completed work.
- Manual CLI exercise of `integrity`, `list-quarantined`, and `backup` against a temporary database, confirming stdout/exit-code contracts match `lab_agent/admin.py`.

### Documentation

- `docs/system-architecture.md`: added a "Durable harness core (Phase 2)" section (runtime context, canvas lease, durable attempts, side-effect intent recovery, audit log, backup — plus the closed-note probe's documented residual crash-duplicate-window limitation) and a "Local-disk, single-host scope, and the Postgres/multi-host trigger" section. Updated the `lab-agent` module table, component-map diagram, "State and idempotency", "Current limitations", and the "Target harness boundary (proposed)" durable-state-machine bullet to stop describing the now-implemented local durable ledger as a future item.
- `docs/setup-and-operations.md`: documented the six durable-harness env vars (with defaults), the fail-closed startup contract, the four operator commands (`integrity`/`list-quarantined`/`reset`/`backup`), a restore-drill procedure, and new troubleshooting entries (`STARTUP FAILED`, quarantined triggers, a second instance appearing to do nothing due to lease denial).
- `docs/development-roadmap.md`: marked Phase 3 "Partially complete" — split its durable-idempotency goal (now Complete, with the chosen approach and success criteria evidenced by the new integration tests) from its still-Future harness-contracts goal (schema/prompt/provider-policy ownership, unchanged). Added a durable-harness-core row to the status snapshot and cross-referenced the Postgres/multi-host migration trigger from Phase 7.
- `README.md`: corrected the "Current implementation vs. target harness" bullets that described loop idempotency as in-memory/session-local and durable workflow state as nonexistent — both are now implemented for the local-disk, single-host scope; the remaining future-item bullets (retrieval/governance/Flywheel/in-silico/multi-user observability) are unchanged.

## 2026-07-16 — Phase 1: Verify and stabilize the MVP

### Fixed

- `detect_experiment_loops` (`canvus_mcp/experiments.py`) no longer re-detects the orchestrator's own round-advance edge (`result_N -> setup_{N+1}`) as an actionable loop: it is graph-isomorphic to a real user-drawn `result_N -> setup_N` loop trigger, but only a same-round or backward edge (`round(setup) <= round(result)`) is a genuine "iterate this experiment" signal. See [experiment workflow](experiment-workflow.md) → "Result to setup loop".
- The orchestrator (`lab_agent/orchestrator.py`) no longer fabricates missing required fields on malformed structured model output. `generate_setup`, `run_on_robot`, and the loop's decision step now validate via `lab_agent/orchestrator_support.py:coerce_or_fail`, which raises `SchemaValidationError` instead of coercing; every call site catches it, logs a `schema_validation_failed` warning, and writes no note/connector for that step — the canvas is left pending for the next poll rather than showing a note with silently invented content. See [code standards](code-standards.md) → "Structured output".

### Added

- `tests/fakes.py:FakeMCP` gained a `live=True` recompute mode that derives `scan_experiment_workflow` snapshots from seeded notes/connectors using canvus-mcp's real `ExpMarkers`/`scan_workflow` (a test-only, path-based import — no runtime dependency between the apps), plus a regression test proving a two-round rescan creates no duplicate setup/result/closed nodes and returns zero actionable loops on the second poll.
- `scripts/check-workflow-contract-parity.py`: a dependency-free script (no `import canvus_mcp` / `import lab_agent`) that statically checks canvus-mcp's `ExpMarkers`/`Settings` marker defaults, lab-agent's `nodes.py` title constants and `orchestrator.py`/`orchestrator_support.py` note-body first lines (the write helpers moved to `orchestrator_support.py` in this same pass), and `docs/experiment-workflow.md` all stay aligned. Runs a self-test against an in-memory fixture proving it catches an injected mismatch before checking the real repo.

### Verified

- `apps/canvus-mcp`: 19/19 tests pass (17 baseline + 2 new round-filter/backward-edge regressions), `ruff check canvus_mcp tests` clean, `mypy canvus_mcp` clean.
- `apps/lab-agent`: 19/19 tests pass (11 baseline + 8 new fail-closed/live-recompute/loop-retry regressions), `ruff check lab_agent tests` clean, `mypy lab_agent` clean.
- `python scripts/check-workflow-contract-parity.py` passes (self-test catches an injected fixture mismatch; real repo markers/docs are aligned).

### Documentation

- Reconciled stale "no commits yet" / "not committed" wording in `README.md` and `docs/development-roadmap.md` (Phase 0) to the current state — the repository has committed git history. `docs/development-roadmap.md` Phase 0 and Phase 1 ("Local verification") are now marked Complete with the verification results above.
- `docs/experiment-workflow.md`: documented the `round(setup) <= round(result)` loop-detection rule and cross-referenced it from the idempotency section.
- `docs/code-standards.md`: documented `coerce_or_fail`/`SchemaValidationError` as the enforcement point for the fail-visible structured-output policy.
- `docs/system-architecture.md`: fixed two references to the removed `orchestrator_support.coerce` function (renamed to `coerce_or_fail` with fail-closed, not-fabricating semantics) in the module table and the target-harness boundary section.
- `docs/lab-in-the-loop-use-case-specification.md`: synced the §17 coverage matrix's Phase 0/Phase 1 rows and the MVP 1 acceptance row to Complete, matching `docs/development-roadmap.md`.

## 2026-07-16 — Documentation-only: canonical Lab-in-the-Loop use case specification

Added `docs/lab-in-the-loop-use-case-specification.md`: a canonical, detailed use case
specification derived from the meeting-vision document (`docs/notes/use-case-lab-in-the-loop.md`,
kept unmodified as source), cross-checked against `docs/system-architecture.md`,
`docs/development-roadmap.md`, `docs/experiment-workflow.md`, `docs/code-standards.md`, and
`apps/lab-agent` source (`models/experiment.py`, `models/states.py`, `orchestrator.py`,
`tool_bridge.py`). Covers actor catalog, functional requirements (`FR-LITL-###`), the three
stable use cases (`UC-LITL-01/02/03`), business rules (`BR-LITL-###`), data contracts
(`ExperimentSetup`/`ExperimentResult`/`LoopDecision` vs. target-schema gaps), NFRs
(`NFR-LITL-###`), the two-layer workflow state model (canvas markers vs. target
`DecisionState` lifecycle), traceability and release-coverage matrices. No code, tests, or
config changed. Updated `README.md` documentation map (labels the vision doc as
meeting-vision/source, lists the new spec) and `docs/code-standards.md` docs-to-keep-in-sync
list accordingly. Translated the canonical specification fully into English, moved the original meeting vision under `docs/notes/` (excluded from normalization), updated all public documentation links, and renamed `apps/canvus-mcp/canvus-sdk/MIGRATION-NOTES.md` to the conventional kebab-case `migration-notes.md`. Conventional `README.md` filenames remain unchanged.

## 2026-07-16 — Documentation update: architecture recovery reconciliation

### Context

A separate, read-only Claude Code session (id `958a41ff-c7bc-4992-8559-5bcf9e229f6d`, project `rag-canvus`, active ~10:26–10:34 Asia/Saigon) was recovered from transcript for context. **That source session made no code or file changes and ran no Bash commands, tests, lint, or builds** — it performed reconnaissance (brainstorm flow, read-only `scan-codebase`, one read-only `Explore` agent, and a read of the source project's `docs/UC-Lab-in-the-Loop.md`) and produced an architectural conclusion only. This changelog entry documents *this repository's* documentation update made in response to that recovered conclusion; it is a documentation-only change, no source/tests/config were touched.

### Documented (proposed, pending owner confirmation — not an approved architecture)

- Added a harness-first architecture direction across `README.md`, `docs/system-architecture.md`, `docs/development-roadmap.md`, and `docs/code-standards.md`: Lab-in-the-Loop should evolve as an **independent, provider-neutral harness/orchestrator**, with the Claude Code skill/MCP registration as an **optional** developer/operator/demo interface only, and shared workflow logic/policy/grounding/schemas centralized in the harness.
- Explicitly flagged this as the recovered session's **final recommendation, not an owner-ratified decision** — its `AskUserQuestion` architecture-choice prompt was interrupted and never answered. No document in this repo marks the harness-first direction as implemented or approved.
- Distinguished the current MVP (OpenAI/Claude adapter factory, OpenAI-compatible `base_url` for Ollama/vLLM-style endpoints, mock robot execution, in-memory `processed_loops`, no durable state/retrieval/governance/Flywheel/in-silico/multi-user observability) from the proposed target harness responsibilities in `docs/system-architecture.md` ("Target harness boundary (proposed)") and strengthened `docs/development-roadmap.md` phases 3–7 accordingly (harness contracts, token/resource governance and model routing, async multimodal ingestion, in-silico + scientist review + lab-lead approval + Flywheel/lab integration, multi-user deployment and observability).

### Fixed (drift between docs and code, verified by reading source)

- `docs/system-architecture.md`: added `canvus_mcp/downloads.py`, `lab_agent/nodes.py`, `lab_agent/orchestrator_support.py`, and `lab_agent/models/states.py` to the module maps — these existed in code but were missing from the architecture doc.
- `docs/experiment-workflow.md` and `docs/code-standards.md`: corrected the "read/grounding tools" list to match the actual model-facing allowlist in `lab_agent/tool_bridge.py:READ_TOOLS` (`scan_server`, `list_canvases`, `check_ragcluster_connections`, `check_widget_connections`, `get_note`, `get_widget`, `download_pdf`) and clarified that `scan_experiment_workflow`/`detect_experiment_loops` are orchestrator/watcher-level calls, not part of the model's tool-use loop. Noted that `create_browser`/`create_image` exist as `canvus-mcp` write tools but are not called by `lab-agent`'s orchestrator (`lab_agent/nodes.py` only calls `create_note`/`create_connector`).
- `docs/experiment-workflow.md`: confirmed via `canvus_mcp/experiments.py` that marker matching (`Robot_`, `[EXP:Setup`, `[EXP:Result`) is an exact title-prefix (`str.startswith`) check, and confirmed the result-note contract requires both `Setup: <id>` and `Round: <n>` first lines (`lab_agent/orchestrator.py:run_on_robot`).
- `docs/canvus-serving-integration.md`: documented as two distinct known issues — (1) the patch's `query_runner.py` references a filename (`sample_640×426.jpeg`, underscore + Unicode `×`) that does not match this repo's actual asset `assets/images/sample-640x426.jpeg` (hyphen + ASCII `x`); (2) the upload keeps `content_type="image/png"` for a JPEG file. Noted that the extracted `test_experiment_prepare.py` cannot run standalone in this repo (imports from the full `canvus-serving` app tree) and must be validated only after applying the patch to the parent tree.
- `docs/setup-and-operations.md`: made Claude Code registration explicitly optional (moved to its own "optional" prerequisite and subsection) and clarified provider-neutral operation is via the `openai` adapter's OpenAI-compatible `base_url`, not dedicated Ollama/vLLM adapters.
- `docs/notes/use-case-lab-in-the-loop.md`: added a vision-vs-current-implementation status note, clarified that its "Phase 3" is GSK's external program phase (not this repo's roadmap Phase 3), mapped conceptual canvas nodes to current markers, marked unbuilt providers/capabilities as future, and reconciled approval ordering to `AI design → in-silico validation → scientist review → lab lead approval → wet lab`.
- Repository status: clarified across `README.md` and `docs/development-roadmap.md` that files are present on disk but the local git repository has **no commits** and all files are **untracked** — extraction produced files, not a committed release.

### Verified, no change needed

- `RAGCluster_` marker casing was checked across all docs and `canvus_mcp/config.py`'s default (`mcp_ragcluster_marker = "RAGCluster_"`) — already consistent everywhere; no casing fix required.

## Unreleased

### Added

- Created standalone `lap-in-the-loop` repository at `~/dev/lap-in-the-loop`.
- Migrated `apps/canvus-mcp` from `rag-canvus`.
- Migrated `apps/lab-agent` from `rag-canvus`.
- Added root `README.md` with quick start, workflow overview, docs map, and security notes.
- Added root `.gitignore` excluding secrets, virtualenvs, caches, downloads, and generated outputs.
- Added full documentation set:
  - `docs/notes/use-case-lab-in-the-loop.md`
  - `docs/system-architecture.md`
  - `docs/experiment-workflow.md`
  - `docs/setup-and-operations.md`
  - `docs/canvus-serving-integration.md`
  - `docs/code-standards.md`
  - `docs/development-roadmap.md`
  - `docs/project-changelog.md`
- Added `integrations/canvus-serving-experiment-prepare/` to preserve the serving-side `{exp:}` action patch and source/test files.
- Added `assets/images/sample-640x426.jpeg` migrated from the prior serving app asset.
- Initialized git on branch `main`.

### Changed

- Renamed Python package metadata from `rag-canvus-*` to `lap-in-the-loop-*` in migrated app `pyproject.toml` files.
- Reframed docs around standalone Lab-in-the-Loop ownership rather than embedding in `rag-canvus`.

### Preserved

- `canvus-mcp` MCP tools for:
  - canvas/server scanning;
  - note/widget reads;
  - PDF/image/asset downloads;
  - note/browser/image/connector creation;
  - widget/RagCluster connection checks;
  - experiment workflow scan;
  - experiment loop detection.
- `lab-agent` watcher/orchestrator for:
  - idea → setup;
  - setup → mock robot result;
  - result → setup loop analysis;
  - continue/stop decisions;
  - OpenAI and Claude model adapters.

### Security

- Did not copy `.env` files.
- Did not copy `.venv`, cache, or download directories.
- Root `.gitignore` blocks secrets and generated artifacts.

### Initial extraction known issues (superseded)

- At initial extraction, tests/lint still needed to be run in the new repository path; superseded by the 2026-07-16 and 2026-07-17 verified entries above.
- `canvus-serving` patch includes an unrelated JPEG upload MIME mismatch in `query_runner.py`; fix before applying upstream.
- At initial extraction, loop processed-state was session-local in `lab-agent`; superseded by the 2026-07-16 durable-harness entry and 2026-07-17 closed-note recovery fix above.
