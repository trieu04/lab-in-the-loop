# Phase 3 — Upgrade Generated Artifacts to HTML Browser Widgets

<!-- Updated: Validation Session 1 - generated artifacts only; dynamic artifact service -->

## Context Links

- Owner decision: `reports/decision-log-260716-1253-implementation-scope.md` — generated workflow artifacts only; dynamic artifact service
- Current write path: `apps/lab-agent/lab_agent/nodes.py`, `render.py`
- Current widget tools: `apps/canvus-mcp/canvus_mcp/tools/widgets.py`
- Current detector: `apps/canvus-mcp/canvus_mcp/experiments.py`
- Browser SDK: `apps/canvus-mcp/canvus-sdk/src/canvus_sdk/models/widgets.py`, `resources/widgets.py`
- Canonical requirements affected: FR-LITL-002/009/010/021, NFR-LITL-004/005/008/010, BR-LITL-005/008

## Overview

- Priority: P1
- Status: complete
- Effort: 7d
- Description: Replace system-generated experiment Notes with Browser widgets backed by a dynamic HTML artifact service and canonical structured artifact store. Keep user-authored `{idea: ...}` and human input Notes as the simple canvas entry/control surface.

## Key Insights

- Canvus Browser widgets store only URL/title/view properties; they do not provide arbitrary metadata storage. Canonical JSON and metadata must live in the harness.
- A stable artifact URL lets content and tabs update without recreating the Browser widget.
- Existing title-prefix markers can remain the connector/state contract if detection accepts generated Browser widgets as well as legacy Notes.
- Browser pages cannot send custom auth headers. Use private-network deployment plus opaque capability URLs, HTTPS, revocation, strict cross-canvas checks, and no third-party assets.
- Legacy Notes must remain readable during migration; automatic deletion is too risky.

## Requirements

- Replace generated Setup, Result, Closed, Needs Input, In-silico, approval-status, Analysis, Knowledge, and Conflict artifacts with Browser widgets.
- Keep `{idea: ...}` and human-authored approval/review input as Notes unless a later UI plan explicitly replaces them.
- Store versioned structured payload, metadata, provenance, state, hashes, timestamps, artifact type, round, and Browser widget mapping outside the widget.
- Render responsive tab views. Baseline tabs: Overview, Details, Evidence, Metadata, Audit; artifact types may add Validation, Execution, Analysis, or Versions.
- Preserve exact workflow title markers and connector semantics.
- Support legacy Note reads and a dry-run/mirror-first migration path.
- Meet NFR security, traceability, operability, and larger-data-display goals without placing raw secrets/internal documents in HTML.

## Architecture / Data Flow

```text
orchestrator typed model
  → ArtifactDocument(schema_version, payload, metadata, provenance, state)
  → ArtifactStore (same Phase 2 SQLite DB; versioned migrations)
  → stable capability URL: {public_base}/artifacts/{opaque_id}?token=...
  → canvus-mcp create_browser(title marker, url, size)
  → connector graph treats Browser title marker as generated workflow node

Canvus Browser GET /artifacts/{id}
  → verify token hash + artifact/canvas scope + revocation
  → server-render tab shell + escaped structured content
  → same-origin CSS/JS only; CSP, no third-party CDN
```

The Browser widget is a view and graph node. `ArtifactStore` is the canonical generated-artifact record. Canvas connectors remain durable workflow-transition evidence.

## Related Code Files

- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/models/artifact.py` — `ArtifactDocument`, `ArtifactType`, `ArtifactVersion`, `TabDefinition`, metadata/provenance models.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/artifact_store.py` — CRUD/versioning, widget mapping, opaque id, capability token hash, revocation, artifact/canvas lookup.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/artifact_render.py` — typed artifact→safe view model/tab mapping; legacy text import helpers.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/artifact_server.py` — minimal Starlette/ASGI routes for HTML, same-origin assets, health, ETag, capability authorization.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/static/artifact-tabs.js`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/static/artifact-view.css` — dependency-free tab UI and responsive styling.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/migrations/002_artifact_documents.sql` — artifact/version/token/widget mapping tables.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/pyproject.toml` — explicit minimal ASGI/runtime dependencies already compatible with the MCP stack.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/config.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/cli.py` — artifact bind/public URL, token/security settings, and long-running artifact-server command.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/nodes.py` — add `create_artifact_widget`; keep Note creation only for legacy/user-input artifacts.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/orchestrator.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/watch.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/render.py` — persist/read structured artifacts and retain legacy Note fallback.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/tools/widgets.py` — `create_browser` title/transparent options and authorized `update_browser` for token rotation/URL repair.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/experiments.py`, `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/tools/experiments.py` — accept Browser title markers for generated artifacts; keep idea detection Note-only.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_artifact_store.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_artifact_render.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_artifact_server.py`.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/fakes.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_orchestrator.py`, `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/tests/test_experiments.py` — Browser graph, legacy compatibility, large payload, and migration cases.
- Create: `/home/ntdm/dev/lap-in-the-loop/scripts/migrate-generated-notes-to-browser-artifacts.py` — dry-run/mirror-first operator migration; no deletion by default.
- Modify: `/home/ntdm/dev/lap-in-the-loop/README.md`, `/home/ntdm/dev/lap-in-the-loop/docs/lab-in-the-loop-use-case-specification.md`, `/home/ntdm/dev/lap-in-the-loop/docs/system-architecture.md`, `/home/ntdm/dev/lap-in-the-loop/docs/experiment-workflow.md`, `/home/ntdm/dev/lap-in-the-loop/docs/setup-and-operations.md`, `/home/ntdm/dev/lap-in-the-loop/docs/code-standards.md`, `/home/ntdm/dev/lap-in-the-loop/docs/development-roadmap.md`, `/home/ntdm/dev/lap-in-the-loop/docs/project-changelog.md`.

## Implementation Steps

1. Define `ArtifactDocument` and version tables. Keep payload and metadata typed/versioned; store content hash, state, round, provenance, audit references, browser widget id, token hash, created/updated timestamps.
2. Implement artifact store on the Phase 2 SQLite connection/migration framework. Updates append a version; no destructive overwrite of safety-relevant history.
3. Build safe view-model/tab rendering. Escape all user/model content, reject raw HTML in payloads, apply strict CSP, no remote scripts/styles, no unsafe inline execution.
4. Add the artifact ASGI service with stable opaque URLs, constant-time token verification, revocation/rotation, artifact/canvas scope checks, ETag/cache rules, and non-sensitive health endpoint.
5. Extend `create_browser` with title/transparent options and add authorized `update_browser`. Browser title keeps markers such as `[EXP:Setup v001]`.
6. Add `create_artifact_widget`: persist intent/document first, create Browser widget via Phase 2 side-effect intent, store widget id, create connector, reconcile on restart.
7. Generalize workflow classification so generated Setup/Result/etc. may be Browser or legacy Note; idea/human input remains Note-only. Read generated data from ArtifactStore by widget mapping, not from Browser HTML.
8. Implement artifact-specific tabs: Setup, Result, Closed, Needs Input initially; Phase 7/8 add Validation, Approval, Execution, Analysis, Versions/Conflict using the same renderer contract.
9. Add migration script: inventory legacy generated Notes, parse known sections, create imported ArtifactDocuments and Browser mirrors, clone/verify connectors in dry-run, and require explicit operator confirmation before any archive/delete action.
10. Test large payloads, XSS/script injection, token guessing/revocation, cross-canvas access, restart reconciliation, Browser graph detection, Note fallback, tab accessibility, and migration idempotency.
11. Document public URL/network requirements. Canvus clients must reach the artifact service; production requires HTTPS/private ingress and backup of the shared artifact/state DB.

## Todo List

- [x] Artifact models, versioned store, and migration added
- [x] Safe tab renderer and same-origin static assets added
- [x] Capability-protected dynamic artifact service added
- [x] Browser creation/update tools support marker titles
- [x] Generated artifact creation uses Browser widgets
- [x] Workflow detector supports Browser + legacy Note artifacts
- [x] Idea/human input remains Note-only
- [x] Legacy migration is dry-run/mirror-first and idempotent
- [x] Security, large-payload, graph, restart, accessibility, and migration tests pass
- [x] README and affected docs/changelog updated

## Success Criteria / Validation

- `cd apps/canvus-mcp && uv run pytest -q && uv run ruff check canvus_mcp tests && uv run mypy canvus_mcp`
- `cd apps/lab-agent && uv run pytest -q && uv run ruff check lab_agent tests && uv run mypy lab_agent`
- A new setup/result/closed artifact creates a Browser widget with the correct title marker, stable URL, connector, and canonical structured record.
- Updating artifact data changes rendered tabs without recreating the Browser widget.
- Payloads materially larger than current Note bodies render without truncating canonical JSON.
- Scripts/HTML supplied in model/user fields render as text and cannot execute.
- Unauthorized, revoked, wrong-canvas, or guessed capability URLs return no artifact data.
- Existing Note-based canvases still scan and continue; repeated migration creates no duplicate artifact or connector.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Canvus clients cannot reach localhost artifact URL | High | High | Required public-base config, startup reachability check, deployment docs, private ingress. |
| Capability URL leaks sensitive artifact | Med | Critical | High-entropy token, hashed storage, HTTPS/private network, revocation/rotation, no referrer, scope checks. |
| Stored content causes XSS in Browser widget | Med | Critical | Typed rendering, escaping, CSP, no raw HTML/remote assets, adversarial tests. |
| Artifact DB and canvas graph drift | Med | High | Phase 2 intents/reconciliation, widget mapping, startup consistency scan, repair command. |
| Migration damages existing connectors | Low | Critical | Dry-run/mirror-first, graph snapshot, explicit confirmation, no deletion default, rollback mapping. |
| Artifact service outage makes canvas nodes blank | Med | High | Health/alerts, durable DB, cached last response where safe, clear unavailable page, legacy fallback during migration. |

## Security Considerations

- Browser widgets cannot attach custom headers; capability URLs are bearer secrets. Never log full URLs/tokens or expose them in model context/audit payloads.
- Bind privately by default; production ingress must use HTTPS and restrict network access to intended Canvus clients/users.
- Enforce artifact/canvas/tenant scope at every read and update. Metadata may be as sensitive as scientific payloads.
- Keep raw evidence/documents out of HTML unless explicitly approved; render references and bounded excerpts.

## Next Steps / Dependencies

- Depends on: Phase 1 stable markers and Phase 2 durable store/intents/auth boundary.
- Blocks: all later generated artifact formats and production E2E.
- External gate: reachable artifact-service base URL and production ingress/TLS ownership.
- Docs impact: major.
