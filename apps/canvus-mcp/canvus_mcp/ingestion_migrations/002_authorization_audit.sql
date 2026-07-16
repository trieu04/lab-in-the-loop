CREATE TABLE authorization_audit (
    id INTEGER PRIMARY KEY,
    created_at REAL NOT NULL,
    subject TEXT NOT NULL,
    category TEXT NOT NULL,
    role TEXT NOT NULL,
    action TEXT NOT NULL,
    canvas_id TEXT,
    job_id INTEGER,
    asset_sha256 TEXT,
    reason TEXT NOT NULL
);
CREATE INDEX idx_authorization_audit_created ON authorization_audit(created_at);
