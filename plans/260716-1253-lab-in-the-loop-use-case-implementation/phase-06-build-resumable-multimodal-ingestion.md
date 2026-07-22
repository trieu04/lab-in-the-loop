# Phase 6 — Build Resumable Multimodal Ingestion

## Context Links

- Canonical requirements: [lab-in-the-loop-use-case-specification.md](../../docs/lab-in-the-loop-use-case-specification.md) (FR-LITL-020, NFR-LITL-007)
- Roadmap: [development-roadmap.md](../../docs/development-roadmap.md) Phase 4c
- Research: [researcher-02-260716-1253-grounding-governance-ingestion.md](research/researcher-02-260716-1253-grounding-governance-ingestion.md)
- Existing seams: `apps/canvus-mcp/canvus_mcp/downloads.py`, `tools/content.py`, `ragcluster.py`

## Overview

- Priority: P2
- Status: complete (2026-07-19)
- Effort: 6d
- Description: Delivered bounded, local-source, resumable ingestion. It uses SQLite/WAL and a standalone worker; it does not introduce a distributed queue, object store, deployment, or unsupported media extractor.

## Key Insights

- SHA-256 is immutable raw-content identity; extractor version identifies derived work.
- Restart-safe work is small leased units, not one opaque model call.
- Raw bytes remain in a protected local cache. Only bounded extracted chunks can become evidence.
- The single-host topology is deliberate. Queue/object-store migration needs measured operational pressure.

## Requirements

- FR-LITL-020: delivered local-source chunked, cached, resumable ingestion.
- NFR-LITL-007: delivered durable progress and restart recovery within configured local bounds.
- NFR-LITL-001/002: protected acquisition/cache and Phase 5 locality before later provider calls.
- Existing MCP download contracts and non-ingestion anonymous reads/downloads remain compatible.

## Architecture / Data Flow

```text
authorized Canvus source → byte-counted stream + SHA-256 → protected local cache
  → SQLite/WAL asset/source/job/unit/chunk/lease state
  → standalone leased worker → bounded completed chunks/status MCP reads
  → lab-agent EvidenceLedger → locality authorization → later provider call
```

Jobs deduplicate by `(asset_sha256, extractor_version)`. Chunks and unit completion are atomic; stale lease generations cannot commit.

## Related Code Files

- Delivered Canvus components: `ingestion_schema.py`, `ingestion_store*.py`, `ingestion_cache.py`, `ingestion_pipeline.py`, `extractors.py`, `ingestion_worker.py`, `access_control.py`, and `tools/ingestion.py`.
- Delivered integration: Canvus server/config/download wiring; exact read-only ingestion allowlisting and bounded evidence handling in `apps/lab-agent/lab_agent/tool_bridge.py`.
- Delivered coverage: ingestion store/pipeline/worker/cache/access/MCP/HTTP/crash-recovery tests plus lab-agent ingestion evidence/locality/transport regressions.
- Delivered documentation: architecture, operations, standards, roadmap, canonical specification, workflow, and changelog.

## Implementation Steps

1. Added checksummed SQLite/WAL schema for assets, canvas-scoped sources, jobs, units, chunks, leases, attempts, cancellation, and audit state.
2. Added SHA-256 plus extractor-version idempotency; unchanged content reuses derived work and a new version creates only new derived work.
3. Added protected streaming acquisition/cache and bounded strict UTF-8 text, CSV/TSV, JSON, image-metadata, and PDF-page extractors with typed outcomes.
4. Added the standalone `IngestionWorker`: lease renewal, graceful stop, expired-lease reclaim, retry/backoff, poison/cancel state, and completed-unit preservation. The MCP server does not start it.
5. Added authenticated, canvas-scoped enqueue/status/chunk/retry/cancel MCP tools with reader, trusted-service, and operator roles.
6. Added exact `get_ingestion_status` and `read_ingestion_chunks` model read access only; status is operational/non-citeable and chunks are bounded untrusted evidence subject to locality.
7. Added durability, recovery, deduplication, authorization, transport, and model-boundary regressions; documented single-host limits and the conditional migration trigger.

