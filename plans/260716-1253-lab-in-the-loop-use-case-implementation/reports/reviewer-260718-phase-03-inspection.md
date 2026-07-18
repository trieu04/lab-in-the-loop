# Reviewer Inspection — Phase 3 Browser Artifact Widgets

## Score / Decision

- Score: 9.6/10
- Critical defects: 0
- Decision: SEALED
- Auto-approval rule: met (score >= 9.5 and criticalCount = 0)

## Critical Defects

None found.

## Concerns / Refinements

- Low: MCP tool docstrings still use some historical Note wording for loop detection, even though implementation accepts Browser + legacy Note widgets. Example: `apps/canvus-mcp/canvus_mcp/tools/experiments.py:41`. Not a runtime contract break, but worth tightening later.
- Low: Starlette test client deprecation warning remains in `apps/lab-agent/tests/test_artifact_server.py:14`; temper evidence shows tests still pass.
- External gate: live Canvus reachability and production HTTPS/TLS/private ingress were not observed locally and remain operational gates, correctly documented.

## Former Refutations Rechecked

- Needs-input path resolved: `apps/lab-agent/lab_agent/orchestrator_needs_input.py:22` writes `[EXP:Needs Input]` Browser artifacts, `apps/lab-agent/lab_agent/nodes.py:20` defines the marker, `apps/lab-agent/lab_agent/durable_browser.py:30` maps `ArtifactType.NEEDS_INPUT` to `needs_inputs`, and `apps/lab-agent/tests/test_needs_input_node.py:52` proves Browser creation/URL/connector/store record.
- Needs-input detector/recovery bucket resolved: `apps/canvus-mcp/canvus_mcp/experiment_widgets.py:54` defines `needs_input`, `apps/canvus-mcp/canvus_mcp/experiments.py:98` tracks `needs_inputs`, and `apps/canvus-mcp/tests/test_experiment_widgets.py:101` proves Browser + legacy Note enumeration.
- Human input remains Note-only: `apps/lab-agent/tests/test_needs_input_node.py:153` and `apps/canvus-mcp/tests/test_experiment_widgets.py:129` prove human response Notes are not touched/classified as generated artifacts.
- Docs resolved: `README.md:65`, `docs/experiment-workflow.md:21`, `docs/system-architecture.md:118`, `docs/setup-and-operations.md:134`, `docs/code-standards.md:71`, `docs/development-roadmap.md:117`, `docs/project-changelog.md:3`, and `docs/lab-in-the-loop-use-case-specification.md:93` describe Browser artifacts, legacy Notes, Note-only human inputs, capability security, migration, backup, and deployment constraints.

## Acceptance Coverage

1. New setup, result, closed, and needs-input outputs create Browser widgets with exact workflow title markers, stable artifact URLs, canonical structured records, and durable connectors.
   - Setup/result/closed: `apps/lab-agent/tests/test_browser_artifacts.py:55` and `apps/lab-agent/tests/test_browser_artifacts.py:81`.
   - Needs input: `apps/lab-agent/lab_agent/orchestrator_needs_input.py:49`, `apps/lab-agent/tests/test_needs_input_node.py:52`, `apps/lab-agent/tests/test_needs_input_node.py:65`.
   - Stable resource/widget identity vs token rotation: `apps/lab-agent/lab_agent/durable_browser.py:49`, `apps/lab-agent/lab_agent/durable_browser.py:107`, `apps/lab-agent/tests/test_needs_input_node.py:125`.

2. Artifact updates append versions and change rendered content without recreating the Browser widget.
   - Append-only versions: `apps/lab-agent/lab_agent/artifact_store.py:140`.
   - ETag/render changes: `apps/lab-agent/tests/test_artifact_server.py:129`.
   - In-place Browser repair: `apps/lab-agent/lab_agent/nodes.py:174`, `apps/lab-agent/lab_agent/durable_browser.py:110`.

3. HTML renderer escapes user/model content, rejects executable HTML, uses strict CSP/same-origin assets, responsive + keyboard accessible.
   - Escape/render: `apps/lab-agent/lab_agent/artifact_render.py:76`, `apps/lab-agent/lab_agent/artifact_render.py:174`.
   - CSP/static allowlist: `apps/lab-agent/lab_agent/artifact_http.py:22`, `apps/lab-agent/lab_agent/artifact_http.py:69`.
   - Tests: `apps/lab-agent/tests/test_artifact_render.py:111`, `apps/lab-agent/tests/test_artifact_render.py:161`.

