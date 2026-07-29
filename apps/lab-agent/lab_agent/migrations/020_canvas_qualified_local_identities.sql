-- Phase 9 P7b: local durable identities are tenant/canvas qualified.
-- 014/018 made identities tenant-qualified; this additive rebuild permits identical
-- caller-controlled ids and keys on different canvases without cross-scope joins.

ALTER TABLE conflict_records RENAME TO conflict_records_legacy_020;
ALTER TABLE knowledge_versions RENAME TO knowledge_versions_legacy_020;
ALTER TABLE artifact_refs RENAME TO artifact_refs_legacy_020;
ALTER TABLE analysis_runs RENAME TO analysis_runs_legacy_020;
ALTER TABLE execution_runs RENAME TO execution_runs_legacy_020;
ALTER TABLE side_effect_intents RENAME TO side_effect_intents_legacy_020;
ALTER TABLE budget_reservations RENAME TO budget_reservations_legacy_020;

CREATE TABLE budget_reservations (
    tenant_id TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    canvas_id TEXT NOT NULL, reservation_id TEXT NOT NULL, intent_key TEXT NOT NULL,
    run_id TEXT NOT NULL, estimated_tokens INTEGER NOT NULL CHECK (estimated_tokens >= 0),
    estimated_cost_usd REAL NOT NULL CHECK (estimated_cost_usd >= 0),
    actual_tokens INTEGER CHECK (actual_tokens >= 0), actual_cost_usd REAL CHECK (actual_cost_usd >= 0),
    status TEXT NOT NULL CHECK (status IN ('reserved', 'committed', 'released')),
    created_at REAL NOT NULL, updated_at REAL NOT NULL, settled_at REAL,
    PRIMARY KEY (tenant_id, canvas_id, reservation_id),
    UNIQUE (tenant_id, canvas_id, intent_key)
);
CREATE INDEX idx_budget_reservations_scope_status ON budget_reservations (tenant_id, canvas_id, status);
CREATE INDEX idx_budget_reservations_scope_run ON budget_reservations (tenant_id, canvas_id, run_id, status);

CREATE TABLE side_effect_intents (
    tenant_id TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    canvas_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, kind TEXT NOT NULL,
    input_hash TEXT NOT NULL, status TEXT NOT NULL
        CHECK (status IN ('pending', 'submitted', 'executed', 'reconciled', 'failed')),
    external_id TEXT, attempt_count INTEGER NOT NULL DEFAULT 0, next_retry_at REAL,
    last_error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL, reconciled_at REAL,
    PRIMARY KEY (tenant_id, canvas_id, idempotency_key)
);
CREATE INDEX idx_side_effect_intents_scope_status ON side_effect_intents (tenant_id, canvas_id, status);

