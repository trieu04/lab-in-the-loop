-- Phase 9: tenant-qualified validation evidence and approval lineage.
-- Migration 013 assigned every existing row to the default tenant.  Rebuild the
-- ledgers so the database, rather than only application queries, prevents a
-- cross-tenant validation result from satisfying a gate approval.

ALTER TABLE gate_approvals RENAME TO gate_approvals_legacy;
ALTER TABLE in_silico_results RENAME TO in_silico_results_legacy;

CREATE TABLE in_silico_results (
    tenant_id                 TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    canvas_id                 TEXT NOT NULL,
    validation_id             TEXT NOT NULL,
    proposal_hash             TEXT NOT NULL,
    result_hash               TEXT NOT NULL,
    decision                  TEXT NOT NULL CHECK (decision IN ('proceed', 'revise', 'reject')),
    predicted_outcome         TEXT NOT NULL,
    confidence                REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    uncertainty               TEXT NOT NULL,
    assumptions_json          TEXT NOT NULL,
    risk_flags_json           TEXT NOT NULL,
    recommended_changes_json  TEXT NOT NULL,
    adapter_name              TEXT NOT NULL,
    adapter_version           TEXT NOT NULL,
    algorithm_version         TEXT NOT NULL,
    mode                      TEXT NOT NULL CHECK (mode IN ('dry_run', 'real')),
    completed_at              TEXT NOT NULL,
    result_json               TEXT NOT NULL,
    recorded_at               REAL NOT NULL,
    PRIMARY KEY (tenant_id, canvas_id, validation_id),
    UNIQUE (tenant_id, canvas_id, proposal_hash, result_hash)
);

INSERT INTO in_silico_results
SELECT tenant_id, canvas_id, validation_id, proposal_hash, result_hash, decision,
       predicted_outcome, confidence, uncertainty, assumptions_json, risk_flags_json,
       recommended_changes_json, adapter_name, adapter_version, algorithm_version,
       mode, completed_at, result_json, recorded_at
FROM in_silico_results_legacy;

CREATE TABLE gate_approvals (
    tenant_id                     TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    canvas_id                     TEXT NOT NULL,
    approval_id                   TEXT NOT NULL,
    proposal_hash                 TEXT NOT NULL,
    validation_result_hash        TEXT NOT NULL,
    actor_id                      TEXT NOT NULL,
    role                          TEXT NOT NULL CHECK (role IN ('scientist', 'lab_lead')),
    decision                      TEXT NOT NULL CHECK (decision IN ('approve', 'reject')),
    identity_provider             TEXT NOT NULL,
    identity_domain               TEXT NOT NULL,
    identity_verified_at          TEXT NOT NULL,
    production_eligible           INTEGER NOT NULL CHECK (production_eligible IN (0, 1)),
    rationale                     TEXT NOT NULL,
    decided_at                    TEXT NOT NULL,
    validation_adapter            TEXT NOT NULL,
    validation_adapter_version    TEXT NOT NULL,
    validation_algorithm_version  TEXT NOT NULL,
    approval_json                 TEXT NOT NULL,
    recorded_at                   REAL NOT NULL,
    PRIMARY KEY (tenant_id, canvas_id, approval_id),
    UNIQUE (tenant_id, canvas_id, proposal_hash, validation_result_hash, role),
    FOREIGN KEY (tenant_id, canvas_id, proposal_hash, validation_result_hash)
        REFERENCES in_silico_results (tenant_id, canvas_id, proposal_hash, result_hash)
);

INSERT INTO gate_approvals
SELECT tenant_id, canvas_id, approval_id, proposal_hash, validation_result_hash,
       actor_id, role, decision, identity_provider, identity_domain,
       identity_verified_at, production_eligible, rationale, decided_at,
       validation_adapter, validation_adapter_version, validation_algorithm_version,
       approval_json, recorded_at
FROM gate_approvals_legacy;

DROP TABLE gate_approvals_legacy;
DROP TABLE in_silico_results_legacy;

CREATE INDEX idx_in_silico_results_tenant_canvas_proposal
    ON in_silico_results (tenant_id, canvas_id, proposal_hash, completed_at, validation_id);
CREATE INDEX idx_gate_approvals_tenant_canvas_evidence
    ON gate_approvals (tenant_id, canvas_id, proposal_hash, validation_result_hash, decided_at, approval_id);
