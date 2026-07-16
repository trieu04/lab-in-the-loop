# Project Changelog

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

### Known issues

- Tests/lint still need to be run in the new repository path.
- `canvus-serving` patch includes an unrelated JPEG upload MIME mismatch in `query_runner.py`; fix before applying upstream.
- Loop processed-state remains session-local in `lab-agent`.
