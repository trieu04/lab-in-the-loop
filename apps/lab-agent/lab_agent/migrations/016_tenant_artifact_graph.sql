-- Phase 9: rebuild the artifact persistence graph with tenant-qualified keys.
-- Legacy artifact rows retain their Migration 013 tenant ownership (normally default).

ALTER TABLE artifacts RENAME TO artifacts_legacy;
ALTER TABLE artifact_versions RENAME TO artifact_versions_legacy;
ALTER TABLE artifact_tokens RENAME TO artifact_tokens_legacy;
ALTER TABLE artifact_widgets RENAME TO artifact_widgets_legacy;

CREATE TABLE artifacts (
    tenant_id       TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    opaque_id       TEXT NOT NULL,
    canvas_id       TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    artifact_type   TEXT NOT NULL,
    state           TEXT NOT NULL,
    round           INTEGER NOT NULL DEFAULT 0,
    current_version INTEGER NOT NULL DEFAULT 1,
    content_hash    TEXT NOT NULL,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    PRIMARY KEY (tenant_id, opaque_id),
    UNIQUE (tenant_id, canvas_id, idempotency_key)
);
CREATE INDEX idx_artifacts_tenant_canvas_v2 ON artifacts (tenant_id, canvas_id);

CREATE TABLE artifact_versions (
    tenant_id       TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    opaque_id       TEXT NOT NULL,
    version         INTEGER NOT NULL,
    payload_json    TEXT NOT NULL,
    metadata_json   TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    created_at      REAL NOT NULL,
    PRIMARY KEY (tenant_id, opaque_id, version),
    FOREIGN KEY (tenant_id, opaque_id)
        REFERENCES artifacts (tenant_id, opaque_id) ON DELETE CASCADE
);

CREATE TABLE artifact_tokens (
    tenant_id  TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    token_hash TEXT NOT NULL,
    opaque_id  TEXT NOT NULL,
    canvas_id  TEXT NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('active', 'revoked', 'rotated')),
    created_at REAL NOT NULL,
    revoked_at REAL,
    PRIMARY KEY (tenant_id, token_hash),
    FOREIGN KEY (tenant_id, opaque_id)
        REFERENCES artifacts (tenant_id, opaque_id) ON DELETE CASCADE
);
CREATE INDEX idx_artifact_tokens_tenant_opaque_status
    ON artifact_tokens (tenant_id, opaque_id, status);
CREATE UNIQUE INDEX idx_artifact_tokens_one_active_v2
    ON artifact_tokens (tenant_id, opaque_id) WHERE status = 'active';

CREATE TABLE artifact_widgets (
    tenant_id  TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    opaque_id  TEXT NOT NULL,
    canvas_id  TEXT NOT NULL,
    widget_id  TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (tenant_id, opaque_id),
    UNIQUE (tenant_id, canvas_id, widget_id),
    FOREIGN KEY (tenant_id, opaque_id)
        REFERENCES artifacts (tenant_id, opaque_id) ON DELETE CASCADE
);

INSERT INTO artifacts
SELECT tenant_id, opaque_id, canvas_id, idempotency_key, artifact_type, state,
       round, current_version, content_hash, created_at, updated_at
FROM artifacts_legacy;
INSERT INTO artifact_versions
SELECT a.tenant_id, v.opaque_id, v.version, v.payload_json, v.metadata_json,
       v.provenance_json, v.content_hash, v.created_at
FROM artifact_versions_legacy AS v
JOIN artifacts_legacy AS a ON a.opaque_id = v.opaque_id;
INSERT INTO artifact_tokens
SELECT a.tenant_id, t.token_hash, t.opaque_id, t.canvas_id, t.status,
       t.created_at, t.revoked_at
FROM artifact_tokens_legacy AS t
JOIN artifacts_legacy AS a ON a.opaque_id = t.opaque_id;
INSERT INTO artifact_widgets
SELECT a.tenant_id, w.opaque_id, w.canvas_id, w.widget_id, w.created_at, w.updated_at
FROM artifact_widgets_legacy AS w
JOIN artifacts_legacy AS a ON a.opaque_id = w.opaque_id;

DROP TABLE artifact_widgets_legacy;
DROP TABLE artifact_tokens_legacy;
DROP TABLE artifact_versions_legacy;
DROP TABLE artifacts_legacy;
