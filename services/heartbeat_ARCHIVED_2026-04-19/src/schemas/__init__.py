"""
Canonical Schema Registry — HeartBeat-hosted single source of truth.

HeartBeat owns and serves canonical database schemas for all Helium services.
Each schema is a versioned SQL file in databases/schemas/.

Services fetch schemas via:
  1. HTTP API:  GET /api/schemas/invoices  (returns SQL text + version metadata)
  2. Python:    from src.schemas import invoice_schema  (direct import for co-located code)

Schema governance:
  - Documentation/Schema/invoice/ docs define the SPEC (human + AI readable).
  - databases/schemas/*.sql files are the IMPLEMENTATION (runtime-accessible SQL).
  - Change propagation: Docs → HeartBeat canonical SQL → Core schema → SDK schema → SDK models.
  - Never add fields downstream without updating the canonical SQL first.

Current schemas:
  - invoices_canonical_v2.sql  →  invoice_schema  (invoices + line items + history + views)
"""

from .registry import (
    SchemaRegistry,
    get_schema_registry,
    reset_schema_registry,
    invoice_schema,
    get_invoice_schema_sql,
    get_invoice_schema_version,
)
from .notifier import (
    SchemaNotifier,
    get_schema_notifier,
    init_schema_notifier,
    reset_schema_notifier,
)

__all__ = [
    "SchemaRegistry",
    "get_schema_registry",
    "reset_schema_registry",
    "invoice_schema",
    "get_invoice_schema_sql",
    "get_invoice_schema_version",
    "SchemaNotifier",
    "get_schema_notifier",
    "init_schema_notifier",
    "reset_schema_notifier",
]
