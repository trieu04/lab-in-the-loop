# Phase 9 — Harden Production & Multi-User Operations

## Context Links

- Canonical requirements: `docs/lab-in-the-loop-use-case-specification.md` (NFR-LITL-006/008/010, ACT-LITL-13)
- Roadmap: `docs/development-roadmap.md` Phase 7
- Architecture: `docs/system-architecture.md`
- Research: `research/researcher-03-260716-1253-safe-execution-integrations.md`

## Overview

- Priority: P1 before production; P2 for scale-out migration
- Status: pending
- Effort: 7d for single-host production hardening; multi-host/Postgres migration separately triggered
- Description: Add operational visibility, health/metrics, realistic integration/E2E coverage, CI, container packaging, recovery runbooks, and tenant/canvas isolation. Prove safe single-host multi-canvas operation before considering multi-host infrastructure.

## Key Insights

- Phase 2 already provides the correct initial concurrency model: SQLite on local disk and one writer lease per canvas.
- Multi-user support is an isolation problem before it is a scale problem. State, logs, budgets, artifacts, and approvals must all carry tenant/canvas scope.
- Health must distinguish process alive, MCP reachable, provider available, state store writable, worker healthy, and external adapter readiness.
- Postgres becomes necessary only when multiple writer hosts/HA or SQLite availability limits are measured requirements.

## Requirements

- NFR-LITL-006: restart/recovery and durable idempotency under operational failure.
- NFR-LITL-008: structured logs, metrics, health checks, per-canvas progress, dashboards/alerts integration.
- NFR-LITL-010: operable CLI/config/troubleshooting and deployment packaging.
- ACT-LITL-13: auditor/admin capability through scoped audit views and health/operations controls; no unsupported human persona assumption.
- Multi-user/canvas isolation from roadmap Phase 7; no credential/state leakage.

## Architecture / Data Flow

```text
TenantContext(tenant_id, allowed canvas ids, credential domain)
  → watch scheduler (bounded concurrent canvases)
  → per-canvas lease + budget + audit + artifact scope
  → structured event/log/metric with run_id + tenant_id + canvas_id

health:
  process + SQLite + MCP + model providers + ingestion worker + configured adapters
CI/E2E:
  fake Canvus/MCP server → both apps → live-recomputed graph → durable state/restart tests
```

Single-host is the production baseline. One process pair (`canvus-mcp` + `lab-agent`) serves exactly one credential domain; all configured canvases must belong to that domain. Tenants needing different Canvus credentials run isolated process pairs until a separately threat-modeled credential registry exists. Scale-out is evidence-based, not prebuilt.

## Related Code Files

- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/tenant.py` — `TenantContext`, canvas allowlist, single credential-domain reference, and scope guards; reject mixed-domain process configuration.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/observability.py` — run ids, structured event context, counters/histograms, health aggregation.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/observability.py` — server/tool/download/ingestion metrics and dependency health.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/tools/health.py` — read-only health/status tool; register in `tools/__init__.py`.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/config.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/cli.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/watch.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/state_store.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/policy.py` — tenant scope, bounded multi-canvas scheduler, health command, schema migration, metrics.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/server.py`, `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/client.py`, `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/config.py` — health and telemetry hooks.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/integration/fake_mcp_server.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/integration/test_end_to_end_loop.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/integration/test_browser_artifact_delivery.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/integration/test_multi_canvas_isolation.py`.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/tests/test_health.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_tenant.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_observability.py`.
- Create: `/home/ntdm/dev/lap-in-the-loop/.github/workflows/ci.yml` — pytest/ruff/mypy for both apps, cross-app contract parity, integration suite, container builds, secret/artifact checks.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/Dockerfile`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/Dockerfile`, `/home/ntdm/dev/lap-in-the-loop/deploy/compose.yaml` — non-root containers, health commands, local persistent volumes.
- Create: `/home/ntdm/dev/lap-in-the-loop/scripts/seed-demo-canvas.py` — idempotent demo canvas setup for Phase 2/E2E; no embedded credentials.
- Modify: `/home/ntdm/dev/lap-in-the-loop/README.md`, `/home/ntdm/dev/lap-in-the-loop/docs/setup-and-operations.md`, `/home/ntdm/dev/lap-in-the-loop/docs/system-architecture.md`, `/home/ntdm/dev/lap-in-the-loop/docs/experiment-workflow.md`, `/home/ntdm/dev/lap-in-the-loop/docs/code-standards.md`, `/home/ntdm/dev/lap-in-the-loop/docs/development-roadmap.md`, `/home/ntdm/dev/lap-in-the-loop/docs/lab-in-the-loop-use-case-specification.md`, `/home/ntdm/dev/lap-in-the-loop/docs/project-changelog.md`.

