-- ============================================================================
-- WS6 Observability: Audit Log Table
-- ============================================================================
-- Date:    2026-03-26
-- Status:  Core cross-cutting audit trail
-- See:     WS6_HANDOFF_NOTE.md
-- ============================================================================

CREATE TABLE IF NOT EXISTS core.audit_log (
    audit_id        TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    entity_id       TEXT,
    action          TEXT NOT NULL,
    actor_id        TEXT,
    actor_type      TEXT DEFAULT 'user',
    company_id      TEXT NOT NULL DEFAULT '',
    x_trace_id      TEXT,
    before_state    JSONB,
    after_state     JSONB,
    changed_fields  TEXT[],
    metadata        JSONB,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_entity
    ON core.audit_log(entity_type, entity_id);

CREATE INDEX IF NOT EXISTS idx_audit_actor
    ON core.audit_log(actor_id);

CREATE INDEX IF NOT EXISTS idx_audit_company
    ON core.audit_log(company_id);

CREATE INDEX IF NOT EXISTS idx_audit_created
    ON core.audit_log(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_trace
    ON core.audit_log(x_trace_id);
