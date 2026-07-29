-- Phase 9: preserve the legacy v1 ledger and add tenant-qualified v2 chains.
-- No defaults are intentional: stale writers without these fields must fail closed.

ALTER TABLE audit_events RENAME TO audit_events_legacy_v1;
CREATE TABLE audit_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id     TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    hash_version  INTEGER NOT NULL CHECK (hash_version IN (1, 2)),
    sequence      INTEGER NOT NULL CHECK (sequence >= 1),
    canvas_id     TEXT NOT NULL,
    round         INTEGER,
    event         TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    event_hash    TEXT NOT NULL,
    created_at    REAL NOT NULL,
    UNIQUE (tenant_id, sequence)
);
INSERT INTO audit_events (
    id, tenant_id, hash_version, sequence, canvas_id, round, event, payload_json,
    previous_hash, event_hash, created_at
)
SELECT
    id, 'default', 1, sequence, canvas_id, round, event, payload_json,
    previous_hash, event_hash, created_at
FROM audit_events_legacy_v1;
DROP TABLE audit_events_legacy_v1;
CREATE INDEX idx_audit_events_tenant_canvas_sequence
    ON audit_events (tenant_id, canvas_id, sequence);
CREATE INDEX idx_audit_events_terminal_trigger
    ON audit_events (
        tenant_id,
        canvas_id,
        event,
        json_extract(payload_json, '$.trigger_id'),
        sequence DESC
    )
    WHERE event = 'loop_stopped';
