-- Restart-safe state for staged experiment-loop successors.
CREATE TABLE loop_continuations (
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
    updated_at REAL NOT NULL,
    PRIMARY KEY (canvas_id, loop_scope)
);

CREATE UNIQUE INDEX idx_loop_continuations_run_id
    ON loop_continuations (run_id);
CREATE INDEX idx_loop_continuations_staged_setup
    ON loop_continuations (canvas_id, staged_setup_id, round_index);
