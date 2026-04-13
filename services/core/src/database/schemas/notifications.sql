-- ============================================================================
-- WS6 Observability: Notifications Schema
-- ============================================================================
-- Date:    2026-03-26
-- Status:  System, business, approval, and report notifications
-- See:     WS6_HANDOFF_NOTE.md
-- ============================================================================

CREATE TABLE IF NOT EXISTS notifications.notifications (
    notification_id     TEXT PRIMARY KEY,
    company_id          TEXT NOT NULL,
    recipient_id        TEXT,
    notification_type   TEXT NOT NULL,
    category            TEXT NOT NULL,
    title               TEXT NOT NULL,
    body                TEXT NOT NULL,
    priority            TEXT DEFAULT 'normal',
    data                JSONB,
    created_at          TIMESTAMPTZ DEFAULT now(),
    expires_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_notif_company
    ON notifications.notifications(company_id);

CREATE INDEX IF NOT EXISTS idx_notif_recipient
    ON notifications.notifications(recipient_id);

CREATE INDEX IF NOT EXISTS idx_notif_created
    ON notifications.notifications(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_notif_type
    ON notifications.notifications(notification_type);


CREATE TABLE IF NOT EXISTS notifications.notification_reads (
    read_id             TEXT PRIMARY KEY,
    notification_id     TEXT NOT NULL REFERENCES notifications.notifications(notification_id),
    read_by             TEXT NOT NULL,
    read_at             TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_notif_reads_notification
    ON notifications.notification_reads(notification_id);

CREATE INDEX IF NOT EXISTS idx_notif_reads_user
    ON notifications.notification_reads(read_by);
