-- Keep terminal-event reconciliation off the full append-only audit scan path.
CREATE INDEX idx_audit_events_terminal_trigger
    ON audit_events (
        canvas_id,
        event,
        json_extract(payload_json, '$.trigger_id'),
        sequence DESC
    )
    WHERE event = 'loop_stopped';
