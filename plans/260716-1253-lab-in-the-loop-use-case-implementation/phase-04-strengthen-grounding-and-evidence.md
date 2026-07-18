# Phase 4 — Strengthen Grounding & Evidence

## Context Links

- Canonical requirements: `docs/lab-in-the-loop-use-case-specification.md` (FR-LITL-001/002/015/016, NFR-LITL-004, BR-LITL-003/005, §13.4)
- Architecture: `docs/system-architecture.md` § "Target harness boundary"
- Research: `research/researcher-02-260716-1253-grounding-governance-ingestion.md`
- Decisions: `reports/decision-log-260716-1253-implementation-scope.md` (additive contracts)

## Overview

- Priority: P1
- Status: complete
- Effort: 5d
- Description: Make every generated setup traceable to retrieved internal evidence, return an explicit insufficient-evidence outcome, and surface unresolved acronyms instead of guessing. Extend contracts additively so existing MVP notes remain readable.
- Completed: 2026-07-18 (D1/D2 defects fixed & re-tempered; 314/314 tests pass)

## Key Insights

- The existing read-tool loop and `RAGCluster_` graph are reusable; this phase adds evidence capture around them rather than replacing retrieval.
- Citation integrity can be verified deterministically: every emitted citation id must exist in the per-run evidence ledger. No second model pass is needed.
- Ambiguous acronym handling needs a visible workflow result, not only prompt wording.
- Richer wiki/KG/vector retrieval remains adapter-driven; a RagCluster-only deployment must still work.

## Requirements

- FR-LITL-001: ground experiment design in internal knowledge.
- FR-LITL-002 and BR-LITL-005: produce an actionable, structured setup.
- FR-LITL-015: return explicit insufficient-evidence status without writing a misleading setup.
- FR-LITL-016 and BR-LITL-003: detect unresolved acronyms/terms, consult approved dictionaries, request clarification instead of guessing.
- NFR-LITL-004: every setup cites the internal evidence used.
- Additive target fields from §13.4: `hypothesis`, `success_criteria`, `constraints`, `confidence`, citations, evidence status, ambiguity flags.

## Architecture / Data Flow

```text
read-tool result → EvidenceLedger.add(source id, type, hash, excerpt)
                              ↓
approved acronym dictionary + retrieved context → ambiguity flags
                              ↓
model emits additive ExperimentSetup fields + citation ids
                              ↓
validate citations against ledger
  ├─ sufficient + valid → update Setup Browser artifact (Overview/Details/Evidence tabs)
  └─ insufficient/ambiguous → idempotent `[EXP:Needs Input vNNN]` Browser artifact
       keyed by (canvas, idea/setup id, reason hash); executable setup remains pending
  └─ invalid citation/schema → no canvas write; durable retry/quarantine from Phase 2
```

Evidence is carried in a structurally separated, explicitly untrusted data channel; retrieved text never becomes instructions. The per-run ledger stores bounded content in memory, while durable audit stores ids/hashes/reasons only.

## Related Code Files

- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/models/evidence.py` — `EvidenceCitation`, `AcronymFlag`, `EvidenceStatus`, and validation types.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/evidence.py` — per-run `EvidenceLedger`, citation membership validation, safe audit summaries.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/acronyms.py` — load/query an organization-approved acronym dictionary; no guessed expansion.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/resources/acronyms.json` — reviewed seed dictionary; empty/minimal by default until domain owner supplies content.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/models/experiment.py` — additive optional setup fields with backward-compatible defaults.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/tool_bridge.py` — capture successful read-tool results into `EvidenceLedger`; keep write tools blocked.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/loop.py` — thread ledger through the bounded tool loop and structured emit.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/orchestrator.py` — validate evidence sufficiency/citations before any setup write.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/prompts.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/artifact_render.py` — require and render hypothesis, constraints, success criteria, evidence, uncertainty, and ambiguity into typed tabs; keep `render.py` as legacy Note fallback.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/state_store.py` — audit evidence ids/hashes and idempotency keys for insufficiency/ambiguity requests.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/artifact_store.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/nodes.py` — create one `[EXP:Needs Input vNNN]` Browser artifact per reason hash; never label it executable.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/config.py`, `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/experiments.py`, `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/tools/experiments.py` — marker and pending/dedup semantics for clarification artifacts.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_evidence.py` and `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_acronyms.py`.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_orchestrator.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_tool_bridge.py` — grounded, insufficient, ambiguous, and invalid-citation cases.
- Modify: `/home/ntdm/dev/lap-in-the-loop/docs/lab-in-the-loop-use-case-specification.md`, `/home/ntdm/dev/lap-in-the-loop/docs/experiment-workflow.md`, `/home/ntdm/dev/lap-in-the-loop/docs/system-architecture.md`, `/home/ntdm/dev/lap-in-the-loop/docs/code-standards.md`, `/home/ntdm/dev/lap-in-the-loop/docs/project-changelog.md`.

