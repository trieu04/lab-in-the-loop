-- Bound stale notification reconciliation during large SMTP backlogs.
CREATE INDEX idx_notification_outbox_reconciliation
    ON notification_outbox (status, reconciliation_deadline, lease_expires_at);
