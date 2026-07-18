-- Canonical generated-artifact ledger (Phase 3). Canvus Browser widgets are
-- a capability-protected *view* over this data; this migration does not
-- alter any table from 001_durable_harness.sql.

CREATE TABLE artifacts (
    opaque_id        TEXT PRIMARY KEY,
    canvas_id        TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL,
    artifact_type    TEXT NOT NULL,
    state            TEXT NOT NULL,
    round            INTEGER NOT NULL DEFAULT 0,
    current_version  INTEGER NOT NULL DEFAULT 1,
    content_hash     TEXT NOT NULL,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL
);

CREATE INDEX idx_artifacts_canvas
    ON artifacts (canvas_id);

-- Restart-idempotent creation: a crash after create_artifact but before the
-- caller records success must not mint a second canonical artifact on
-- retry. ArtifactStore.get_or_create_artifact looks up by this key before
-- creating; this UNIQUE index is the DB-level backstop against a
-- concurrent or replayed duplicate slipping past that check.
CREATE UNIQUE INDEX idx_artifacts_canvas_idempotency
    ON artifacts (canvas_id, idempotency_key);

-- Append-only: application code never UPDATEs or DELETEs a version row.
CREATE TABLE artifact_versions (
    opaque_id       TEXT NOT NULL REFERENCES artifacts(opaque_id) ON DELETE CASCADE,
    version         INTEGER NOT NULL,
    payload_json    TEXT NOT NULL,
    metadata_json   TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    created_at      REAL NOT NULL,
    PRIMARY KEY (opaque_id, version)
);

-- Bearer-capability tokens gating Browser reads. Only the SHA-256 hash of
-- the token is ever stored; the raw token exists only in memory at
-- issue/rotate time (lab_agent.state.artifact_tokens).
CREATE TABLE artifact_tokens (
    token_hash TEXT PRIMARY KEY,
    opaque_id  TEXT NOT NULL REFERENCES artifacts(opaque_id) ON DELETE CASCADE,
    canvas_id  TEXT NOT NULL,
    status     TEXT NOT NULL
               CHECK (status IN ('active','revoked','rotated')),
    created_at REAL NOT NULL,
    revoked_at REAL
);

CREATE INDEX idx_artifact_tokens_opaque_status
    ON artifact_tokens (opaque_id, status);

-- At most one active token per artifact -- application code rotates any
-- existing active token out before minting a replacement (issue_token,
-- rotate_token), and this partial unique index is the DB-level backstop.
CREATE UNIQUE INDEX idx_artifact_tokens_one_active
    ON artifact_tokens (opaque_id)
    WHERE status = 'active';

-- One Browser widget per artifact; a given widget id is claimed by at most
-- one artifact within a canvas (canvas-scoped uniqueness).
CREATE TABLE artifact_widgets (
    opaque_id  TEXT PRIMARY KEY REFERENCES artifacts(opaque_id) ON DELETE CASCADE,
    canvas_id  TEXT NOT NULL,
    widget_id  TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (canvas_id, widget_id)
);
