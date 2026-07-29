-- Phase 9: tenant/canvas identities for result generations and continuations.
-- Migrations 013--018 already added tenant_id with a default for legacy rows.

ALTER TABLE loop_continuations RENAME TO loop_continuations_legacy;
CREATE TABLE loop_continuations (
    tenant_id TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    canvas_id TEXT NOT NULL CHECK (length(canvas_id) BETWEEN 1 AND 200),
    loop_scope TEXT NOT NULL CHECK (length(loop_scope) BETWEEN 1 AND 200),
    run_id TEXT NOT NULL CHECK (length(run_id) BETWEEN 1 AND 200),
    started_at REAL NOT NULL,
    round_index INTEGER NOT NULL CHECK (round_index >= 1),
    observed_round INTEGER NOT NULL DEFAULT 0 CHECK (observed_round >= 0),
    previous_result_signature TEXT NOT NULL DEFAULT '' CHECK (length(previous_result_signature) <= 64),
    no_progress_streak INTEGER NOT NULL DEFAULT 0 CHECK (no_progress_streak >= 0),
    predecessor_setup_id TEXT NOT NULL CHECK (length(predecessor_setup_id) BETWEEN 1 AND 200),
    predecessor_result_id TEXT NOT NULL CHECK (length(predecessor_result_id) BETWEEN 1 AND 200),
    staged_setup_id TEXT NOT NULL DEFAULT '' CHECK (length(staged_setup_id) <= 200),
    staged_result_id TEXT NOT NULL DEFAULT '' CHECK (length(staged_result_id) <= 200),
    staged_proposal_hash TEXT NOT NULL DEFAULT '' CHECK (
        staged_proposal_hash = '' OR
        (staged_proposal_hash NOT GLOB '*[^0-9a-f]*' AND length(staged_proposal_hash) = 64)
    ),
    branch_setup_id TEXT NOT NULL DEFAULT '' CHECK (length(branch_setup_id) <= 200),
    updated_at REAL NOT NULL,
    PRIMARY KEY (tenant_id, canvas_id, loop_scope)
);
INSERT INTO loop_continuations
SELECT tenant_id, canvas_id, loop_scope, run_id, started_at, round_index,
       observed_round, previous_result_signature, no_progress_streak,
       predecessor_setup_id, predecessor_result_id, staged_setup_id,
       staged_result_id, staged_proposal_hash, branch_setup_id, updated_at
FROM loop_continuations_legacy;
DROP TABLE loop_continuations_legacy;
CREATE UNIQUE INDEX idx_loop_continuations_tenant_canvas_run
    ON loop_continuations (tenant_id, canvas_id, run_id);
CREATE INDEX idx_loop_continuations_tenant_canvas_staged_setup
    ON loop_continuations (tenant_id, canvas_id, staged_setup_id, round_index);
CREATE INDEX idx_loop_continuations_tenant_canvas_branch_setup
    ON loop_continuations (tenant_id, canvas_id, branch_setup_id);

ALTER TABLE result_generations RENAME TO result_generations_legacy;
CREATE TABLE result_generations (
    tenant_id TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
    canvas_id TEXT NOT NULL CHECK (length(canvas_id) BETWEEN 1 AND 200),
    setup_id TEXT NOT NULL CHECK (length(setup_id) BETWEEN 1 AND 200),
    round_index INTEGER NOT NULL CHECK (round_index >= 1),
    proposal_hash TEXT NOT NULL CHECK (proposal_hash NOT GLOB '*[^0-9a-f]*' AND length(proposal_hash) = 64),
    validation_result_hash TEXT NOT NULL CHECK (validation_result_hash NOT GLOB '*[^0-9a-f]*' AND length(validation_result_hash) = 64),
    result_hash TEXT NOT NULL CHECK (result_hash NOT GLOB '*[^0-9a-f]*' AND length(result_hash) = 64),
    result_json TEXT NOT NULL CHECK (length(result_json) BETWEEN 2 AND 20000),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (tenant_id, canvas_id, setup_id, round_index, proposal_hash, validation_result_hash)
);
INSERT INTO result_generations
SELECT tenant_id, canvas_id, setup_id, round_index, proposal_hash,
       validation_result_hash, result_hash, result_json, created_at, updated_at
FROM result_generations_legacy;
DROP TABLE result_generations_legacy;
CREATE INDEX idx_result_generations_tenant_canvas_setup
    ON result_generations (tenant_id, canvas_id, setup_id, round_index);
