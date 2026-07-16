-- Phase 8 durable execution, analysis, artifact lineage, and knowledge history.
-- This additive migration retains only safe identifiers, digests, classifications,
-- and bounded canonical payloads. It never retains bytes, credentials, raw bodies,
-- provider error strings, or capability URLs.

CREATE TABLE execution_runs (
    execution_run_id TEXT PRIMARY KEY CHECK (length(execution_run_id) BETWEEN 1 AND 200),
    canvas_id TEXT NOT NULL CHECK (length(canvas_id) BETWEEN 1 AND 200),
    request_id TEXT NOT NULL CHECK (length(request_id) BETWEEN 1 AND 200),
    setup_id TEXT NOT NULL CHECK (length(setup_id) BETWEEN 1 AND 200),
    round_index INTEGER NOT NULL CHECK (round_index >= 0),
    proposal_hash TEXT NOT NULL CHECK (proposal_hash NOT GLOB '*[^0-9a-f]*' AND length(proposal_hash) = 64),
    validation_result_hash TEXT NOT NULL CHECK (validation_result_hash NOT GLOB '*[^0-9a-f]*' AND length(validation_result_hash) = 64),
    adapter_name TEXT NOT NULL CHECK (length(adapter_name) BETWEEN 1 AND 100),
    adapter_version TEXT NOT NULL CHECK (length(adapter_version) BETWEEN 1 AND 100),
    mode TEXT NOT NULL CHECK (mode IN ('dry_run', 'sandbox', 'real')),
    evidence_kind TEXT NOT NULL CHECK (evidence_kind IN ('mock_or_dry_run', 'measured')),
    submit_intent_key TEXT NOT NULL UNIQUE CHECK (length(submit_intent_key) BETWEEN 1 AND 200),
    input_hash TEXT NOT NULL CHECK (input_hash NOT GLOB '*[^0-9a-f]*' AND length(input_hash) = 64),
    abort_intent_key TEXT UNIQUE CHECK (abort_intent_key IS NULL OR length(abort_intent_key) BETWEEN 1 AND 200),
    provider_execution_id TEXT CHECK (provider_execution_id IS NULL OR length(provider_execution_id) BETWEEN 1 AND 200),
    status TEXT NOT NULL CHECK (status IN ('pending', 'submitted', 'running', 'reconciling', 'ambiguous', 'succeeded', 'failed', 'abort_requested', 'aborted', 'blocked')),
    failure_code TEXT CHECK (failure_code IS NULL OR failure_code IN ('timeout', 'provider_failure', 'invalid_schema', 'not_ready', 'authorization_stale', 'reconciliation_unsupported', 'retention_locality_denied', 'aborted', 'implementation_not_installed')),
    rerun_of_execution_id TEXT,
    created_at TEXT NOT NULL,
    submitted_at TEXT,
    finished_at TEXT,
    CHECK ((mode = 'real') OR evidence_kind = 'mock_or_dry_run'),
    CHECK (finished_at IS NULL OR status IN ('succeeded', 'failed', 'aborted', 'blocked')),
    UNIQUE (canvas_id, execution_run_id),
    UNIQUE (adapter_name, provider_execution_id),
    FOREIGN KEY (canvas_id, rerun_of_execution_id) REFERENCES execution_runs(canvas_id, execution_run_id)
);

CREATE INDEX idx_execution_runs_canvas_setup_created
    ON execution_runs (canvas_id, setup_id, created_at);
CREATE INDEX idx_execution_runs_provider_id ON execution_runs (provider_execution_id);
CREATE INDEX idx_execution_runs_rerun ON execution_runs (rerun_of_execution_id);
CREATE INDEX idx_execution_runs_status ON execution_runs (status);

