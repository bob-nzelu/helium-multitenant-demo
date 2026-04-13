# CORE INTEGRATION REQUIREMENTS FOR RELAY PHASE 1B

**Version**: 1.0
**Last Updated**: 2026-01-31
**Status**: REQUIREMENTS FOR CORE TEAM
**Target Audience**: Core service implementation team

---

## DOCUMENT PURPOSE

This document specifies the **exact API contracts** that Core must implement to integrate with Relay Bulk Upload (Phase 1B).

Relay Phase 1B has been implemented with **mock/placeholder responses** for Core API calls. The Core team must implement these endpoints matching the specifications below.

---

## CRITICAL: MOCK RESPONSES IN PHASE 1B

Phase 1B implementation uses **placeholder Core responses**. These are the structures Relay expects:

### Current Mock Response Structure

```python
# In CoreAPIClient.process_preview() - Phase 1B returns this structure:
{
    "queue_id": "queue_123",
    "status": "processed",  # or "queued"
    "statistics": {
        "total_invoices": 150,
        "valid_count": 145,
        "failed_count": 5,
        "duplicate_count": 0,
        "processing_time_seconds": 12.5,
        "total_revenue": 0.0,
        "total_tax": 0.0,
        "red_flags": [
            {
                "type": "missing_hsn_code",
                "invoice_id": "INV_003",
                "severity": "error",
                "message": "HSN code could not be determined"
            }
        ]
    },
    "preview_data": {
        "firs_invoices_url": "/api/blob/{uuid}/firs_invoices.json",
        "report_url": "/api/blob/{uuid}/report.json",
        "customers_url": "/api/blob/{uuid}/customers.json",
        "inventory_url": "/api/blob/{uuid}/inventory.json",
        "failed_invoices_url": "/api/blob/{uuid}/failed_invoices.xlsx",
        "fixed_pdf_url": "/api/blob/{uuid}/fixed.pdf"
    }
}
```

**Core team MUST implement endpoints that return this EXACT structure.**

---

## REQUIRED CORE API ENDPOINTS

Core must implement these 3 endpoints for Relay integration:

### 1. POST /api/v1/enqueue

**Purpose**: Queue a file for processing

**Called by**: Relay (after blob write)

**Request**:
```json
{
    "file_uuid": "550e8400-e29b-41d4-a716-446655440000",
    "blob_path": "/files_blob/550e8400-...-bulk.zip",
    "original_filename": "invoice1.pdf",
    "source": "relay-bulk",
    "immediate_processing": false
}
```

**Response** (200 OK):
```json
{
    "queue_id": "queue_123",
    "status": "queued",
    "message": "File queued for processing"
}
```

**Response** (500 Internal Server Error):
```json
{
    "error": "CORE_ENQUEUE_FAILED",
    "message": "Failed to enqueue file: {reason}"
}
```

---

### 2. POST /api/v1/process_preview

**Purpose**: Process file for preview (blocking call, up to 300 seconds timeout)

**Called by**: Relay (after enqueue)

**Request**:
```json
{
    "queue_id": "queue_123"
}
```

**Response** (200 OK - Processing Complete):
```json
{
    "queue_id": "queue_123",
    "status": "processed",
    "statistics": {
        "total_invoices": 150,
        "valid_count": 145,
        "failed_count": 5,
        "duplicate_count": 0,
        "processing_time_seconds": 12.5,
        "total_revenue": 45000.00,
        "total_tax": 3150.00,
        "red_flags": [
            {
                "type": "missing_hsn_code",
                "invoice_id": "INV_003",
                "severity": "error",
                "message": "HSN code could not be determined"
            },
            {
                "type": "suspicious_amount",
                "invoice_id": "INV_007",
                "severity": "warning",
                "message": "Amount exceeds typical range for this customer"
            }
        ]
    },
    "preview_data": {
        "firs_invoices_url": "/api/blob/{uuid}/firs_invoices.json",
        "report_url": "/api/blob/{uuid}/report.json",
        "customers_url": "/api/blob/{uuid}/customers.json",
        "inventory_url": "/api/blob/{uuid}/inventory.json",
        "failed_invoices_url": "/api/blob/{uuid}/failed_invoices.xlsx",
        "fixed_pdf_url": "/api/blob/{uuid}/fixed.pdf"
    }
}
```

