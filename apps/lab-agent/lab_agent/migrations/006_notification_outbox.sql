-- Durable, metadata-only notification outbox.  A row represents one logical
-- terminal closure notification, never an inbox-delivery exactly-once promise.
CREATE TABLE notification_outbox (
    logical_key TEXT PRIMARY KEY,
    canvas_id TEXT NOT NULL,
    closure_metadata_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'sending', 'sent', 'quarantined', 'ambiguous')),
    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 0),
    next_retry_at REAL,
    lease_owner TEXT,
    lease_expires_at REAL,
    lease_generation INTEGER NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
    reconciliation_deadline REAL,
    message_id TEXT NOT NULL UNIQUE,
    failure_category TEXT CHECK (failure_category IN (
        'smtp_transient', 'smtp_rejected', 'smtp_ambiguous', 'idempotency_conflict'
    )),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    sent_at REAL
);

CREATE INDEX idx_notification_outbox_due
    ON notification_outbox (status, next_retry_at, lease_expires_at);

CREATE INDEX idx_notification_outbox_canvas_status
    ON notification_outbox (canvas_id, status, created_at);
