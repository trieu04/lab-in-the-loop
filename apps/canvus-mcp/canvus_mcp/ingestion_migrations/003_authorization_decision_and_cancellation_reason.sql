ALTER TABLE authorization_audit ADD COLUMN decision TEXT NOT NULL DEFAULT 'denied';
ALTER TABLE cancellations ADD COLUMN reason TEXT;