**Response** (202 Accepted - Still Processing):
```json
{
    "queue_id": "queue_123",
    "status": "processing",
    "message": "Processing in progress. Check status later."
}
```

**Response** (503 Service Unavailable - Core Down):
```json
{
    "error": "SERVICE_UNAVAILABLE",
    "message": "Core processing service temporarily unavailable"
}
```

**Timeout Handling**:
- Relay waits up to **300 seconds** (5 minutes) for response
- If Core doesn't respond within 300 seconds, Relay returns "queued" status to user
- Large batches (1000+ invoices) may exceed timeout - this is expected behavior

---

### 3. POST /api/v1/finalize

**Purpose**: Finalize previewed invoices with user edits

**Called by**: Relay (after user confirmation)

**Request**:
```json
{
    "queue_id": "queue_123",
    "edits": {
        "invoice_edits": {
            "137861": {
                "accounting_supplier_party": {
                    "party_name": "Updated Company Name",
                    "tin": "12345678"
                }
            },
            "137863": {
                "issue_date": "2026-01-15"
            }
        },
        "customer_edits": {
            "customer_001": {
                "party_name": "Corrected Customer Name"
            }
        },
        "inventory_edits": {
            "item_005": {
                "hsn_code": "998311"
            }
        }
    }
}
```

**Response** (200 OK):
```json
{
    "queue_id": "queue_123",
    "status": "finalized",
    "statistics": {
        "invoices_processed": 145,
        "invoices_failed": 0,
        "edge_queue_entries": 145
    }
}
```

**Response** (400 Bad Request - Invalid Edits):
```json
{
    "error": "INVALID_EDITS",
    "message": "Edit validation failed",
    "details": [
        {
            "invoice_id": "137861",
            "field": "accounting_supplier_party.tin",
            "error": "TIN must be 8 digits"
        }
    ]
}
```

---

## RED FLAGS TAXONOMY

Core must classify issues into these red flag types:

| Type | Severity | Description | Example |
|------|----------|-------------|---------|
| `missing_hsn_code` | error | HSN code could not be determined | Item description too vague |
| `missing_supplier_tin` | error | Supplier TIN is missing | Required field empty |
| `invalid_date_format` | error | Date format is invalid | "2026-31-01" instead of ISO 8601 |
| `suspicious_amount` | warning | Amount exceeds typical range | Invoice for $1M when avg is $100 |
| `duplicate_invoice_number` | warning | Invoice number already exists | Same number as previous upload |
| `missing_customer_details` | warning | Customer information incomplete | No address or contact |
| `invalid_tax_calculation` | error | Tax calculation does not match | Computed tax differs from stated |
| `unsupported_currency` | error | Currency not supported | Currency other than NGN |

**Note**: Core team can extend this taxonomy as needed. Relay will pass all red flags through to Float UI.

---

## PREVIEW DATA BLOB STRUCTURE

Core must write these files to blob storage (7-day retention):

### 1. firs_invoices.json

FIRS-compliant invoice data in JSON format.

**Structure**:
```json
[
    {
        "id": "137861",
        "invoice_number": "INV-2026-001",
        "issue_date": "2026-01-15",
        "accounting_supplier_party": {
            "party_name": "ExecuJet Aviation Nigeria Limited",
            "tin": "12345678"
        },
        "accounting_customer_party": {
            "party_name": "Customer Name",
            "tin": "87654321"
        },
        "invoice_lines": [
            {
                "id": "1",
                "item_description": "Flight services",
                "quantity": 1,
                "unit_price": 45000.00,
                "tax_total": 3150.00,
                "line_extension_amount": 45000.00
            }
        ],
        "tax_total": 3150.00,
        "legal_monetary_total": 48150.00
    }
]
```

### 2. report.json

Processing statistics and summary.

**Structure**:
```json
{
    "total_invoices": 150,
    "valid_count": 145,
    "failed_count": 5,
    "total_revenue": 450000.00,
    "total_tax": 31500.00,
    "processing_time_seconds": 12.5,
    "red_flags_summary": {
        "error_count": 5,
        "warning_count": 3
    }
}
```

