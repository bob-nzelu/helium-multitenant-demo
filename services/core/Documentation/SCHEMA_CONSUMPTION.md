# Core Service — Canonical Schema Consumption

## Principle
Core fetches the canonical invoice schema from HeartBeat at startup. HeartBeat is the single source of truth for all database schemas.

## Startup Flow
1. Core calls `GET /api/schemas/invoices` from HeartBeat
2. Compares response `version` against local `schema_version` table in invoices.db
3. If HeartBeat version is newer → apply migration (see Migration Strategy below)
4. If HeartBeat is unreachable → use local fallback: `Core/schemas/sqlite/invoices_sqlite.sql`

## Callback Endpoint
Core MUST expose: `POST /internal/schema-refresh`

HeartBeat calls this endpoint when the canonical schema changes (fire-and-forget).

Payload:
```json
{
  "schema_name": "invoices",
  "old_version": "2.0",
  "new_version": "2.1",
  "fetch_url": "/api/schemas/invoices/sql",
  "timestamp": "2026-02-25T14:30:00Z"
}
```

On receiving this callback, Core should:
1. Fetch the new schema: `GET {heartbeat_base_url}/api/schemas/invoices/sql`
2. Compare version against current
3. Apply migration if newer (or log warning if migration not yet implemented)

## Migration Strategy
- For now: **Log warning** when schema version mismatch is detected
- Future: ALTER TABLE / migration scripts (to be designed in dedicated Core session)
- Core's PostgreSQL schema (`schemas/postgres/invoices.sql`) is already aligned with canonical v2.0
- Core's SQLite schema (`schemas/sqlite/invoices_sqlite.sql`) needs update — 20+ missing fields, 2 missing tables (see canonical v2.0 for truth)

## Known Gap (as of 2026-02-25)
Core's current SQLite schema (`schemas/sqlite/invoices_sqlite.sql`) claims v2.0 in its header but is actually v1.0 content:
- Missing fields: helium_invoice_no, payment_status, direction, document_type, transaction_type, all inbound_* fields (7), trace_id, csid, csid_status, and more
- Missing tables: invoice_references, invoice_transmission_attempts
- Old enum values: 'DRAFT' should be 'COMMITTED', 'SUBMITTED' should be transmission states
- Old field names: invoice_date should be issue_date, status should be workflow_status

This will be resolved in a dedicated Core schema alignment session.

## Related Documentation
- Canonical schema: `Documentation/Schema/invoice/06_INVOICES_DB_CANONICAL_SCHEMA_V2.sql`
- HeartBeat schema API: `GET /api/schemas/invoices`, `GET /api/schemas/invoices/sql`
- SDK schema sync: `Documentation/Schema/invoice/SDK_SCHEMA_SYNC.md`
- Schema notification architecture: `Documentation/Schema/invoice/README.md`