## Implementation Steps

1. Add evidence/ambiguity models; keep new setup fields optional with defaults so old adapter fixtures and canvas notes still parse.
2. Implement `EvidenceLedger` around read-tool results. Store stable source id, tool, content hash, bounded excerpt, and metadata; never store credentials.
3. Add dictionary loading with explicit source/version metadata. An unknown acronym remains unresolved; no heuristic expansion becomes fact.
4. Package retrieved evidence in a distinct untrusted-data structure with provenance labels and bounded fields. System prompts explicitly forbid following instructions embedded in evidence.
5. Extend setup prompting and artifact rendering. Require citation ids for factual claims and an explicit evidence-sufficiency decision; display evidence/ambiguity in dedicated Browser tabs.
6. Validate emitted citations against the ledger before `nodes.create_node`. Invalid citations follow Phase 1 fail-visible and Phase 2 retry/quarantine behavior.
7. For insufficient evidence or blocking ambiguity, compute a stable reason hash and create/connect one `[EXP:Needs Input vNNN]` Browser artifact through the Phase 3 artifact store. Repeated polls update/reuse it rather than duplicate it.
8. Add read-tool-loop and adversarial tests: multi-step calls, citation spoofing, prompt injection in notes/PDF text, acronym collisions, repeated insufficient scans, and backward-compatible parsing.
9. Update source-of-truth docs and changelog; document future wiki/KG/vector adapters as external retrieval providers, not hard dependencies.

## Todo List

- [x] Evidence and ambiguity models added
- [x] Per-run `EvidenceLedger` captures read results
- [x] Approved acronym dictionary loader added
- [x] `ExperimentSetup` extended additively
- [x] Citation and sufficiency validation gates writes
- [x] Insufficient/ambiguous flows create one deduplicated needs-input artifact and leave executable setup pending
- [x] Evidence/data channel is structurally separated from instructions
- [x] Grounding, citation, prompt-injection, acronym, dedup, and compatibility tests pass
- [x] Canonical spec, workflow, architecture, standards, and changelog updated

## Success Criteria / Validation

- `cd apps/lab-agent && uv run pytest -q && uv run ruff check lab_agent tests && uv run mypy lab_agent`
- Every newly generated setup contains valid evidence citations or an explicit insufficient-evidence result.
- A citation not present in the ledger creates no setup/connector.
- Repeated insufficient-evidence polls produce exactly one current needs-input artifact per reason hash.
- Evidence containing instruction-like text cannot change tool permissions, write policy, or output schema in adversarial tests.
- Ambiguous acronyms produce visible unresolved flags and never silent guessed expansions.
- Existing MVP fixtures without new fields continue to validate and render.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Sensitive excerpts enter the ledger/audit DB | Med | High | Bound excerpts in memory; persist ids/hashes/reasons only; redact logs. |
| Optional fields stay empty and create false compliance | Med | Med | New setup generation requires evidence fields; optionality exists only for backward parsing. |
| Acronym dictionary is incomplete/stale | High | Med | Version it, expose unresolved terms, require domain-owner updates. |
| Citation ids drift across tools | Med | Med | One canonical id derivation and contract tests per read tool. |
| Repeated scans spam clarification notes | Med | Med | Stable reason hash, graph detector, durable intent, and dedup regression. |
| Retrieved content injects model instructions | Med | High | Structural data/instruction separation, provenance labels, strict tool allowlist, adversarial fixtures. |

## Security Considerations

- Apply provider/data-locality policy in Phase 4 before external retrieval sources are enabled.
- Evidence snippets are untrusted data. Keep them in a distinct structured channel, label provenance, bound length, forbid embedded-instruction execution, and test adversarial content; escaping alone is not a control.
- Audit records must exclude full internal documents, credentials, and downloaded bytes.

## Next Steps / Dependencies

- Depends on: Phase 2 durable audit/store and Phase 3 Browser artifact/store contract.
- Blocks: Phase 5 policy decisions and Phase 7 evidence-backed in-silico requests.
- External gate: organization-approved acronym dictionary and future wiki/KG/vector endpoints.
- Docs impact: major.