### 3. customers.json

Extracted customer master data.

**Structure**:
```json
[
    {
        "customer_id": "customer_001",
        "party_name": "Customer Name",
        "tin": "87654321",
        "address": "123 Main St, Lagos",
        "email": "contact@customer.com"
    }
]
```

### 4. inventory.json

Extracted inventory/product data.

**Structure**:
```json
[
    {
        "item_id": "item_001",
        "item_description": "Flight services",
        "hsn_code": "998311",
        "unit_price": 45000.00
    }
]
```

### 5. failed_invoices.xlsx

Excel file with failed invoices and error details (for user download).

**Columns**:
- Invoice Number
- Issue Date
- Customer Name
- Error Code
- Error Message
- Suggested Fix

### 6. fixed.pdf (optional)

Corrected/fixed invoices in PDF format (if applicable).

---

## TIMEOUT AND GRACEFUL DEGRADATION

### Timeout Behavior

Relay sets a **300-second (5-minute) timeout** for `process_preview()`:

```python
core_response = await asyncio.wait_for(
    self.core_client.process_preview(queue_id),
    timeout=300  # 5 minutes
)
```

**If Core exceeds 300 seconds**:
- Relay returns "queued" status to Float UI
- User sees: "Processing in progress. Large batch may take several minutes."
- User can poll `/api/status/{queue_id}` later (to be implemented in Phase 1C)

**Core Implementation Requirements**:
1. Process small batches (<100 invoices) within 300 seconds
2. For large batches (1000+ invoices), return 202 Accepted immediately
3. Continue processing in background
4. Implement `/api/status/{queue_id}` for status polling

### Graceful Degradation (Core Unavailable)

**If Core is down during enqueue**:
- Relay returns error to user
- File is written to blob but NOT queued
- HeartBeat reconciliation will detect orphaned blob

**If Core is down during process_preview**:
- Relay catches `CoreUnavailableError`
- Returns "queued" status to user
- Core will process when it comes back up

**Expected Core Behavior**:
- On startup, scan `core_queue` for unprocessed entries
- Resume processing any queued files

---

## DEDUPLICATION INTEGRATION

Relay performs **preliminary deduplication** but relies on Core for **persistent dedup check**.

### Deduplication Flow

1. **Relay Session Cache** (in-memory, current batch):
   - Relay checks SHA256 hash against local cache
   - Catches duplicates within same upload

2. **HeartBeat Persistent Check** (cross-session):
   - Relay calls HeartBeat API: `POST /api/duplicate/check`
   - HeartBeat queries Core's `processed_files` table

3. **Core Final Check** (during processing):
   - Core checks `processed_files` table again
   - If duplicate found during processing, mark as duplicate in response

**Core Requirements**:
- Maintain `processed_files` table with SHA256 hashes
- Return duplicate status in `process_preview()` response
- Include `original_queue_id` for duplicate files

---

## ERROR HANDLING REQUIREMENTS

### Core Must Return Structured Errors

All Core error responses must follow this format:

```json
{
    "error": "ERROR_CODE",
    "message": "Human-readable error message",
    "details": [
        {
            "field": "field_name",
            "error": "Specific error for this field"
        }
    ]
}
```

### Core Error Codes

| Error Code | HTTP Status | Description |
|------------|-------------|-------------|
| `CORE_ENQUEUE_FAILED` | 500 | Failed to write to core_queue |
| `BLOB_NOT_FOUND` | 404 | Blob file not found in storage |
| `INVALID_FILE_FORMAT` | 400 | File format not supported |
| `PROCESSING_FAILED` | 500 | Processing pipeline failed |
| `INVALID_EDITS` | 400 | User edits failed validation |
| `SERVICE_UNAVAILABLE` | 503 | Core temporarily unavailable |

---

## AUDIT LOGGING REQUIREMENTS

Core must log these events to `audit.db`:

### 1. File Processing Started

```json
{
    "event_type": "core.processing.started",
    "queue_id": "queue_123",
    "file_uuid": "550e8400-...",
    "blob_path": "/files_blob/550e8400-...-bulk.zip"
}
```