CREATE TABLE execution_runs (
    tenant_id TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    canvas_id TEXT NOT NULL CHECK (length(canvas_id) BETWEEN 1 AND 200),
    execution_run_id TEXT NOT NULL CHECK (length(execution_run_id) BETWEEN 1 AND 200),
    request_id TEXT NOT NULL CHECK (length(request_id) BETWEEN 1 AND 200),
    setup_id TEXT NOT NULL CHECK (length(setup_id) BETWEEN 1 AND 200),
    round_index INTEGER NOT NULL CHECK (round_index >= 0),
    proposal_hash TEXT NOT NULL CHECK (proposal_hash NOT GLOB '*[^0-9a-f]*' AND length(proposal_hash) = 64),
    validation_result_hash TEXT NOT NULL CHECK (validation_result_hash NOT GLOB '*[^0-9a-f]*' AND length(validation_result_hash) = 64),
    adapter_name TEXT NOT NULL CHECK (length(adapter_name) BETWEEN 1 AND 100),
    adapter_version TEXT NOT NULL CHECK (length(adapter_version) BETWEEN 1 AND 100),
    mode TEXT NOT NULL CHECK (mode IN ('dry_run', 'sandbox', 'real')),
    evidence_kind TEXT NOT NULL CHECK (evidence_kind IN ('mock_or_dry_run', 'measured')),
    submit_intent_key TEXT NOT NULL CHECK (length(submit_intent_key) BETWEEN 1 AND 200),
    input_hash TEXT NOT NULL CHECK (input_hash NOT GLOB '*[^0-9a-f]*' AND length(input_hash) = 64),
    abort_intent_key TEXT CHECK (abort_intent_key IS NULL OR length(abort_intent_key) BETWEEN 1 AND 200),
    provider_execution_id TEXT CHECK (provider_execution_id IS NULL OR length(provider_execution_id) BETWEEN 1 AND 200),
    status TEXT NOT NULL CHECK (status IN ('pending', 'submitted', 'running', 'reconciling', 'ambiguous', 'succeeded', 'failed', 'abort_requested', 'aborted', 'blocked')),
    failure_code TEXT CHECK (failure_code IS NULL OR failure_code IN ('timeout', 'provider_failure', 'invalid_schema', 'not_ready', 'authorization_stale', 'reconciliation_unsupported', 'retention_locality_denied', 'aborted', 'implementation_not_installed')),
    rerun_of_execution_id TEXT, created_at TEXT NOT NULL, submitted_at TEXT, finished_at TEXT,
    PRIMARY KEY (tenant_id, canvas_id, execution_run_id),
    UNIQUE (tenant_id, canvas_id, submit_intent_key), UNIQUE (tenant_id, canvas_id, abort_intent_key),
    UNIQUE (tenant_id, canvas_id, adapter_name, provider_execution_id),
    CHECK ((mode = 'real') OR evidence_kind = 'mock_or_dry_run'),
    CHECK (finished_at IS NULL OR status IN ('succeeded', 'failed', 'aborted', 'blocked')),
    FOREIGN KEY (tenant_id, canvas_id, rerun_of_execution_id)
        REFERENCES execution_runs(tenant_id, canvas_id, execution_run_id)
);
CREATE INDEX idx_execution_runs_scope_setup ON execution_runs (tenant_id, canvas_id, setup_id, created_at);
CREATE INDEX idx_execution_runs_scope_status ON execution_runs (tenant_id, canvas_id, status);

CREATE TABLE analysis_runs (
    tenant_id TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    canvas_id TEXT NOT NULL CHECK (length(canvas_id) BETWEEN 1 AND 200),
    analysis_run_id TEXT NOT NULL CHECK (length(analysis_run_id) BETWEEN 1 AND 200),
    request_id TEXT NOT NULL CHECK (length(request_id) BETWEEN 1 AND 200), execution_run_id TEXT NOT NULL,
    source_artifact_ref_ids_json TEXT NOT NULL CHECK (length(source_artifact_ref_ids_json) BETWEEN 2 AND 12000),
    adapter_name TEXT NOT NULL CHECK (length(adapter_name) BETWEEN 1 AND 100), adapter_version TEXT NOT NULL CHECK (length(adapter_version) BETWEEN 1 AND 100),
    mode TEXT NOT NULL CHECK (mode IN ('dry_run', 'sandbox', 'real')), evidence_kind TEXT NOT NULL CHECK (evidence_kind IN ('mock_or_dry_run', 'measured')),
    submit_intent_key TEXT NOT NULL CHECK (length(submit_intent_key) BETWEEN 1 AND 200), input_hash TEXT NOT NULL CHECK (input_hash NOT GLOB '*[^0-9a-f]*' AND length(input_hash) = 64),
    abort_intent_key TEXT CHECK (abort_intent_key IS NULL OR length(abort_intent_key) BETWEEN 1 AND 200), provider_job_id TEXT CHECK (provider_job_id IS NULL OR length(provider_job_id) BETWEEN 1 AND 200),
    status TEXT NOT NULL CHECK (status IN ('pending', 'submitted', 'running', 'reconciling', 'ambiguous', 'succeeded', 'failed', 'abort_requested', 'aborted', 'blocked')),
    failure_code TEXT CHECK (failure_code IS NULL OR failure_code IN ('timeout', 'provider_failure', 'invalid_schema', 'not_ready', 'authorization_stale', 'reconciliation_unsupported', 'retention_locality_denied', 'aborted', 'implementation_not_installed')),
    rerun_of_analysis_id TEXT, created_at TEXT NOT NULL, submitted_at TEXT, finished_at TEXT,
    PRIMARY KEY (tenant_id, canvas_id, analysis_run_id),
    UNIQUE (tenant_id, canvas_id, submit_intent_key), UNIQUE (tenant_id, canvas_id, abort_intent_key),
    UNIQUE (tenant_id, canvas_id, adapter_name, provider_job_id),
    CHECK ((mode = 'real') OR evidence_kind = 'mock_or_dry_run'),
    CHECK (finished_at IS NULL OR status IN ('succeeded', 'failed', 'aborted', 'blocked')),
    FOREIGN KEY (tenant_id, canvas_id, execution_run_id) REFERENCES execution_runs(tenant_id, canvas_id, execution_run_id),
    FOREIGN KEY (tenant_id, canvas_id, rerun_of_analysis_id) REFERENCES analysis_runs(tenant_id, canvas_id, analysis_run_id)
);
CREATE INDEX idx_analysis_runs_scope_execution ON analysis_runs (tenant_id, canvas_id, execution_run_id, created_at);
CREATE INDEX idx_analysis_runs_scope_status ON analysis_runs (tenant_id, canvas_id, status);

