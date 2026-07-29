-- Keep each bounded notification selector on an indexable predicate/order.
CREATE INDEX idx_notification_outbox_pending_retry
    ON notification_outbox (status, next_retry_at, created_at, logical_key)
    WHERE status = 'pending' AND next_retry_at IS NOT NULL;

CREATE INDEX idx_notification_outbox_pending_fresh
    ON notification_outbox (status, created_at, logical_key)
    WHERE status = 'pending' AND next_retry_at IS NULL;

CREATE INDEX idx_notification_outbox_sending_expiry
    ON notification_outbox (status, lease_expires_at)
    WHERE status = 'sending';
