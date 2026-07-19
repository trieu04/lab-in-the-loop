-- Add a durable pre-dispatch state for provider calls. Legacy pending model
-- calls are conservatively ambiguous: they may have crossed the provider
-- boundary before a crash, so upgrade them to submitted rather than redispatch.

ALTER TABLE side_effect_intents RENAME TO side_effect_intents_legacy;

CREATE TABLE side_effect_intents (
    idempotency_key TEXT PRIMARY KEY,
    canvas_id       TEXT NOT NULL,
    kind            TEXT NOT NULL,
    input_hash      TEXT NOT NULL,
    status          TEXT NOT NULL
                    CHECK (status IN ('pending','submitted','executed','reconciled','failed')),
    external_id     TEXT,
    attempt_count   INTEGER NOT NULL DEFAULT 0,
    next_retry_at   REAL,
    last_error      TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    reconciled_at   REAL
);

INSERT INTO side_effect_intents (
    idempotency_key, canvas_id, kind, input_hash, status, external_id,
    attempt_count, next_retry_at, last_error, created_at, updated_at, reconciled_at
)
SELECT
    idempotency_key, canvas_id, kind, input_hash,
    CASE WHEN kind = 'model_call' AND status = 'pending' THEN 'submitted' ELSE status END,
    external_id, attempt_count, next_retry_at, last_error, created_at, updated_at, reconciled_at
FROM side_effect_intents_legacy;

DROP TABLE side_effect_intents_legacy;

CREATE INDEX idx_side_effect_intents_canvas
    ON side_effect_intents (canvas_id, status);