## Todo List

- [x] Durable asset/job/unit/chunk/lease store added
- [x] Content-hash + extractor-version dedup implemented
- [x] Focused extractor Protocols and initial local extractors added
- [x] Resumable worker supports retry/cancel/restart
- [x] MCP enqueue/status/retry/cancel/read tools registered with role-based exposure
- [x] Trusted-client/operator authorization and local-default transport policy tested
- [x] Lab-agent evidence path consumes approved chunks
- [x] Failure/restart/dedup/security tests pass
- [x] Architecture, operations, standards, roadmap, canonical spec, and changelog updated
- [x] Final temper evidence and sealed inspection reconciled; Phase 7 remains untouched

## Success Criteria / Validation

- Completed locally on 2026-07-19; final evidence sealed on 2026-07-20: durable WAL jobs/chunks/leases; SHA/version dedup; protected streaming/cache; bounded strict extractors; standalone worker recovery; authenticated scoped MCP tools; bounded lab-agent evidence/locality path.
- Authoritative final validation: `canvus-mcp` full suite **134 passed**, focused Phase 6 regressions **77 passed**; `lab-agent` full suite **480 passed**, focused Phase 6 regressions **122 passed**.
- Ruff, mypy, compileall, lock checks, package builds, workflow-contract parity, tracked/untracked whitespace checks, and Phase 7 isolation passed. Real local streamable-HTTP authorization and subprocess crash/restart proofs passed.
- Final inspection seal: **9.7/10**, `criticalCount: 0`, `decision: SEALED`; all prior C1–C2, H1–H8, and M1–M5 findings closed; `contractStatus: INTACT`; no reachable regressions.
- Known deprecation warnings are non-blocking. Statement/branch coverage remains unclaimed: authoritative hosted/locked-environment coverage tooling was unavailable.
- Interrupted work preserves completed units; unchanged content reuses cache/job; new extractor versions invalidate derived chunks only; model context receives bounded chunk evidence and scalar provenance, never raw bytes, paths, or capability URLs.

## External Gates — Not Completed / Not Claimed

- [ ] Live credentials, live Canvus validation, deployment approval, and production reachability.
- [ ] Hosted CI and authoritative locked-environment statement/branch coverage tooling.
- [ ] Large-format validation plus approved operator capacity, backup, disk-monitoring, and retention policy; no automatic cleanup exists.
- [ ] Video and spreadsheet formats other than CSV/TSV; they remain typed unsupported/external gates.
- [ ] External queue/object-storage or multi-host migration; evaluate only on measured sustained backlog/throughput, disk pressure, availability/SLO failure, or a multi-host requirement. No numeric threshold or distributed implementation exists.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| SQLite contention under worker concurrency | Med | Med | WAL, short transactions, bounded 1–4 workers; measure before migration. |
| Extractor attack surface | Med | High | Minimal local parsers, strict bounds, typed failures, malformed-file tests. |
| Protected local state outgrows operator controls | Med | High | External gate: approved backup, retention, capacity, and disk-monitoring procedure. |
| Unsupported media is mistaken for supported | Med | High | Typed `unsupported`; no video/non-CSV/TSV spreadsheet claim. |
| Network caller invokes a mutation tool | Med | Critical | Exact Bearer role/canvas checks, fixed denial, metadata-only audit, negative tests. |

## Security Considerations

- Downloaded files are untrusted: bound acquisition and parsing; cache only verified regular files using no-follow, owner/mode/inode/digest checks.
- Cache directories are owner-only and entries private; raw content, paths, credentials, and capability URLs stay out of MCP/model/audit output.
- Enforce role, exact canvas scope, and locality. Model allowlisting is defense in depth, not MCP authentication.

## Next Steps / Dependencies

- Depends on delivered Phase 2 WAL/lease conventions, Phase 4 evidence, and Phase 5 locality.
- Phase 7 is the next pending phase.
- Docs impact: major; delivered documentation reflects the local-source contract and external gates.
