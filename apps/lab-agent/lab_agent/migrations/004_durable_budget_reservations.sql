-- Persist budget holds before provider dispatch so process restarts cannot bypass
-- per-run or per-canvas limits. Amounts only; prompts and provider bodies stay out.

CREATE TABLE budget_reservations (
    reservation_id          TEXT PRIMARY KEY,
    intent_key              TEXT NOT NULL UNIQUE,
    canvas_id               TEXT NOT NULL,
    run_id                  TEXT NOT NULL,
    estimated_tokens        INTEGER NOT NULL CHECK (estimated_tokens >= 0),
    estimated_cost_usd      REAL NOT NULL CHECK (estimated_cost_usd >= 0),
    actual_tokens           INTEGER CHECK (actual_tokens >= 0),
    actual_cost_usd         REAL CHECK (actual_cost_usd >= 0),
    status                  TEXT NOT NULL CHECK (status IN ('reserved', 'committed', 'released')),
    created_at              REAL NOT NULL,
    updated_at              REAL NOT NULL,
    settled_at              REAL
);

CREATE INDEX idx_budget_reservations_canvas_status
    ON budget_reservations (canvas_id, status);

CREATE INDEX idx_budget_reservations_run_status
    ON budget_reservations (canvas_id, run_id, status);
