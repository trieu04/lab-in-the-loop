# Phase 6 — Build Resumable Multimodal Ingestion

## Context Links

- Canonical requirements: `docs/lab-in-the-loop-use-case-specification.md` (FR-LITL-020, NFR-LITL-007)
- Roadmap: `docs/development-roadmap.md` Phase 4c
- Research: `research/researcher-02-260716-1253-grounding-governance-ingestion.md`
- Existing seams: `apps/canvus-mcp/canvus_mcp/downloads.py`, `tools/content.py`, `ragcluster.py`

## Overview

- Priority: P2
- Status: pending
- Effort: 6d
- Description: Add content-hash deduplication, chunk/cache/job state, resumable single-host workers, and progress reporting for PDF/image/video/table ingestion. Reuse SQLite/WAL patterns; do not introduce a distributed queue.

## Key Insights

- `downloads.save_bytes()` already returns SHA-256; use it as the immutable cache/idempotency key.
- Long-running extraction must be split into restart-safe units, not one opaque model call.
- SQLite is appropriate for current single-host worker topology. Celery/Kafka/Redis is deferred until measured multi-host throughput requires it.
- Extraction output becomes evidence for Phase 3; raw bytes remain outside model context unless an approved extractor emits bounded text/metadata.

## Requirements

- FR-LITL-020: chunked, cached, resumable multimodal ingest.
- NFR-LITL-007: multi-day/large-asset jobs resume after interruption and expose progress.
- NFR-LITL-001/002: downloads remain protected; sensitive content follows Phase 4 locality policy.
- Preserve existing MCP download contracts and backward compatibility.

## Architecture / Data Flow

```text
download_* → saved file + sha256
  → enqueue(asset hash, modality, extractor version)
  → SQLite job + deterministic work units
  → leased worker extracts bounded chunks
  → chunk cache(hash, extractor version, ordinal)
  → status/progress/read-chunks MCP tools
  → lab-agent read-only grounding + EvidenceLedger
```

Jobs are idempotent by `(asset_sha256, extractor_version)`. Units are retryable; terminal failures keep the source path/hash and reason.

## Related Code Files

- Create: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/ingestion_store.py` — SQLite schema for assets, jobs, units, chunks, leases, attempts, and progress.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/ingestion_pipeline.py` — modality dispatch, deterministic unit creation, retry/resume orchestration.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/extractors.py` — focused extractor Protocols and initial local PDF/text/image-metadata/table implementations; video contract may remain mock until an approved decoder exists.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/ingestion_worker.py` — bounded async single-host worker with lease renewal and graceful shutdown.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/tools/ingestion.py` — enqueue, status, retry, cancel, and bounded chunk-read tools.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/access_control.py` — trusted service/operator roles and centralized authorization for mutating MCP tools.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/tools/__init__.py`, `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/server.py` — register tools, enforce authenticated/trusted client context, bind locally by default, and optionally start/stop the worker.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/downloads.py`, `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/tools/content.py` — expose stable hash/path metadata to enqueue flow.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/config.py`, `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/.env.example`, `/home/ntdm/dev/lap-in-the-loop/.gitignore` — DB/cache paths, worker limits, ignored artifacts.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/tool_bridge.py` — allow approved read-only ingestion status/chunk tools; never allow enqueue/cancel to the model.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/tests/test_ingestion_store.py`, `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/tests/test_ingestion_pipeline.py`, `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/tests/test_ingestion_tools.py`.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/tests/test_downloads.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_tool_bridge.py`.
- Modify: `/home/ntdm/dev/lap-in-the-loop/docs/system-architecture.md`, `/home/ntdm/dev/lap-in-the-loop/docs/setup-and-operations.md`, `/home/ntdm/dev/lap-in-the-loop/docs/code-standards.md`, `/home/ntdm/dev/lap-in-the-loop/docs/development-roadmap.md`, `/home/ntdm/dev/lap-in-the-loop/docs/lab-in-the-loop-use-case-specification.md`, `/home/ntdm/dev/lap-in-the-loop/docs/project-changelog.md`.

