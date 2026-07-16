-- Immutable validation and human-approval evidence.  JSON columns retain the
-- complete typed contracts; indexed columns support current-evidence projection.
-- Identity metadata is limited to provider/domain/actor/role; no authentication
-- material, token, or secret may be written to this ledger.

CREATE TABLE in_silico_results (
    validation_id          TEXT NOT NULL,
    canvas_id              TEXT NOT NULL,
    proposal_hash          TEXT NOT NULL,
    result_hash            TEXT NOT NULL,
    decision               TEXT NOT NULL CHECK (decision IN ('proceed', 'revise', 'reject')),
    predicted_outcome      TEXT NOT NULL,
    confidence             REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    uncertainty            TEXT NOT NULL,
    assumptions_json       TEXT NOT NULL,
    risk_flags_json        TEXT NOT NULL,
    recommended_changes_json TEXT NOT NULL,
    adapter_name           TEXT NOT NULL,
    adapter_version        TEXT NOT NULL,
    algorithm_version      TEXT NOT NULL,
    mode                   TEXT NOT NULL CHECK (mode IN ('dry_run', 'real')),
    completed_at           TEXT NOT NULL,
    result_json            TEXT NOT NULL,
    recorded_at            REAL NOT NULL,
    PRIMARY KEY (canvas_id, validation_id)
);

CREATE INDEX idx_in_silico_results_canvas_proposal
    ON in_silico_results (canvas_id, proposal_hash, completed_at, validation_id);

CREATE INDEX idx_in_silico_results_current
    ON in_silico_results (canvas_id, proposal_hash, result_hash);

CREATE TABLE gate_approvals (
    approval_id            TEXT NOT NULL,
    canvas_id              TEXT NOT NULL,
    proposal_hash          TEXT NOT NULL,
    validation_result_hash TEXT NOT NULL,
    actor_id               TEXT NOT NULL,
    role                   TEXT NOT NULL CHECK (role IN ('scientist', 'lab_lead')),
    decision               TEXT NOT NULL CHECK (decision IN ('approve', 'reject')),
    identity_provider      TEXT NOT NULL,
    identity_domain        TEXT NOT NULL,
    identity_verified_at   TEXT NOT NULL,
    production_eligible    INTEGER NOT NULL CHECK (production_eligible IN (0, 1)),
    rationale              TEXT NOT NULL,
    decided_at             TEXT NOT NULL,
    validation_adapter     TEXT NOT NULL,
    validation_adapter_version TEXT NOT NULL,
    validation_algorithm_version TEXT NOT NULL,
    approval_json          TEXT NOT NULL,
    recorded_at            REAL NOT NULL,
    PRIMARY KEY (canvas_id, approval_id),
    UNIQUE (canvas_id, proposal_hash, validation_result_hash, role)
);

CREATE INDEX idx_gate_approvals_canvas_evidence
    ON gate_approvals (canvas_id, proposal_hash, validation_result_hash, decided_at, approval_id);
