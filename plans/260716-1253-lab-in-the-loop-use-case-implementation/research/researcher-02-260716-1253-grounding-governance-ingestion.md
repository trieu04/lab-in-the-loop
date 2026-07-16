# Research: Grounding, Governance & Ingestion Architecture (Middle Capabilities)

Scope: FR-LITL-001/013/014/015/016/020, NFR-LITL-002/003/004/007/009 — roadmap Phase 4/4b/4c.
Date: 2026-07-16. Constraints honored: research/planning only, no source/doc edits.

## 0. Governing fit with current code

Current seams (all confirmed by direct read, not inferred):

- `lab_agent/adapters/base.py::ModelAdapter` — already a provider-neutral `Protocol`. Keep it; do not replace with a gateway library.
- `lab_agent/adapters/factory.py::get_adapter()` — single global adapter chosen by `Settings.model_provider`. No per-task or per-sensitivity routing today.
- `lab_agent/tool_bridge.py::READ_TOOLS` — the only place the model touches MCP; all writes stay in `lab_agent/nodes.py`. New capabilities MUST enter through this same read/write split (`code-standards.md` "Tool and write discipline").
- `canvus_mcp/downloads.py::save_bytes()` — already content-hashes (`sha256`) and never inlines bytes; this is the natural cache key for ingestion, already half-built.
- `canvus_mcp/ragcluster.py::ConnectorIndex` — the only grounding graph that exists; evidence IDs should resolve through it, not a new index.
- `lab_agent/orchestrator_support.py::coerce()` — fills missing **required** fields with falsy defaults on validation failure. This conflicts with "fail visible, leave pending" in `code-standards.md` and is a real trap for any new required field (e.g. `evidence_sufficient`) — flagged, not fixed here (scout report finding #3 already raised it).
- `AdapterResponse` (`adapters/base.py`) has **no token-usage field**. Both `OpenAIAdapter`/`ClaudeAdapter` discard `completion.usage` / `resp.usage` from the underlying SDK responses today — confirmed by reading both files. Any cost governance requires adding this field first; it is not a policy problem, it's a missing wire.
- `lab_agent/config.py::Settings` / `canvus_mcp/config.py::Settings` — both `pydantic_settings.BaseSettings` with env prefixes (`LAB_AGENT_`, `CANVUS_`/`CANVUS_MCP_`). All new config below follows this exact pattern — no new config system.

Everything proposed below is additive to `lab-agent`/`canvus-mcp` and carries no Claude-Code-skill-only logic, per `code-standards.md` "Provider and harness boundaries" — it will transplant cleanly if/when the harness-first direction is ratified (still `[Proposed]`, unratified per `system-architecture.md`).

---

## 1. Evidence-grounded retrieval, citations, ambiguity contracts

### 1.1 Reference pattern (industry, cross-checked across AWS Bedrock KB, Google Grounding API, Azure RAG guidance)

Converged pattern across 3 independent vendor docs plus a July-2026 entity-attribution study: **immutable evidence IDs assigned at ingestion → model cites short labels only → citations resolved and verified server-side, never trusted from model output alone.** Key risk called out by the arXiv entity-attribution paper (2607.09349, clinical RAG domain): a citation can be topically correct but reference the *wrong entity* — directly relevant to acronym/drug-name-heavy GSK domain language.

### 1.2 Minimal contract (fits current schema, no new service)

Do **not** build a vector DB or retrieval service now (FR-LITL-001 grounding source is still `[Future]`/RagCluster-only — out of scope for this phase). Instead, capture evidence from the **read-tool calls that already happen** in `run_tool_loop` (`lab_agent/loop.py`) — every `get_note`/`get_widget`/`download_pdf` call the model makes is already the evidence; the gap is that results aren't given stable IDs the model can cite back.

New module `lab_agent/evidence.py`:

```python
class EvidenceRecord(BaseModel):
    evidence_id: str        # short label, e.g. "E1" — assigned per run, not globally unique
    source_tool: str        # "get_note" | "get_widget" | "download_pdf" | ...
    source_id: str          # widget_id / note_id / ragcluster_id
    canvas_id: str
    content_hash: str       # sha256, reuse canvus_mcp/downloads.py convention
    retrieved_at: datetime
    excerpt: str            # truncated (~500 chars) — never the full PDF text (see §5 security)

class EvidenceLedger:
    """Per-run, in-memory list of EvidenceRecord. Not persisted — matches the
    existing session-local scope of processed_loops (watch.py); durable
    evidence storage is a Phase 3 durable-state concern, not this phase's."""
```

Wire it into `execute_tool_calls` (`tool_bridge.py`) as a thin wrapper that appends a record per successful read-tool result and stamps the tool response with its `evidence_id` before it re-enters the transcript — so the model sees `E1`, `E2` labels the same way vendor APIs hand back short grounding-chunk IDs.

### 1.3 Schema expansion — extend, do not fork, `ExperimentSetup`

Add fields to `lab_agent/models/experiment.py::ExperimentSetup` (additive, backward compatible — all `Field(default_factory=list)` or defaulted):

```python
citations: list[Citation] = Field(default_factory=list)
evidence_sufficient: bool = Field(default=True)
insufficiency_reason: str = Field(default="")
acronym_flags: list[AcronymFlag] = Field(default_factory=list)
```

```python
class Citation(BaseModel):
    evidence_id: str          # must exist in the run's EvidenceLedger — see verification below
    claim: str                # the specific rationale/step/parameter text it supports

class AcronymFlag(BaseModel):
    term: str
    candidate_expansions: list[str] = Field(default_factory=list)
    needs_confirmation: bool = True
```

This directly satisfies:
- **NFR-LITL-004** (traceability — "every setup can cite the internal notes/PDFs/widgets it used", currently `[Future]`).
- **FR-LITL-015** (insufficient evidence) via `evidence_sufficient`/`insufficiency_reason` — orchestrator checks this flag in `generate_setup` (`orchestrator.py`) and, if `False`, **does not** create `[EXP:Setup]`; instead render a distinct note (reuse the unused `DecisionState.NEEDS_REVIEW` state rather than inventing a tenth enum value — DRY against `models/states.py`, which already has 6 unused states).
- **FR-LITL-016** (ambiguous acronym) via `acronym_flags`. Minimal acronym source: a static local dictionary (e.g. `lab_agent/grounding/acronyms.yaml`, owner-curated — see §6 open dependency) looked up deterministically, not an ML disambiguation model. Matches the existing prompt-only rule in `code-standards.md` ("don't guess, lower confidence") — this schema makes that rule structurally enforceable instead of advisory-only.

### 1.4 Verification — cheap, deterministic, no second model call (YAGNI)

Before rendering a setup note, orchestrator validates every `citation.evidence_id` exists in that run's `EvidenceLedger`. Reject/flag (do not silently drop) citations to IDs never returned by a tool call — this is the "server-side resolution, reject unsupported labels" pattern from the research, achievable with a set-membership check, no LLM-as-judge needed at this scale. A second-pass entailment/faithfulness checker (claim-vs-evidence-text similarity) is a legitimate Phase 4+ enhancement but is **not justified now** — no evidence yet that model-invented citations are a measured problem in this codebase, and it would double model-call cost against the not-yet-built token budget (see §2).

---

## 2. Policy enforcement: provider routing, data locality, token/cost/wall-time/no-progress limits

### 2.1 Provider routing / model routing

**Do not adopt LiteLLM or OpenRouter.** Research comparison: LiteLLM is a self-hosted gateway (100+ providers, spend controls, retries) and OpenRouter is a managed routing intermediary with EU-residency and ZDR flags — both are real, actively maintained (LiteLLM has a July-2026-relevant critical CVE history, GHSA-r75f-5x8p-qvmc, requiring version pinning if ever adopted). But this project has exactly 2 named adapters plus one OpenAI-compatible `base_url` escape hatch (Ollama/vLLM). A gateway adds an external network hop and a new trust boundary for a 2-provider case — contradicts YAGNI and the research's own recommendation that **locality policy must be app-owned, not inferred from gateway config**. Keep `ModelAdapter` Protocol as the only abstraction; revisit LiteLLM only if provider count grows past ~4 or a shared multi-app gateway becomes a real requirement (explicit owner trigger, not a default).

New module `lab_agent/policy.py`:

```python
class TaskRoute(BaseModel):
    task: Literal["setup", "result", "decision"]
    provider: Literal["openai", "claude"]
    model: str
    allow_external: bool = True   # False => must resolve to a local/self-hosted endpoint

class RoutingTable:
    """Task -> route. Defaults to Settings.model_provider for full backward
    compatibility; per-task override via LAB_AGENT_ROUTE_<TASK>_PROVIDER /
    _MODEL env vars, same BaseSettings pattern as config.py."""
```

`orchestrator.py::generate_setup` / `run_on_robot` / `_decide` each call `get_adapter()` once, unconditionally. Change to `get_adapter(route=routing_table.for_task("setup"))` etc. — this is the FR-LITL-014/NFR-LITL-003 "task-based model routing... on top of the existing adapter factory" item from roadmap Phase 4b, verbatim.

### 2.2 Data locality (NFR-LITL-002)

No field or authority exists today for "this canvas/knowledge-scope is internal-only." Minimal design, reusing the marker-config pattern already in `canvus_mcp/config.py` (e.g. `mcp_ragcluster_marker`):

- Add a `locality_class: Literal["internal_only", "external_ok"]` resolved per RagCluster (title-suffix convention, or an explicit allowlist of RagCluster ids in `Settings` — owner decision, see §6).
- Policy check happens in the orchestrator **before** the adapter call, not inside the adapter: if `locality_class == "internal_only"` and the resolved route's provider is not a self-hosted/`openai_base_url`-local endpoint, refuse and route to `NEEDS_REVIEW`/policy-violation state rather than silently sending data externally. Research explicitly warns against "silently relaxing policy" — fail closed, always.
- This is a **policy gate**, not a data-loss-prevention scanner — it trusts the locality tag on the knowledge scope, it does not inspect content. Scope kept intentionally narrow; content-level DLP is out of scope and not implied by any FR/NFR here.

### 2.3 Token/cost/wall-time/no-progress governance (NFR-LITL-009, FR-LITL-013)

Cross-validated pattern (Microsoft AutoGen `TimeoutTermination`/`FunctionalTermination`, OpenAI Agents SDK `max_turns`, LangGraph recursion limit, Anthropic task budgets documented as *advisory only* — client-side enforcement still required): **treat tokens, cost, wall-clock, tool-calls, and semantic no-progress as independent dimensions, each with its own counter, and record which one tripped.**

Prerequisite fix: extend `AdapterResponse` with `usage: TokenUsage | None` (`prompt_tokens`, `completion_tokens`) and populate it in both adapters from `completion.usage` (OpenAI) / `resp.usage` (Claude) — currently discarded. Without this, cost/token governance has no data source.

New module (co-located with routing, since both are "run-scoped guardrails" — one file, not two, per KISS): extend `lab_agent/policy.py` with:

```python
class GovernanceBudget:
    max_tokens: int | None
    max_cost_usd: float | None
    max_wall_seconds: float | None
    max_no_progress_rounds: int          # e.g. 2 identical setup/result diffs in a row

    def check(self, run_state: RunCounters) -> str | None:
        """Return a stop_reason string, or None to continue."""
```

Enforcement point: `orchestrator.py::run_loop`'s existing `while True` already computes `backstop = summary.rounds >= settings.loop_max_rounds` each iteration and writes the reason onto `[EXP:Closed]`. Extend that single check to `budget.check(...)`, additively — no new control-flow shape, just more predicates ORed together, matching the existing "first tripped reason wins" pattern already coded there.

New `Settings` fields (additive, all optional/`None`-default so behavior is unchanged until an owner sets them):
`loop_max_tokens`, `loop_max_cost_usd`, `loop_max_wall_seconds`, `loop_no_progress_rounds`. Pricing to convert tokens→cost needs a small `pricing.yaml`/dict per provider+model — see §6 (this drifts over time; no code can auto-discover it).

"No-progress" detection stays simple: compare successive `setup_text`/`result_text` diffs already available in `run_loop`'s locals — no new instrumentation needed, just a rolling window check.

---

## 3. Resumable chunk/cache/job architecture for async multimodal ingestion (FR-LITL-020, NFR-LITL-007)

### 3.1 Problem, precisely

`canvus_mcp/tools/content.py::download_pdf/download_image/download_asset` call `save_bytes()` **synchronously inline** inside one MCP tool response — for a large PDF/video this blocks the model's tool-use turn (`max_tool_steps` budget from §2.3) on however long the whole download+extraction takes. Roadmap Phase 4c names exactly this: "a multi-day/large-asset ingestion case can resume after interruption... ingestion progress is observable, not a single opaque long-running call."

### 3.2 Cross-validated minimal design (no Celery/Kafka/Redis)

Research surfaced a consistent pattern across `persist-queue` (SQLite-backed queue, WAL mode), `Stabilize` (SQLite durable workflow + chaos-tested resume), and DBOS's own document-ingestion reference: **SQLite lease-table job queue is the standard "single-host, no broker" answer**, and SQLite's own docs confirm WAL supports concurrent readers + one writer on a single host (not over a network filesystem) — matches this project's current single-process deployment model (`setup-and-operations.md`, not yet verified as multi-host).

New module `canvus_mcp/ingestion.py` (mirrors `downloads.py`'s ownership of "how bytes get persisted"), backed by a SQLite file (new `CANVUS_MCP_INGESTION_DB` setting, default `./ingestion.db`, same directory convention as `mcp_output_dir`):

```text
documents(document_id PK, canvas_id, widget_id, content_hash UNIQUE,
          mime_type, original_filename, total_units, status, discovered_at)

units(unit_id PK, document_id FK, unit_index, unit_type,   -- page | frame | table
      status, lease_expires_at, attempt_count, output_path, error,
      content_hash)
```

Job lifecycle (from research, `BEGIN IMMEDIATE` lease claim, matches persist-queue's ack semantics):
`pending → leased → running → completed | (retry_wait → pending) | failed`

**New MCP tools** (`canvus_mcp/tools/ingestion.py`, same `register(mcp)` pattern as every other `tools/*.py` module):
- `start_ingestion(canvas_id, widget_id)` — enqueues `documents` + `units` rows, returns `document_id` immediately, non-blocking. **Not** added to `lab_agent/tool_bridge.py::READ_TOOLS` (it's a side-effecting trigger, not a pure read) — call it from the orchestrator/watcher the same way `scan_experiment_workflow`/`detect_experiment_loops` are already called directly, not exposed to the model (preserves BR-LITL-001 read/write separation).
- `get_ingestion_status(document_id)` — unit counts by status; **is** safe to add to `READ_TOOLS` — pure read, satisfies "ingestion progress is observable."
- `get_ingestion_result(document_id)` — aggregated extracted text/summary once `completed`, partial content if still running; also `READ_TOOLS`-eligible.

**Worker**: separate small async loop, either a background `asyncio.create_task` inside the existing `canvus_mcp/server.py` FastMCP process, or a standalone `canvus-mcp ingest-worker` CLI entrypoint. Recommend the **standalone process** (own crash domain, own restart, doesn't compete with MCP request-handling event loop for large PDF/video CPU work) — this is an operational/topology decision to confirm with the owner (§6), not a code blocker.

**Caching/dedup**: `content_hash` (sha256, same as `downloads.py::save_bytes` already computes) is the unit's cache key — a rescan that finds an already-`completed` hash skips re-processing, satisfying "repeated scans do not re-process unchanged assets" directly.

**Modality-specific extraction, kept minimal**:
- PDF → page-level text extraction (library TBD, see §6 — lightweight `pypdf`/`pdfplumber` preferred over `docling` for a first pass; docling is a much heavier ML-model dependency the research surfaced as strong but overkill for text-only Phase 4c).
- Image/video → **do not build a custom captioning/CV pipeline now** (YAGNI). Frame-sample video into stored image units; let the existing model-adapter tool-use loop describe images at grounding time, same as it already does for any widget it reads. Revisit dedicated captioning only if latency/cost of on-demand model description becomes a measured problem.
- Table → structured CSV/JSON extraction only where the source PDF/asset is already tabular; no generic table-detection ML model added at this phase.

**Explicit non-adoption**: Celery/Kafka/RabbitMQ/Redis are rejected for this phase — single-host, single-active-loop-per-process today (Phase 7 multi-user/multi-canvas concurrency is a separate, later concern per roadmap). SQLite WAL is sufficient at this scale per its own docs; the research is explicit that this ceases to hold once ingestion runs across multiple hosts or needs cross-machine worker pools — flag that boundary now so nobody silently outgrows it.

---

## 4. Provider-neutral service/contract summary (map to files)

| Capability | New/changed module | Fits existing seam |
|---|---|---|
| Evidence capture | `lab_agent/evidence.py` (new) | wraps `tool_bridge.py::execute_tool_calls` |
| Citation/ambiguity schema | `lab_agent/models/experiment.py` (extend `ExperimentSetup`) | additive fields, `orchestrator_support.coerce` interaction flagged |
| Acronym dictionary | `lab_agent/grounding/acronyms.yaml` (new, owner-curated) | loaded by `evidence.py` or a new `lookup_acronym` read tool |
| Task/provider routing | `lab_agent/policy.py::RoutingTable` (new) | wraps `adapters/factory.py::get_adapter()`, called from `orchestrator.py` |
| Data locality gate | `lab_agent/policy.py::LocalityPolicy` (new, same file) | checked in `orchestrator.py` before adapter call |
| Token/cost/wall/no-progress budget | `lab_agent/policy.py::GovernanceBudget` (new, same file) | extends `run_loop`'s existing `backstop` check |
| Adapter usage reporting | `lab_agent/adapters/base.py::AdapterResponse.usage` (extend) + both adapters | prerequisite for cost governance |
| Ingestion job/unit store | `canvus_mcp/ingestion.py` (new) + SQLite file | mirrors `downloads.py` ownership pattern |
| Ingestion MCP tools | `canvus_mcp/tools/ingestion.py` (new) | same `register(mcp)` pattern as every `tools/*.py` |
| Read-tool exposure | `lab_agent/tool_bridge.py::READ_TOOLS` (extend) | add `get_ingestion_status`, `get_ingestion_result` only |
| Config | `lab_agent/config.py::Settings`, `canvus_mcp/config.py::Settings` (extend) | same `pydantic_settings` env-prefixed pattern |

Single new file count: 5 (`evidence.py`, `grounding/acronyms.yaml`, `policy.py`, `canvus_mcp/ingestion.py`, `canvus_mcp/tools/ingestion.py`). Everything else is additive change to files that already own the relevant responsibility — no new service process beyond the optional standalone ingestion worker.

---

## 5. Security / privacy, performance, testing

**Security/privacy**
- Citation `excerpt` fields must stay truncated (~500 chars) and never carry full PDF/document text into canvas notes — canvas is visible to all canvas users; this mirrors the existing rule that downloaded bytes never enter model context inline (`downloads.py`, `NFR-LITL-001`).
- Locality policy must fail closed — refuse, don't downgrade silently, on an ambiguous or missing `locality_class` (matches the "reject if no compliant route exists" principle from the routing research).
- `ingestion.db` and any extracted-artifact files belong under the same git-ignore treatment as `downloads/` (`code-standards.md`: "Never commit... downloaded internal data") — flag for the docs/gitignore owner, not fixed in this research pass.
- Acronym dictionary may contain proprietary/internal terminology — keep local-only; do not send the whole dictionary to an external provider when `locality_class == internal_only` is in effect for that scope.
- Pricing table (`pricing.yaml`, §2.3) is operational config, not a secret, but should not be conflated with credentials — keep separate from `.env`.

**Performance**
- Evidence capture cost is one in-memory `list.append` per read-tool call — negligible; no new network round trip.
- `AdapterResponse.usage` costs nothing extra — the SDKs already return it, it's currently discarded.
- Ingestion worker decouples large-document processing from the loop's `max_tool_steps`/`watch_poll_seconds` cadence — the loop no longer blocks on a multi-minute PDF/video extraction.
- SQLite WAL: multiple readers + one writer, same host only — acceptable now, explicit ceiling flagged in §3.2.

**Testing**
- `GovernanceBudget.check()` and `RoutingTable.for_task()` are pure functions — unit-testable the same way `orchestrator_support.py` already is (existing test pattern, no new fixtures needed).
- Citation verification (`evidence_id` must exist in `EvidenceLedger`) — unit test with a fake ledger, assert invented IDs are rejected/flagged.
- Ingestion lease/claim/resume: new `apps/canvus-mcp/tests/test_ingestion.py` — cover enqueue, claim via `BEGIN IMMEDIATE`, dedup-by-hash on rescan, and a simulated crash (kill mid-lease, assert an expired lease is reclaimed) — this directly targets the Phase 4c "resume after interruption" success criterion and should use the project's existing in-memory fake-MCP test pattern where possible, real SQLite file for the lease logic itself (can't fake `BEGIN IMMEDIATE` semantics meaningfully).
- Adapter `usage` field: extend existing scripted-adapter tests (`apps/lab-agent/tests/`) to assert both real adapters populate it from a mocked SDK response.
- No integration/E2E test is proposed here — Phase 7 already owns "integration tests with a fake Canvus/MCP server"; this research's testing scope stays unit-level, consistent with current test coverage described in the scout report.

---

## 6. External dependencies and owner decisions (unresolved)

1. **PDF/table extraction library** — `pypdf`/`pdfplumber` (light) vs `docling` (heavy, ML-based, strong table/OCR support per research) — recommend starting light for Phase 4c MVP; owner/planner to confirm.
2. **Ingestion worker topology** — background task in `canvus-mcp`'s FastMCP process vs standalone `ingest-worker` CLI process — affects `setup-and-operations.md`; recommend standalone, owner to confirm.
3. **LiteLLM/OpenRouter adoption trigger** — explicitly deferred; owner should set the provider-count or shared-gateway threshold that would revisit this, so it isn't silently reintroduced later.
4. **Token/cost pricing table source and refresh cadence** — no code can auto-discover current $/token rates; owner must supply and maintain `pricing.yaml`.
5. **Acronym dictionary content** — no dictionary exists in-repo; owner must supply/curate the initial GSK-internal acronym list and its update process.
6. **Locality classification authority** — who/what marks a RagCluster/canvas as `internal_only` vs `external_ok`, and how that's represented on the canvas (title suffix vs explicit config allowlist vs new widget property) — no existing field or precedent to reuse.
7. Interaction between `orchestrator_support.coerce()`'s defensive-fill behavior and new **required** fields (`evidence_sufficient`, etc.) — recommend the planner resolve the scout report's open question ("fail closed vs preserve defensive coercion") before implementing §1.3, since `coerce()` would currently silently synthesize `evidence_sufficient=False`/empty citations on any malformed model output rather than failing visibly.

---

## Sources

- LiteLLM: [github.com/BerriAI/litellm](https://github.com/BerriAI/litellm), CVE advisory [GHSA-r75f-5x8p-qvmc](https://github.com/BerriAI/litellm/security/advisories/GHSA-r75f-5x8p-qvmc), open locality gap [issue #30070](https://github.com/BerriAI/litellm/issues/30070)
- OpenRouter routing/locality: [openrouter.ai/docs/guides/routing/provider-selection](https://openrouter.ai/docs/guides/routing/provider-selection), [openrouter.ai/docs/guides/get-started/sovereign-ai](https://openrouter.ai/docs/guides/get-started/sovereign-ai)
- AWS Bedrock Knowledge Base citations: [docs.aws.amazon.com/bedrock/.../kb-test-retrieve-generate.html](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-test-retrieve-generate.html)
- Azure RAG reference architecture: [learn.microsoft.com/.../rag-information-retrieval](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/rag/rag-information-retrieval), [retrieval-augmented-generation-overview](https://learn.microsoft.com/en-us/azure/search/retrieval-augmented-generation-overview)
- Google grounding/citation verification: [cloud.google.com/generative-ai-app-builder/docs/check-grounding](https://cloud.google.com/generative-ai-app-builder/docs/check-grounding?hl=en)
- Citation faithfulness study: [arxiv.org/abs/2412.18004](https://arxiv.org/abs/2412.18004); entity-attribution clinical-RAG study: [arxiv.org/abs/2607.09349](https://arxiv.org/abs/2607.09349)
- RAGAS eval framework: [aclanthology.org/2024.eacl-demo.16](https://aclanthology.org/2024.eacl-demo.16/)
- Termination/governance patterns: AutoGen [termination docs](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/termination.html), OpenAI Agents SDK [run ref](https://openai.github.io/openai-agents-python/ref/run/), Anthropic [task budgets](https://platform.claude.com/docs/en/build-with-claude/task-budgets?pubDate=20260522) (advisory-only, confirmed), runaway-loop failure study [arxiv.org/abs/2607.01641](https://arxiv.org/abs/2607.01641) (preprint, not peer-reviewed)
- SQLite-backed resumable ingestion: [persist-queue PyPI](https://pypi.org/project/persist-queue/0.7.0/), [Stabilize PyPI](https://pypi.org/project/stabilize/), [DBOS document pipeline](https://docs.dbos.dev/python/examples/document-detective), [Docling](https://www.docling.ai/), SQLite [WAL](https://www.sqlite.org/wal.html) and [transactional](https://www.sqlite.org/transactional.html) docs
- Acronym/ambiguity disambiguation: [Tree of Clarifications](https://aclanthology.org/2023.emnlp-main.63/), [CLARINET](https://arxiv.org/abs/2405.15784), [disambiguation survey](https://aclanthology.org/2025.emnlp-main.482/), [acronym-gap ontology study](https://www.sciencedirect.com/science/article/pii/S002002552600112X)

## Unresolved questions

- See §6 items 1–7 (library choice, worker topology, gateway-adoption trigger, pricing-table ownership, acronym-dictionary content, locality-authority representation, coerce-vs-fail-closed policy).
- Whether `NEEDS_REVIEW` (existing unused `DecisionState`) is the right reuse target for "insufficient evidence," or whether the state-model rework already flagged in the use-case spec (§18 item 4, post-silico review ordering) should settle this together — recommend the planner decide once, not twice.

**Status:** DONE
**Summary:** Recommends 5 new modules (`lab_agent/evidence.py`, `lab_agent/grounding/acronyms.yaml`, `lab_agent/policy.py` for routing+locality+budget, `canvus_mcp/ingestion.py`, `canvus_mcp/tools/ingestion.py`) plus additive extensions to `ExperimentSetup`, `AdapterResponse`, both `Settings` classes, and `READ_TOOLS` — all fitting the existing read/write-separated, provider-neutral seams with no new external service beyond an optional standalone SQLite-backed ingestion worker. Explicitly rejects LiteLLM/OpenRouter and Celery/Kafka at current scale (YAGNI), citing sourced trade-off analysis.
**Concerns/Blockers:** None blocking research delivery. 7 owner decisions listed in §6 must be resolved before implementation (library choice, worker topology, gateway trigger, pricing source, acronym content, locality authority, coerce-vs-fail-closed policy interaction with new required schema fields).