CREATE TABLE analysis_runs (
    analysis_run_id TEXT PRIMARY KEY CHECK (length(analysis_run_id) BETWEEN 1 AND 200),
    canvas_id TEXT NOT NULL CHECK (length(canvas_id) BETWEEN 1 AND 200),
    request_id TEXT NOT NULL CHECK (length(request_id) BETWEEN 1 AND 200),
    execution_run_id TEXT NOT NULL,
    source_artifact_ref_ids_json TEXT NOT NULL CHECK (length(source_artifact_ref_ids_json) BETWEEN 2 AND 12000),
    adapter_name TEXT NOT NULL CHECK (length(adapter_name) BETWEEN 1 AND 100),
    adapter_version TEXT NOT NULL CHECK (length(adapter_version) BETWEEN 1 AND 100),
    mode TEXT NOT NULL CHECK (mode IN ('dry_run', 'sandbox', 'real')),
    evidence_kind TEXT NOT NULL CHECK (evidence_kind IN ('mock_or_dry_run', 'measured')),
    submit_intent_key TEXT NOT NULL UNIQUE CHECK (length(submit_intent_key) BETWEEN 1 AND 200),
    input_hash TEXT NOT NULL CHECK (input_hash NOT GLOB '*[^0-9a-f]*' AND length(input_hash) = 64),
    abort_intent_key TEXT UNIQUE CHECK (abort_intent_key IS NULL OR length(abort_intent_key) BETWEEN 1 AND 200),
    provider_job_id TEXT CHECK (provider_job_id IS NULL OR length(provider_job_id) BETWEEN 1 AND 200),
    status TEXT NOT NULL CHECK (status IN ('pending', 'submitted', 'running', 'reconciling', 'ambiguous', 'succeeded', 'failed', 'abort_requested', 'aborted', 'blocked')),
    failure_code TEXT CHECK (failure_code IS NULL OR failure_code IN ('timeout', 'provider_failure', 'invalid_schema', 'not_ready', 'authorization_stale', 'reconciliation_unsupported', 'retention_locality_denied', 'aborted', 'implementation_not_installed')),
    rerun_of_analysis_id TEXT,
    created_at TEXT NOT NULL,
    submitted_at TEXT,
    finished_at TEXT,
    CHECK ((mode = 'real') OR evidence_kind = 'mock_or_dry_run'),
    CHECK (finished_at IS NULL OR status IN ('succeeded', 'failed', 'aborted', 'blocked')),
    UNIQUE (canvas_id, analysis_run_id),
    UNIQUE (adapter_name, provider_job_id),
    FOREIGN KEY (canvas_id, execution_run_id) REFERENCES execution_runs(canvas_id, execution_run_id),
    FOREIGN KEY (canvas_id, rerun_of_analysis_id) REFERENCES analysis_runs(canvas_id, analysis_run_id)
);

CREATE INDEX idx_analysis_runs_canvas_execution_created
    ON analysis_runs (canvas_id, execution_run_id, created_at);
CREATE INDEX idx_analysis_runs_provider_id ON analysis_runs (provider_job_id);
CREATE INDEX idx_analysis_runs_rerun ON analysis_runs (rerun_of_analysis_id);
CREATE INDEX idx_analysis_runs_status ON analysis_runs (status);

CREATE TABLE artifact_refs (
    artifact_ref_id TEXT PRIMARY KEY CHECK (length(artifact_ref_id) BETWEEN 1 AND 200),
    canvas_id TEXT NOT NULL CHECK (length(canvas_id) BETWEEN 1 AND 200),
    execution_run_id TEXT,
    analysis_run_id TEXT,
    role TEXT NOT NULL CHECK (role IN ('raw', 'derived', 'log')),
    evidence_kind TEXT NOT NULL CHECK (evidence_kind IN ('mock_or_dry_run', 'measured')),
    content_hash TEXT NOT NULL CHECK (content_hash NOT GLOB '*[^0-9a-f]*' AND length(content_hash) = 64),
    logical_uri TEXT NOT NULL CHECK (length(logical_uri) BETWEEN 1 AND 500),
    media_type TEXT NOT NULL CHECK (length(media_type) BETWEEN 1 AND 100),
    classification TEXT NOT NULL CHECK (length(classification) BETWEEN 1 AND 100),
    retention_until TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    measured_receipt_json TEXT,
    CHECK ((execution_run_id IS NULL) != (analysis_run_id IS NULL)),
    FOREIGN KEY (canvas_id, execution_run_id) REFERENCES execution_runs(canvas_id, execution_run_id),
    FOREIGN KEY (canvas_id, analysis_run_id) REFERENCES analysis_runs(canvas_id, analysis_run_id)
);

CREATE INDEX idx_artifact_refs_execution ON artifact_refs (execution_run_id);
CREATE INDEX idx_artifact_refs_analysis ON artifact_refs (analysis_run_id);
CREATE INDEX idx_artifact_refs_canvas_hash ON artifact_refs (canvas_id, content_hash);

