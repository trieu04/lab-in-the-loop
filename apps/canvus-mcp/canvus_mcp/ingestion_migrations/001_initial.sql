CREATE TABLE assets (
    sha256 TEXT PRIMARY KEY CHECK (length(sha256) = 64),
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    mime_type TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE sources (
    id INTEGER PRIMARY KEY,
    canvas_id TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    asset_sha256 TEXT NOT NULL REFERENCES assets(sha256),
    classification TEXT,
    created_at REAL NOT NULL,
    UNIQUE(canvas_id, source_ref, asset_sha256)
);
CREATE INDEX idx_sources_access ON sources(canvas_id, source_ref, asset_sha256);

CREATE TABLE jobs (
    id INTEGER PRIMARY KEY,
    asset_sha256 TEXT NOT NULL REFERENCES assets(sha256),
    extractor_version TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued','running','completed','failed','cancelled')),
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0,1)),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(asset_sha256, extractor_version)
);
CREATE INDEX idx_jobs_status ON jobs(status, updated_at);

CREATE TABLE units (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    unit_kind TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    start_at INTEGER NOT NULL DEFAULT 0,
    end_at INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('planned','retry','leased','completed','cancelled','poison')),
    generation INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at REAL,
    failure_code TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(job_id, ordinal)
);
CREATE INDEX idx_units_claim ON units(status, next_retry_at, id);

CREATE TABLE leases (
    unit_id INTEGER PRIMARY KEY REFERENCES units(id) ON DELETE CASCADE,
    token TEXT NOT NULL UNIQUE,
    generation INTEGER NOT NULL,
    owner TEXT NOT NULL,
    expires_at REAL NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX idx_leases_expiry ON leases(expires_at);

CREATE TABLE attempts (
    id INTEGER PRIMARY KEY,
    unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    generation INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running','completed','failed','expired','cancelled')),
    failure_code TEXT,
    started_at REAL NOT NULL,
    finished_at REAL,
    UNIQUE(unit_id, generation)
);

CREATE TABLE chunks (
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(job_id, ordinal)
);

CREATE TABLE cancellations (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    requested_at REAL NOT NULL
);