## Implementation Steps

1. Define job/unit/chunk schemas and WAL store. Use unique keys for content hash + extractor version + unit ordinal.
2. Make enqueue idempotent: unchanged content returns the existing completed/in-progress job; changed content creates a new versioned job.
3. Define extractor Protocols. Initial implementations must be deterministic and bounded; unsupported modalities return a typed `unsupported` state, not fabricated descriptions.
4. Implement a leased worker with configurable concurrency, attempt limit, cancellation, backoff, and graceful restart. Completed units never rerun.
5. Add progress calculation from durable unit states and MCP tools for enqueue/status/retry/cancel/read-chunks.
6. Add a real MCP client authorization boundary: local binding by default; authenticated trusted-service role for orchestrator writes/enqueue; operator role for retry/cancel; anonymous/untrusted callers denied and audited. Model tool allowlisting is defense-in-depth, not authentication.
7. Integrate approved chunk reads into `READ_TOOLS` and Phase 4 evidence capture; enforce Phase 5 locality before any model call.
8. Test crash/restart midway, duplicate enqueue, extractor-version invalidation, cancellation, poison unit, bounded output, and ignored DB/cache files.
9. Document capacity limits and migration trigger: introduce an external queue/object store only when single-host throughput, disk, or availability targets cannot be met.

## Todo List

- [ ] Durable asset/job/unit/chunk/lease store added
- [ ] Content-hash + extractor-version dedup implemented
- [ ] Focused extractor Protocols and initial local extractors added
- [ ] Resumable worker supports retry/cancel/restart
- [ ] MCP enqueue/status/retry/cancel/read tools registered with role-based exposure
- [ ] Trusted-client/operator authorization and local-default transport policy tested
- [ ] Lab-agent evidence path consumes approved chunks
- [ ] Failure/restart/dedup/security tests pass
- [ ] Architecture, operations, standards, roadmap, canonical spec, and changelog updated

## Success Criteria / Validation

- `cd apps/canvus-mcp && uv run pytest -q && uv run ruff check canvus_mcp tests && uv run mypy canvus_mcp`
- `cd apps/lab-agent && uv run pytest -q && uv run ruff check lab_agent tests && uv run mypy lab_agent`
- Killing the worker mid-job and restarting resumes unfinished units without rerunning completed units.
- Re-enqueueing unchanged content returns the same job/cache; extractor-version changes invalidate only derived chunks.
- Unauthenticated/untrusted MCP clients cannot enqueue, cancel, retry, or mutate canvas state; denied attempts are audited.
- Progress and terminal failure reasons are visible; raw bytes are never inserted into model context by default.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| SQLite contention under worker concurrency | Med | Med | Small transactions, WAL/busy timeout, bounded workers, measured migration trigger. |
| Extractor libraries expand attack surface | Med | High | Minimal approved dependencies, sandbox/time/memory bounds, malformed-file tests. |
| Cache leaks sensitive content | Med | High | Local ignored directory, restrictive permissions, retention policy, no raw-content audit logs. |
| Video support becomes premature scope | High | Med | Ship Protocol + typed unsupported/mock path until an approved decoder/use case exists. |
| Any network client invokes mutation tools | Med | Critical | Local bind default, authenticated service/operator roles, centralized checks, denial audit, negative tests. |

## Security Considerations

- Treat every downloaded file as untrusted. Validate type by bytes, cap size/work, avoid shelling out with unsanitized paths.
- Enforce tenant/canvas and locality scope on job/chunk reads.
- Do not expose enqueue/cancel/retry mutation tools to the model-facing allowlist. Independently authenticate/authorize every MCP caller; allowlisting inside lab-agent is not a transport control.

## Next Steps / Dependencies

- Depends on: Phase 2 WAL/lease conventions; Phase 4 evidence contract; Phase 5 locality policy for model consumption.
- Can progress as a canvus-mcp-heavy branch while Phase 7 design is reviewed, but integration lands before Phase 9 E2E.
- External gate: approved extractor libraries and retention limits for real internal media.
- Docs impact: major.
