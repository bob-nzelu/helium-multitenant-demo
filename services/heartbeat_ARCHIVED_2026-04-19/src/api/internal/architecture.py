"""
Architecture Metadata API (Q3)

Static JSON endpoints describing Helium's service boundaries and data flows.
Purely for demo/documentation — no database queries, no dynamic state.

These endpoints answer the demo question:
  Q3: "How do you actually keep HeartBeat, Core, Relay, and Edge in sync?"

Endpoints:
    GET /api/architecture/services    — Service boundary definitions
    GET /api/architecture/data-flows  — Blob lifecycle state machine
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/architecture", tags=["Architecture Metadata"])


# ── Static Service Definitions ─────────────────────────────────────────

SERVICES = [
    {
        "name": "HeartBeat",
        "role": "Infrastructure Hub",
        "port": 9000,
        "description": (
            "Central infrastructure service. Owns blob storage, service registry, "
            "API credentials, audit trail, config, and observability."
        ),
        "databases": ["blob.db", "registry.db", "config.db"],
        "owns": [
            "Blob storage (filesystem + metadata)",
            "Service registry & API credentials",
            "Audit trail (immutable, hash-chained)",
            "Configuration store",
            "Prometheus metrics & Wazuh security events",
            "SSE event streaming",
            "Reconciliation engine",
        ],
        "does_not_own": [
            "FIRS submission (Edge/Core)",
            "Invoice extraction (Core)",
            "Queue management (Core/Edge)",
        ],
        "deployment_modes": ["primary", "satellite"],
    },
    {
        "name": "Relay",
        "role": "Ingestion Gateway",
        "port": 8001,
        "description": (
            "Accepts files from multiple sources (bulk, NAS, ERP, email) "
            "and writes them to HeartBeat's blob storage."
        ),
        "databases": [],
        "owns": [
            "File ingestion from external sources",
            "Deduplication check (via HeartBeat)",
            "Batch grouping",
        ],
        "does_not_own": [
            "Blob storage (delegates to HeartBeat)",
            "File processing (delegates to Core)",
        ],
        "deployment_modes": ["standalone"],
    },
    {
        "name": "Core",
        "role": "Processing Engine",
        "port": 8002,
        "description": (
            "Extracts, validates, enriches invoice data from blobs. "
            "Writes processed outputs back to HeartBeat."
        ),
        "databases": ["core_queue.db"],
        "owns": [
            "Invoice extraction pipeline",
            "LLM classification (Textract + Claude/DeepSeek)",
            "Validation and enrichment",
            "Processing queue",
        ],
        "does_not_own": [
            "Blob storage (reads from HeartBeat)",
            "FIRS submission (delegates to Edge)",
        ],
        "deployment_modes": ["standalone"],
    },
    {
        "name": "Edge",
        "role": "Compliance Gateway",
        "port": 8003,
        "description": (
            "Handles FIRS e-invoicing submission, receipt validation, "
            "and compliance status tracking."
        ),
        "databases": ["edge_queue.db"],
        "owns": [
            "FIRS BIS 3.0 submission",
            "Receipt validation",
            "Compliance status tracking",
            "Edge queue (outbound to FIRS)",
        ],
        "does_not_own": [
            "Invoice extraction (Core)",
            "Blob storage (HeartBeat)",
        ],
        "deployment_modes": ["standalone"],
    },
    {
        "name": "Float",
        "role": "User Interface",
        "port": None,
        "description": (
            "Desktop application (PySide6) providing invoice preview, "
            "approval workflow, and dashboard."
        ),
        "databases": ["sync.db (local)"],
        "owns": [
            "User interface and interactions",
            "Local sync cache",
            "Preview/approval workflow",
        ],
        "does_not_own": [
            "Server-side processing",
            "Blob storage",
        ],
        "deployment_modes": ["desktop"],
    },
    {
        "name": "HIS",
        "role": "Helium Intelligence Service",
        "port": 8004,
        "description": (
            "Product classification intelligence. Manages HS/ISIC code lookups, "
            "keyword rules, and semantic search via embeddings."
        ),
        "databases": ["his_base.db", "his_tenant_{id}.db"],
        "owns": [
            "HS code / ISIC code intelligence",
            "Keyword rule matching",
            "Semantic embedding search (all-MiniLM-L6-v2)",
            "Nigerian commodity/service enrichments",
        ],
        "does_not_own": [
            "Product extraction (Core)",
            "Blob storage (HeartBeat)",
        ],
        "deployment_modes": ["standalone"],
    },
]


# ── Static Data Flow Definitions ──────────────────────────────────────

DATA_FLOWS = {
    "blob_lifecycle": {
        "description": "State machine for blob processing from ingestion to finalization",
        "states": [
            {"state": "uploaded", "description": "Blob written to storage by Relay, awaiting Core pickup"},
            {"state": "processing", "description": "Core is extracting/validating/enriching the blob"},
            {"state": "preview_pending", "description": "Processing complete, awaiting user approval in Float"},
            {"state": "finalized", "description": "User approved, blob processing complete"},
            {"state": "error", "description": "Processing failed at some stage"},
        ],
        "transitions": [
            {"from": "uploaded", "to": "processing", "trigger": "Core picks up blob from queue"},
            {"from": "processing", "to": "preview_pending", "trigger": "Core finishes extraction"},
            {"from": "processing", "to": "error", "trigger": "Extraction/validation failure"},
            {"from": "preview_pending", "to": "finalized", "trigger": "User approves in Float"},
            {"from": "preview_pending", "to": "error", "trigger": "User rejects in Float"},
        ],
    },
    "ingestion_flow": {
        "description": "How files enter the system",
        "steps": [
            {"step": 1, "actor": "External Source", "action": "File arrives (bulk upload, NAS, ERP, email)"},
            {"step": 2, "actor": "Relay", "action": "Accepts file, computes SHA256 hash"},
            {"step": 3, "actor": "Relay → HeartBeat", "action": "POST /api/dedup/check (is this file a duplicate?)"},
            {"step": 4, "actor": "Relay → HeartBeat", "action": "POST /api/blobs/write (store file on filesystem)"},
            {"step": 5, "actor": "Relay → HeartBeat", "action": "POST /api/blobs/register (record metadata in DB)"},
            {"step": 6, "actor": "Relay → HeartBeat", "action": "POST /api/dedup/record (mark hash as seen)"},
            {"step": 7, "actor": "Relay → HeartBeat", "action": "POST /api/audit/log (immutable audit event)"},
        ],
    },
    "sync_protocol": {
        "description": "How services stay in sync without shared databases",
        "mechanisms": [
            {
                "mechanism": "API Contracts",
                "description": "Each service exposes RESTful APIs. No direct DB access across service boundaries.",
            },
            {
                "mechanism": "SSE Event Streaming",
                "description": "HeartBeat publishes blob.* events via SSE. Services subscribe for real-time updates.",
            },
            {
                "mechanism": "Service Registry",
                "description": "Services self-register with HeartBeat on startup. HeartBeat knows all active instances.",
            },
            {
                "mechanism": "Config Store",
                "description": "Centralized config in HeartBeat's config.db. Services fetch config via API.",
            },
            {
                "mechanism": "Audit Trail",
                "description": "All cross-service actions are logged to HeartBeat's immutable audit trail.",
            },
            {
                "mechanism": "Cache Refresh",
                "description": "HeartBeat broadcasts cache invalidation via SSE when config/reference data changes.",
            },
        ],
    },
}


# ── Endpoints ──────────────────────────────────────────────────────────

@router.get("/services")
async def get_services():
    """
    Service boundary definitions for all Helium services.

    Static metadata — describes what each service owns and doesn't own.
    """
    return {
        "services": SERVICES,
        "count": len(SERVICES),
        "note": "Static metadata — describes service boundaries for architecture documentation",
    }


@router.get("/data-flows")
async def get_data_flows():
    """
    Data flow definitions — blob lifecycle, ingestion flow, sync protocol.

    Static metadata — describes how data moves through the Helium platform.
    """
    return {
        "flows": DATA_FLOWS,
        "count": len(DATA_FLOWS),
        "note": "Static metadata — describes data flows for architecture documentation",
    }
