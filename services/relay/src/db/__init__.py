"""
Relay-side durable storage.

The single intentional exception to the all-PG mantra: ``relay.db`` is a
per-tenant SQLite file holding the durable ingest ledger. See
``services/relay/Documentation/FRONTDOOR_ARCHITECTURE.md`` §2 (why SQLite)
and §6 (schema). Do NOT migrate this to PostgreSQL — it is by design.
"""

from .ledger import IngestLedger, LedgerRow

__all__ = ["IngestLedger", "LedgerRow"]