CREATE TABLE knowledge_versions (
    knowledge_version_id TEXT PRIMARY KEY CHECK (length(knowledge_version_id) BETWEEN 1 AND 200),
    canvas_id TEXT NOT NULL CHECK (length(canvas_id) BETWEEN 1 AND 200),
    execution_run_id TEXT NOT NULL,
    analysis_run_id TEXT NOT NULL,
    proposal_hash TEXT NOT NULL CHECK (proposal_hash NOT GLOB '*[^0-9a-f]*' AND length(proposal_hash) = 64),
    hypothesis TEXT NOT NULL CHECK (length(hypothesis) BETWEEN 1 AND 2000),
    hypothesis_hash TEXT NOT NULL CHECK (hypothesis_hash NOT GLOB '*[^0-9a-f]*' AND length(hypothesis_hash) = 64),
    evidence_kind TEXT NOT NULL CHECK (evidence_kind IN ('mock_or_dry_run', 'measured')),
    provenance_ref_ids_json TEXT NOT NULL CHECK (length(provenance_ref_ids_json) BETWEEN 2 AND 12000),
    payload_json TEXT NOT NULL CHECK (length(payload_json) BETWEEN 1 AND 12000),
    content_hash TEXT NOT NULL CHECK (content_hash NOT GLOB '*[^0-9a-f]*' AND length(content_hash) = 64),
    idempotency_key TEXT NOT NULL UNIQUE CHECK (length(idempotency_key) BETWEEN 1 AND 200),
    created_at TEXT NOT NULL,
    UNIQUE (canvas_id, knowledge_version_id),
    FOREIGN KEY (canvas_id, execution_run_id) REFERENCES execution_runs(canvas_id, execution_run_id),
    FOREIGN KEY (canvas_id, analysis_run_id) REFERENCES analysis_runs(canvas_id, analysis_run_id)
);

CREATE INDEX idx_knowledge_versions_canvas_created ON knowledge_versions (canvas_id, created_at);
CREATE INDEX idx_knowledge_versions_execution ON knowledge_versions (execution_run_id);
CREATE INDEX idx_knowledge_versions_analysis ON knowledge_versions (analysis_run_id);
CREATE INDEX idx_knowledge_versions_content_hash ON knowledge_versions (content_hash);

CREATE TABLE conflict_records (
    conflict_id TEXT PRIMARY KEY CHECK (length(conflict_id) BETWEEN 1 AND 200),
    canvas_id TEXT NOT NULL CHECK (length(canvas_id) BETWEEN 1 AND 200),
    prior_knowledge_version_id TEXT NOT NULL,
    proposed_knowledge_version_id TEXT NOT NULL,
    old_hypothesis_hash TEXT NOT NULL CHECK (old_hypothesis_hash NOT GLOB '*[^0-9a-f]*' AND length(old_hypothesis_hash) = 64),
    new_hypothesis_hash TEXT NOT NULL CHECK (new_hypothesis_hash NOT GLOB '*[^0-9a-f]*' AND length(new_hypothesis_hash) = 64),
    evidence_ref_ids_json TEXT NOT NULL CHECK (length(evidence_ref_ids_json) BETWEEN 2 AND 12000),
    reason_code TEXT NOT NULL CHECK (length(reason_code) BETWEEN 1 AND 100),
    reason_text TEXT NOT NULL CHECK (length(reason_text) BETWEEN 1 AND 2000),
    idempotency_key TEXT NOT NULL UNIQUE CHECK (length(idempotency_key) BETWEEN 1 AND 200),
    created_at TEXT NOT NULL,
    FOREIGN KEY (canvas_id, prior_knowledge_version_id) REFERENCES knowledge_versions(canvas_id, knowledge_version_id),
    FOREIGN KEY (canvas_id, proposed_knowledge_version_id) REFERENCES knowledge_versions(canvas_id, knowledge_version_id)
);

CREATE INDEX idx_conflict_records_canvas_created ON conflict_records (canvas_id, created_at);
CREATE INDEX idx_conflict_records_prior ON conflict_records (prior_knowledge_version_id);
CREATE INDEX idx_conflict_records_proposed ON conflict_records (proposed_knowledge_version_id);