4. Capability reads use high-entropy opaque identifiers and constant-time token verification; revoked/guessed/unauthorized/cross-canvas expose no data.
   - Token generation/hash/constant-time: `apps/lab-agent/lab_agent/state/artifact_tokens.py:26`, `apps/lab-agent/lab_agent/state/artifact_tokens.py:82`.
   - Uniform 404: `apps/lab-agent/lab_agent/artifact_server.py:55`, `apps/lab-agent/tests/test_artifact_server_unauthorized.py:47`.
   - Access log disabled: `apps/lab-agent/lab_agent/cli.py:92`.

5. Workflow detection accepts generated Browser widgets and legacy Notes; idea and human input remain Note-only.
   - Generated classifier: `apps/canvus-mcp/canvus_mcp/experiment_widgets.py:61`.
   - Idea Note-only: `apps/canvus-mcp/canvus_mcp/experiment_widgets.py:57`.
   - Detector tests: `apps/canvus-mcp/tests/test_experiment_widgets.py:31`, `apps/canvus-mcp/tests/test_experiment_widgets.py:93`, `apps/canvus-mcp/tests/test_experiment_widgets.py:129`.

6. Crash recovery reconciles artifacts, Browser widgets, mappings, and connectors without duplicates.
   - Browser probe/repair/map flow: `apps/lab-agent/lab_agent/durable_browser.py:102`, `apps/lab-agent/lab_agent/durable_browser.py:107`, `apps/lab-agent/lab_agent/durable_browser.py:116`.
   - Connector reconciliation: `apps/lab-agent/lab_agent/durable_browser.py:170`.
   - Tests: `apps/lab-agent/tests/test_durable_browser_recovery.py:32`, `apps/lab-agent/tests/test_needs_input_node.py:125`, `apps/lab-agent/tests/test_durable_browser_error_payloads.py:31`.

7. Legacy generated Notes inventory/mirror idempotently in dry-run-first mode, no deletion by default.
   - Migration logic: `apps/lab-agent/lab_agent/artifact_migration.py:154`, `apps/lab-agent/lab_agent/artifact_migration.py:168`.
   - Non-destructive connector mirroring: `apps/lab-agent/lab_agent/artifact_migration.py:83`.
   - Tests: `apps/lab-agent/tests/test_artifact_migration_apply.py:42`, `apps/lab-agent/tests/test_artifact_migration_apply.py:114`, `apps/lab-agent/tests/test_artifact_migration_apply.py:140`.

8. Both applications pass pytest, ruff, and mypy; security/large-payload/graph/restart/accessibility/migration coverage present.
   - Canonical evidence: `plans/260716-1253-lab-in-the-loop-use-case-implementation/evidence/temper-results.json:1`.
   - Raw evidence: `plans/260716-1253-lab-in-the-loop-use-case-implementation/evidence/temper-raw-runs.json:1`.
   - Verified: 9/9 commands exit 0, 37 canvus-mcp + 256 lab-agent = 293 tests, ruff/mypy clean, parity clean, git diff check clean.
   - Focused re-run during inspection: 24 lab-agent tests passed and 16 canvus-mcp tests passed.

9. README and eight Phase 3 docs describe service/deployment constraints.
   - README: `README.md:65` and `README.md:121`.
   - Workflow: `docs/experiment-workflow.md:21`, `docs/experiment-workflow.md:94`, `docs/experiment-workflow.md:147`.
   - Architecture: `docs/system-architecture.md:118`.
   - Operations: `docs/setup-and-operations.md:134`.
   - Standards: `docs/code-standards.md:71`.
   - Roadmap: `docs/development-roadmap.md:117`.
   - Changelog: `docs/project-changelog.md:3`.
   - Use-case spec: `docs/lab-in-the-loop-use-case-specification.md:93`.

## Regression / Contract Result

- Contract status: OK.
- Backward compatibility: workflow title markers and connector semantics preserved; generated Browser widgets and legacy Notes both classify; `{idea: ...}` and human-authored responses remain Notes.
- Capability URL contract clarified: stable Browser widget id and `/artifacts/{opaque_id}` path; token-bearing query may rotate and is repaired in place.
- Migration contract preserved: dry-run/mirror-first/idempotent/non-destructive by default.
- Canvas remains workflow truth; durable ledger owns artifact versions, mappings, recovery, and audit evidence.

## Metrics

- Type coverage: mypy clean in both apps (`canvus-mcp` 14 source files, `lab-agent` 53 source files).
- Test coverage: 293 tests passed in final canonical evidence.
- Linting issues: 0.
- Critical issues: 0.

## External Runtime Gates

- Canvus clients must reach `LAB_AGENT_ARTIFACT_PUBLIC_BASE_URL`.
- Production must terminate HTTPS and restrict private ingress/network access.
- Shared state DB backup/restore remains operationally required because it stores workflow state, artifact records, token hashes, and widget mappings.
- No live external Canvus reachability or TLS observation was made in this local inspection.

## Unresolved Questions

None blocking Phase 3 seal.