CREATE TABLE artifact_refs (
    tenant_id TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    canvas_id TEXT NOT NULL CHECK (length(canvas_id) BETWEEN 1 AND 200), artifact_ref_id TEXT NOT NULL CHECK (length(artifact_ref_id) BETWEEN 1 AND 200),
    execution_run_id TEXT, analysis_run_id TEXT, role TEXT NOT NULL CHECK (role IN ('raw', 'derived', 'log')),
    evidence_kind TEXT NOT NULL CHECK (evidence_kind IN ('mock_or_dry_run', 'measured')),
    content_hash TEXT NOT NULL CHECK (content_hash NOT GLOB '*[^0-9a-f]*' AND length(content_hash) = 64), logical_uri TEXT NOT NULL CHECK (length(logical_uri) BETWEEN 1 AND 500),
    media_type TEXT NOT NULL CHECK (length(media_type) BETWEEN 1 AND 100), classification TEXT NOT NULL CHECK (length(classification) BETWEEN 1 AND 100),
    retention_until TEXT NOT NULL, recorded_at TEXT NOT NULL, measured_receipt_json TEXT,
    PRIMARY KEY (tenant_id, canvas_id, artifact_ref_id), CHECK ((execution_run_id IS NULL) != (analysis_run_id IS NULL)),
    FOREIGN KEY (tenant_id, canvas_id, execution_run_id) REFERENCES execution_runs(tenant_id, canvas_id, execution_run_id),
    FOREIGN KEY (tenant_id, canvas_id, analysis_run_id) REFERENCES analysis_runs(tenant_id, canvas_id, analysis_run_id)
);
CREATE INDEX idx_artifact_refs_scope_execution ON artifact_refs (tenant_id, canvas_id, execution_run_id);
CREATE INDEX idx_artifact_refs_scope_analysis ON artifact_refs (tenant_id, canvas_id, analysis_run_id);

CREATE TABLE knowledge_versions (
    tenant_id TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    canvas_id TEXT NOT NULL CHECK (length(canvas_id) BETWEEN 1 AND 200), knowledge_version_id TEXT NOT NULL CHECK (length(knowledge_version_id) BETWEEN 1 AND 200),
    execution_run_id TEXT NOT NULL, analysis_run_id TEXT NOT NULL, proposal_hash TEXT NOT NULL CHECK (proposal_hash NOT GLOB '*[^0-9a-f]*' AND length(proposal_hash) = 64),
    hypothesis TEXT NOT NULL CHECK (length(hypothesis) BETWEEN 1 AND 2000), hypothesis_hash TEXT NOT NULL CHECK (hypothesis_hash NOT GLOB '*[^0-9a-f]*' AND length(hypothesis_hash) = 64),
    evidence_kind TEXT NOT NULL CHECK (evidence_kind IN ('mock_or_dry_run', 'measured')), provenance_ref_ids_json TEXT NOT NULL CHECK (length(provenance_ref_ids_json) BETWEEN 2 AND 12000),
    payload_json TEXT NOT NULL CHECK (length(payload_json) BETWEEN 1 AND 12000), content_hash TEXT NOT NULL CHECK (content_hash NOT GLOB '*[^0-9a-f]*' AND length(content_hash) = 64),
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200), created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, canvas_id, knowledge_version_id), UNIQUE (tenant_id, canvas_id, idempotency_key),
    FOREIGN KEY (tenant_id, canvas_id, execution_run_id) REFERENCES execution_runs(tenant_id, canvas_id, execution_run_id),
    FOREIGN KEY (tenant_id, canvas_id, analysis_run_id) REFERENCES analysis_runs(tenant_id, canvas_id, analysis_run_id)
);
CREATE INDEX idx_knowledge_versions_scope_created ON knowledge_versions (tenant_id, canvas_id, created_at);

