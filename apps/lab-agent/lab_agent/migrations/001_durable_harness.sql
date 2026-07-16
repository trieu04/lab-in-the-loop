-- Durable harness ledger (Phase 2). Canvas remains workflow truth; this
-- ledger records attempts, leases, side-effect intents, edges, and audit
-- evidence only -- never a second competing state machine.
--
-- `schema_migrations` itself is bootstrapped by lab_agent.state.connection
-- before this file runs (it must exist to track which versions applied).

CREATE TABLE workflow_attempts (
    canvas_id        TEXT NOT NULL,
    trigger_id       TEXT NOT NULL,
    status           TEXT NOT NULL
                      CHECK (status IN ('pending','running','completed','failed','quarantined')),
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    lease_owner      TEXT,
    lease_expires_at REAL,
    next_retry_at    REAL,
    last_error       TEXT,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL,
    completed_at     REAL,
    PRIMARY KEY (canvas_id, trigger_id)
);

CREATE INDEX idx_workflow_attempts_due
    ON workflow_attempts (status, next_retry_at);

CREATE TABLE canvas_leases (
    canvas_id           TEXT PRIMARY KEY,
    runtime_instance_id TEXT NOT NULL,
    acquired_at         REAL NOT NULL,
    expires_at          REAL NOT NULL
);

CREATE TABLE side_effect_intents (
    idempotency_key TEXT PRIMARY KEY,
    canvas_id       TEXT NOT NULL,
    kind            TEXT NOT NULL,
    input_hash      TEXT NOT NULL,
    status          TEXT NOT NULL
                    CHECK (status IN ('pending','executed','reconciled','failed')),
    external_id     TEXT,
    attempt_count   INTEGER NOT NULL DEFAULT 0,
    next_retry_at   REAL,
    last_error      TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    reconciled_at   REAL
);

CREATE INDEX idx_side_effect_intents_canvas
    ON side_effect_intents (canvas_id, status);

CREATE TABLE orchestrator_edges (
    canvas_id    TEXT NOT NULL,
    connector_id TEXT NOT NULL,
    kind         TEXT NOT NULL,
    round        INTEGER NOT NULL,
    created_at   REAL NOT NULL,
    PRIMARY KEY (canvas_id, connector_id)
);

CREATE TABLE audit_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sequence      INTEGER NOT NULL UNIQUE,
    canvas_id     TEXT NOT NULL,
    round         INTEGER,
    event         TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    event_hash    TEXT NOT NULL,
    created_at    REAL NOT NULL
);

CREATE INDEX idx_audit_events_canvas
    ON audit_events (canvas_id, sequence);