## Implementation Steps

1. Add `TenantContext` and migrate durable tables to composite tenant/canvas keys with a backward-compatible default tenant. Scope every claim, lease, approval, budget, artifact, and audit query.
2. Enforce one credential domain per process pair. Validate configured canvas allowlists at startup; mixed-domain tenants fail startup. Document isolated deployments for different Canvus credentials.
3. Add bounded multi-canvas scheduling. One canvas failure must not block others; one live writer lease per tenant/canvas remains mandatory.
4. Standardize structured log/event fields: run id, tenant, canvas, round, stage, adapter, duration, status, error class. Redact content and credentials.
5. Add metrics for scans, pending triggers, nodes/connectors created, model usage/cost, validation/approval transitions, job duration/failure, loop closure, and worker health.
6. Implement layered health checks and CLI/operator output, including artifact-service public-base reachability, DB/read health, token verification, and Browser page delivery. Dependency degradation must be explicit; safety-critical dependency failure blocks transitions.
7. Build a fake MCP/Canvus server that recomputes graph state. Add full Browser-artifact mock loop, page delivery, update-without-widget-recreate, restart/recovery, malformed-output, approval, adapter failure, ingestion resume, and multi-canvas isolation E2E tests.
8. Add CI for both apps and container builds. Run the Phase 1 cross-app marker/render/document parity check. Fail on test/lint/type errors, tracked secrets, `.env`, DB/cache/download artifacts, or missing lock consistency.
9. Package non-root containers and local compose deployment with persistent state/download/cache volumes and documented backup/restore/recovery.
10. Add an idempotent demo-seeding script and operator runbooks for restart, SQLite backup/reset, stuck lease, failed job, provider outage, and adapter disablement.
11. Evaluate Postgres/multi-host only if a trigger is met: more than one writer host, HA requirement, network storage, sustained SQLite lock contention, or tenant volume beyond tested capacity. Record the decision as an ADR before migration.

## Todo List

- [ ] Tenant/canvas scoping added to all durable and policy records
- [ ] One-credential-domain-per-process invariant enforced and documented
- [ ] Bounded multi-canvas scheduler and isolation tests pass
- [ ] Structured logs, metrics, and layered health checks include artifact service/page delivery
- [ ] Fake-server E2E covers full mock workflow and failures
- [ ] CI runs pytest/ruff/mypy/integration/container checks
- [ ] Non-root containers and compose deployment verified
- [ ] Demo seeding and recovery runbooks added
- [ ] Postgres/multi-host decision triggers documented
- [ ] README, operations, architecture, workflow, standards, roadmap, spec, and changelog synchronized

## Success Criteria / Validation

- `cd apps/canvus-mcp && uv run pytest -q && uv run ruff check canvus_mcp tests && uv run mypy canvus_mcp`
- `cd apps/lab-agent && uv run pytest -q && uv run ruff check lab_agent tests && uv run mypy lab_agent`
- CI passes from a clean checkout and builds both containers.
- Restart during each major stage resumes safely without duplicate external intents or canvas writes.
- Concurrent test tenants/canvases cannot read or mutate each other's state, budgets, artifacts, approvals, or logs.
- Startup rejects mixed credential domains; isolated process pairs prove separate Canvus credentials cannot cross tenant boundaries.
- Health identifies the failed dependency; artifact Browser URLs remain reachable from the Canvus client network; metrics expose per-canvas progress without sensitive payloads.
- Deployment docs cover backup, restore, restart, rollback/disable, and incident triage.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Tenant scope omitted in one query | Med | Critical | Repository-wide scope helper, schema constraints, adversarial isolation tests, review checklist. |
| Process-global Canvus client uses wrong tenant credential | Med | Critical | One credential domain per process pair, startup validation, isolated deployments, no in-process secret registry yet. |
| Metrics/logs leak scientific content | Med | High | Ids/counts/status only, centralized redaction, test captured telemetry. |
| Containers create false production confidence | Med | Med | Explicit single-host baseline and external-adapter readiness gates; run failure drills. |
| SQLite ceiling reached unexpectedly | Low | High | Load/concurrency tests, lock metrics, documented Postgres trigger and migration boundary. |

## Security Considerations

- Run containers as non-root; mount secrets read-only through the deployment platform, never bake them into images.
- Restrict audit/health endpoints and tools by operator role; health details must not expose credentials or internal document contents.
- Preserve per-credential-domain process isolation when tenants require different Canvus credentials; do not multiplex raw secrets in canvas data.

## Next Steps / Dependencies

- Depends on: all prior phases; real adapter E2E depends on external sandboxes.
- This is the release-readiness gate for production use. Multi-host/Postgres remains a separately approved follow-up unless a trigger is already met.
- Docs impact: major.