CREATE TABLE conflict_records (
    tenant_id TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    canvas_id TEXT NOT NULL CHECK (length(canvas_id) BETWEEN 1 AND 200), conflict_id TEXT NOT NULL CHECK (length(conflict_id) BETWEEN 1 AND 200),
    prior_knowledge_version_id TEXT NOT NULL, proposed_knowledge_version_id TEXT NOT NULL,
    old_hypothesis_hash TEXT NOT NULL CHECK (old_hypothesis_hash NOT GLOB '*[^0-9a-f]*' AND length(old_hypothesis_hash) = 64),
    new_hypothesis_hash TEXT NOT NULL CHECK (new_hypothesis_hash NOT GLOB '*[^0-9a-f]*' AND length(new_hypothesis_hash) = 64),
    evidence_ref_ids_json TEXT NOT NULL CHECK (length(evidence_ref_ids_json) BETWEEN 2 AND 12000), reason_code TEXT NOT NULL CHECK (length(reason_code) BETWEEN 1 AND 100),
    reason_text TEXT NOT NULL CHECK (length(reason_text) BETWEEN 1 AND 2000), idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200), created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, canvas_id, conflict_id), UNIQUE (tenant_id, canvas_id, idempotency_key),
    FOREIGN KEY (tenant_id, canvas_id, prior_knowledge_version_id) REFERENCES knowledge_versions(tenant_id, canvas_id, knowledge_version_id),
    FOREIGN KEY (tenant_id, canvas_id, proposed_knowledge_version_id) REFERENCES knowledge_versions(tenant_id, canvas_id, knowledge_version_id)
);
CREATE INDEX idx_conflict_records_scope_created ON conflict_records (tenant_id, canvas_id, created_at);

INSERT INTO budget_reservations SELECT tenant_id, canvas_id, reservation_id, intent_key, run_id, estimated_tokens, estimated_cost_usd, actual_tokens, actual_cost_usd, status, created_at, updated_at, settled_at FROM budget_reservations_legacy_020;
INSERT INTO side_effect_intents SELECT tenant_id, canvas_id, idempotency_key, kind, input_hash, status, external_id, attempt_count, next_retry_at, last_error, created_at, updated_at, reconciled_at FROM side_effect_intents_legacy_020;
INSERT INTO execution_runs SELECT tenant_id, canvas_id, execution_run_id, request_id, setup_id, round_index, proposal_hash, validation_result_hash, adapter_name, adapter_version, mode, evidence_kind, submit_intent_key, input_hash, abort_intent_key, provider_execution_id, status, failure_code, rerun_of_execution_id, created_at, submitted_at, finished_at FROM execution_runs_legacy_020;
INSERT INTO analysis_runs SELECT tenant_id, canvas_id, analysis_run_id, request_id, execution_run_id, source_artifact_ref_ids_json, adapter_name, adapter_version, mode, evidence_kind, submit_intent_key, input_hash, abort_intent_key, provider_job_id, status, failure_code, rerun_of_analysis_id, created_at, submitted_at, finished_at FROM analysis_runs_legacy_020;
INSERT INTO artifact_refs SELECT tenant_id, canvas_id, artifact_ref_id, execution_run_id, analysis_run_id, role, evidence_kind, content_hash, logical_uri, media_type, classification, retention_until, recorded_at, measured_receipt_json FROM artifact_refs_legacy_020;
INSERT INTO knowledge_versions SELECT tenant_id, canvas_id, knowledge_version_id, execution_run_id, analysis_run_id, proposal_hash, hypothesis, hypothesis_hash, evidence_kind, provenance_ref_ids_json, payload_json, content_hash, idempotency_key, created_at FROM knowledge_versions_legacy_020;
INSERT INTO conflict_records SELECT tenant_id, canvas_id, conflict_id, prior_knowledge_version_id, proposed_knowledge_version_id, old_hypothesis_hash, new_hypothesis_hash, evidence_ref_ids_json, reason_code, reason_text, idempotency_key, created_at FROM conflict_records_legacy_020;

DROP TABLE conflict_records_legacy_020;
DROP TABLE knowledge_versions_legacy_020;
DROP TABLE artifact_refs_legacy_020;
DROP TABLE analysis_runs_legacy_020;
DROP TABLE execution_runs_legacy_020;
DROP TABLE side_effect_intents_legacy_020;
DROP TABLE budget_reservations_legacy_020;
