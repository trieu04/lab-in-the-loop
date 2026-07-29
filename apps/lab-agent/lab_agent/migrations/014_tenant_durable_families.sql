-- Phase 9: tenant-qualified identities for budget, intent, and notification ledgers.
-- Migration 013 introduced tenant_id; this migration replaces legacy global keys.

ALTER TABLE budget_reservations RENAME TO budget_reservations_legacy;
CREATE TABLE budget_reservations (
    tenant_id TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    reservation_id TEXT NOT NULL,
    intent_key TEXT NOT NULL,
    canvas_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    estimated_tokens INTEGER NOT NULL CHECK (estimated_tokens >= 0),
    estimated_cost_usd REAL NOT NULL CHECK (estimated_cost_usd >= 0),
    actual_tokens INTEGER CHECK (actual_tokens >= 0),
    actual_cost_usd REAL CHECK (actual_cost_usd >= 0),
    status TEXT NOT NULL CHECK (status IN ('reserved', 'committed', 'released')),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    settled_at REAL,
    PRIMARY KEY (tenant_id, reservation_id),
    UNIQUE (tenant_id, intent_key)
);
INSERT INTO budget_reservations
SELECT tenant_id, reservation_id, intent_key, canvas_id, run_id, estimated_tokens,
       estimated_cost_usd, actual_tokens, actual_cost_usd, status, created_at,
       updated_at, settled_at
FROM budget_reservations_legacy;
DROP TABLE budget_reservations_legacy;
CREATE INDEX idx_budget_reservations_tenant_canvas_status
    ON budget_reservations (tenant_id, canvas_id, status);
CREATE INDEX idx_budget_reservations_tenant_run_status
    ON budget_reservations (tenant_id, canvas_id, run_id, status);

ALTER TABLE side_effect_intents RENAME TO side_effect_intents_legacy;
CREATE TABLE side_effect_intents (
    tenant_id TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    idempotency_key TEXT NOT NULL,
    canvas_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('pending', 'submitted', 'executed', 'reconciled', 'failed')),
    external_id TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at REAL,
    last_error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    reconciled_at REAL,
    PRIMARY KEY (tenant_id, idempotency_key)
);
INSERT INTO side_effect_intents
SELECT tenant_id, idempotency_key, canvas_id, kind, input_hash, status, external_id,
       attempt_count, next_retry_at, last_error, created_at, updated_at, reconciled_at
FROM side_effect_intents_legacy;
DROP TABLE side_effect_intents_legacy;
CREATE INDEX idx_side_effect_intents_tenant_canvas_status
    ON side_effect_intents (tenant_id, canvas_id, status);

ALTER TABLE orchestrator_edges RENAME TO orchestrator_edges_legacy;
CREATE TABLE orchestrator_edges (
    tenant_id TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    canvas_id TEXT NOT NULL,
    connector_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    round INTEGER NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (tenant_id, canvas_id, connector_id)
);
INSERT INTO orchestrator_edges
SELECT tenant_id, canvas_id, connector_id, kind, round, created_at
FROM orchestrator_edges_legacy;
DROP TABLE orchestrator_edges_legacy;
CREATE INDEX idx_orchestrator_edges_tenant_canvas
    ON orchestrator_edges (tenant_id, canvas_id, connector_id);

ALTER TABLE notification_outbox RENAME TO notification_outbox_legacy;
CREATE TABLE notification_outbox (
    tenant_id TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    logical_key TEXT NOT NULL,
    canvas_id TEXT NOT NULL,
    closure_metadata_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'sending', 'sent', 'quarantined', 'ambiguous')),
    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 0),
    next_retry_at REAL,
    lease_owner TEXT,
    lease_expires_at REAL,
    lease_generation INTEGER NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
    reconciliation_deadline REAL,
    message_id TEXT NOT NULL,
    failure_category TEXT CHECK (failure_category IN (
        'smtp_transient', 'smtp_rejected', 'smtp_ambiguous', 'idempotency_conflict'
    )),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    sent_at REAL,
    PRIMARY KEY (tenant_id, logical_key),
    UNIQUE (tenant_id, message_id)
);
INSERT INTO notification_outbox
SELECT tenant_id, logical_key, canvas_id, closure_metadata_json, status, attempt_count,
       next_retry_at, lease_owner, lease_expires_at, lease_generation,
       reconciliation_deadline, message_id, failure_category, created_at, updated_at, sent_at
FROM notification_outbox_legacy;
DROP TABLE notification_outbox_legacy;
CREATE INDEX idx_notification_outbox_pending_retry
    ON notification_outbox (tenant_id, status, next_retry_at, created_at, logical_key)
    WHERE status = 'pending' AND next_retry_at IS NOT NULL;
CREATE INDEX idx_notification_outbox_pending_fresh
    ON notification_outbox (tenant_id, status, created_at, logical_key)
    WHERE status = 'pending' AND next_retry_at IS NULL;
CREATE INDEX idx_notification_outbox_sending_expiry
    ON notification_outbox (tenant_id, status, lease_expires_at)
    WHERE status = 'sending';
CREATE INDEX idx_notification_outbox_reconciliation
    ON notification_outbox (tenant_id, status, reconciliation_deadline, lease_expires_at);
CREATE INDEX idx_notification_outbox_tenant_canvas_status
    ON notification_outbox (tenant_id, canvas_id, status, created_at);
