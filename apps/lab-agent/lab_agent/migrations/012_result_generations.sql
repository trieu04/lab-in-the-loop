-- Durable typed ExperimentResult payloads before Canvas projection.
ALTER TABLE loop_continuations ADD COLUMN branch_setup_id TEXT NOT NULL DEFAULT '';
UPDATE loop_continuations SET branch_setup_id=predecessor_setup_id WHERE branch_setup_id='';
CREATE INDEX idx_loop_continuations_branch_setup
    ON loop_continuations (canvas_id, branch_setup_id);

CREATE TABLE result_generations (
    canvas_id TEXT NOT NULL CHECK (length(canvas_id) BETWEEN 1 AND 200),
    setup_id TEXT NOT NULL CHECK (length(setup_id) BETWEEN 1 AND 200),
    round_index INTEGER NOT NULL CHECK (round_index >= 1),
    proposal_hash TEXT NOT NULL CHECK (proposal_hash NOT GLOB '*[^0-9a-f]*' AND length(proposal_hash) = 64),
    validation_result_hash TEXT NOT NULL CHECK (validation_result_hash NOT GLOB '*[^0-9a-f]*' AND length(validation_result_hash) = 64),
    result_hash TEXT NOT NULL CHECK (result_hash NOT GLOB '*[^0-9a-f]*' AND length(result_hash) = 64),
    result_json TEXT NOT NULL CHECK (length(result_json) BETWEEN 2 AND 20000),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (canvas_id, setup_id, round_index, proposal_hash, validation_result_hash)
);
CREATE INDEX idx_result_generations_canvas_setup
    ON result_generations (canvas_id, setup_id, round_index);
