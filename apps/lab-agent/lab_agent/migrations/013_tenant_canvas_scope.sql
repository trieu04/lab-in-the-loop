-- Phase 9: canonical tenant/canvas identity for the original durable harness.
-- Existing single-canvas ledgers retain their default tenant ownership.

ALTER TABLE workflow_attempts RENAME TO workflow_attempts_legacy;
CREATE TABLE workflow_attempts (
    tenant_id TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    canvas_id TEXT NOT NULL,
    trigger_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','running','completed','failed','quarantined')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_expires_at REAL,
    next_retry_at REAL,
    last_error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    completed_at REAL,
    PRIMARY KEY (tenant_id, canvas_id, trigger_id)
);
INSERT INTO workflow_attempts
SELECT 'default', canvas_id, trigger_id, status, attempt_count, lease_owner,
       lease_expires_at, next_retry_at, last_error, created_at, updated_at, completed_at
FROM workflow_attempts_legacy;
DROP TABLE workflow_attempts_legacy;
CREATE INDEX idx_workflow_attempts_due
    ON workflow_attempts (tenant_id, status, next_retry_at);

ALTER TABLE canvas_leases RENAME TO canvas_leases_legacy;
CREATE TABLE canvas_leases (
    tenant_id TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    canvas_id TEXT NOT NULL,
    runtime_instance_id TEXT NOT NULL,
    acquired_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY (tenant_id, canvas_id)
);
INSERT INTO canvas_leases
SELECT 'default', canvas_id, runtime_instance_id, acquired_at, expires_at
FROM canvas_leases_legacy;
DROP TABLE canvas_leases_legacy;

ALTER TABLE side_effect_intents ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
CREATE INDEX idx_side_effect_intents_tenant_canvas
    ON side_effect_intents (tenant_id, canvas_id, status);
ALTER TABLE orchestrator_edges ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
CREATE INDEX idx_orchestrator_edges_tenant_canvas
    ON orchestrator_edges (tenant_id, canvas_id, connector_id);
ALTER TABLE audit_events ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
CREATE INDEX idx_audit_events_tenant_canvas
    ON audit_events (tenant_id, canvas_id, sequence);
ALTER TABLE artifacts ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
CREATE INDEX idx_artifacts_tenant_canvas ON artifacts (tenant_id, canvas_id);
ALTER TABLE artifact_tokens ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE artifact_widgets ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE budget_reservations ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
CREATE INDEX idx_budget_reservations_tenant_canvas
    ON budget_reservations (tenant_id, canvas_id, status);
ALTER TABLE in_silico_results ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE gate_approvals ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE notification_outbox ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
CREATE INDEX idx_notification_outbox_tenant_canvas
    ON notification_outbox (tenant_id, canvas_id, status, created_at);
ALTER TABLE execution_runs ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE analysis_runs ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE artifact_refs ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE knowledge_versions ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE conflict_records ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE loop_continuations ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE result_generations ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';