### 2. File Processing Completed

```json
{
    "event_type": "core.processing.completed",
    "queue_id": "queue_123",
    "total_invoices": 150,
    "valid_count": 145,
    "failed_count": 5,
    "processing_time_seconds": 12.5
}
```

### 3. File Processing Failed

```json
{
    "event_type": "core.processing.failed",
    "queue_id": "queue_123",
    "error": "PROCESSING_FAILED",
    "error_message": "Unexpected error during extraction"
}
```

### 4. Batch Finalized

```json
{
    "event_type": "core.finalization.completed",
    "queue_id": "queue_123",
    "invoices_processed": 145,
    "edge_queue_entries": 145
}
```

---

## TESTING REQUIREMENTS

Core team must test these scenarios:

### Unit Tests

- [ ] Enqueue file → returns queue_id
- [ ] Process preview → returns statistics + preview_data
- [ ] Finalize with edits → applies edits and creates invoices
- [ ] Duplicate file → returns duplicate status

### Integration Tests

- [ ] Relay → Core → HeartBeat flow (end-to-end)
- [ ] Large batch (1000+ invoices) → timeout handling
- [ ] Core down → graceful degradation
- [ ] Invalid edits → validation error response

### Performance Tests

- [ ] Small batch (10 invoices) → <10 seconds
- [ ] Medium batch (100 invoices) → <60 seconds
- [ ] Large batch (1000 invoices) → <300 seconds or return 202 Accepted

---

## PHASE 1C INTEGRATION

During Phase 1C, OPUS will:
1. Validate Core implementation matches these specs
2. Write integration tests for Relay ↔ Core flow
3. Replace mock responses with real Core API calls
4. Test end-to-end upload flow with Float UI

**Core team must complete these endpoints BEFORE Phase 1C begins.**

---

## CONTACT & SUPPORT

- **Relay Team**: relay-team@prodeus.com
- **Core Team**: core-team@prodeus.com
- **Integration Issues**: Submit to GitLab issue tracker

---

**This document is BINDING for Core team implementation. Any deviations must be discussed with Relay team.**

---

## IQC MODULE — IRN + QR + CSID (Added 2026-03-25)

**CRITICAL: Read this section before implementing any IRN/QR/CSID logic.**

Relay needs IRN generation and QR code encoding for Edge transmission to FIRS.
These functions are in the **shared `helium_formats.iqc` module**, NOT in Transforma.

### What Relay CAN import

```python
from helium_formats.iqc import generate_irn, compute_irn_hash   # IRN
from helium_formats.iqc import generate_qr_data, encode_tlv      # QR
from helium_formats.iqc import generate_csid_request, validate_csid_response  # CSID
```

### What Relay CANNOT import

```python
# WRONG — Transforma is proprietary to Core. HeartBeat will return 403.
# from transforma.qr.generator import ...
# from transforma import ...
```

Relay's `pyproject.toml` must depend on `helium_formats`, NOT `transforma`.
Any `import transforma` in Relay code is a security violation.

### HeartBeat IQC Config Fetch

Relay fetches IQC config from HeartBeat on startup and caches locally:

```python
# Relay startup
iqc_config = await heartbeat_client.fetch_script(
    tenant_id, script_category="IQC"
)
# Cache locally. Use for IRN generation + QR encoding on Edge payloads.
```

### HeartBeat Push Notification

HeartBeat calls Relay's webhook when IQC config changes:

```python
@app.post("/config-updated")
async def on_config_updated(payload):
    if payload["category"] == "IQC":
        iqc_config = await heartbeat_client.fetch_script(
            tenant_id, script_category="IQC"
        )
        update_local_cache(iqc_config)
```

No hash checksum is needed for IQC — it's a shared format spec, not proprietary.
The push notification ensures currency.

### Access Control Summary

| Script Category | Relay Access | Core Access | Hash Checksum |
|----------------|-------------|-------------|---------------|
| **IQC** (IRN/QR/CSID) | YES | YES | No |
| **TRANSFORMA** (tenant scripts) | **NO — 403 Forbidden** | YES | Yes (SHA-256) |

---

**Last Updated**: 2026-03-25
