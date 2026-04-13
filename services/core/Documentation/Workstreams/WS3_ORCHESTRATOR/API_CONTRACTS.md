# WS3: ORCHESTRATOR - API Contracts

**Version:** 2.0
**Last Updated:** 2026-03-24
**Status:** Implemented (WS3 Session)
**Workstream:** WS3_ORCHESTRATOR

---

## 1. ENDPOINT: POST /api/v1/process_preview

### 1.1 Purpose

Process a queued file through the full pipeline (Phases 1-7) and return preview data. This is a **blocking call** — the HTTP connection stays open while processing runs. Called by Relay after a successful `/api/v1/enqueue`.

### 1.2 Request

**Method:** POST
**Path:** `/api/v1/process_preview`
**Content-Type:** application/json
**Authentication:** Internal service-to-service (Relay -> Core). Validated via `x-service-key` header.

```json
{
    "queue_id": "string (required — from enqueue response)",
    "data_uuid": "string (required — Relay per-request group ID, UUIDv7)"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| queue_id | string | Yes | Core queue entry ID returned by `/api/v1/enqueue` |
| data_uuid | string | Yes | Relay-assigned per-request group identifier (UUIDv7) |

### 1.3 Response: 200 OK (Completed Within Timeout)

Returned when all pipeline phases complete within the 280-second soft timeout.

```json
{
    "queue_id": "01JQXYZ123456789ABCDEF",
    "data_uuid": "01JQXYZ987654321FEDCBA",
    "status": "preview_ready",
    "statistics": {
        "total_invoices": 150,
        "valid_count": 145,
        "failed_count": 5,
        "duplicate_count": 0,
        "skipped_count": 0,
        "processing_time_ms": 45200,
        "confidence": 0.92,
        "batch_count": 2,
        "worker_count": 10
    },
    "red_flags": [
        {
            "type": "missing_hsn_code",
            "severity": "error",
            "message": "HSN code could not be determined for line item",
            "invoice_index": 2,
            "invoice_number": "INV-003",
            "field": "hsn_code",
            "phase": "enrich",
            "suggestion": "Manually enter HSN code or check product description"
        },
        {
            "type": "low_confidence_amount",
            "severity": "warning",
            "message": "Total amount has low extraction confidence (78%)",
            "invoice_index": 7,
            "invoice_number": "INV-008",
            "field": "total_amount",
            "phase": "parse",
            "suggestion": "Verify total amount against source document"
        }
    ],
    "hlx_blob_uuid": "01JQBLOB_HLX_ENCRYPTED"
}
```

#### Response Fields (200)

| Field | Type | Description |
|-------|------|-------------|
| queue_id | string | Echo of request queue_id |
| data_uuid | string | Echo of request data_uuid |
| status | string | `"preview_ready"` for normal path, `"finalized"` for immediate path |
| statistics | object | Processing statistics (see below) |
| red_flags | array | List of red flag objects (see below) |
| hlx_blob_uuid | string or null | UUID of the encrypted .hlx archive in HeartBeat blob store. `null` on immediate finalize path. |

#### statistics Object

| Field | Type | Description |
|-------|------|-------------|
| total_invoices | int | Total invoices extracted from source file(s) |
| valid_count | int | Invoices that passed all phases successfully |
| failed_count | int | Invoices that failed during processing |
| duplicate_count | int | Invoices detected as duplicates (via WS1 dedup) |
| skipped_count | int | Invoices skipped (empty rows, non-invoice pages) |
| processing_time_ms | int | Wall-clock pipeline time in milliseconds (Phase 1-7) |
| confidence | float | Overall confidence score (0.0 - 1.0), weighted average |
| batch_count | int | Number of batches the invoices were split into |
| worker_count | int | Number of concurrent workers used |

#### red_flags Array Item

| Field | Type | Description |
|-------|------|-------------|
| type | string | Red flag type code (e.g., `missing_hsn_code`, `low_confidence_amount`, `unresolved_customer`, `missing_tin`, `duplicate_suspected`, `math_mismatch`, `invalid_date`) |
| severity | string | `"error"` / `"warning"` / `"info"` |
| message | string | Human-readable description |
| invoice_index | int | Zero-based index in the invoice list |
| invoice_number | string | Original invoice number from source |
| field | string | Field that triggered the flag |
| phase | string | Pipeline phase where flag was raised (`parse`, `transform`, `enrich`, `resolve`) |
| suggestion | string | Actionable guidance for the user |

#### hlx_blob_uuid Field

The `hlx_blob_uuid` is the UUID of a single encrypted `.hlx` archive stored in HeartBeat's blob store. The .hlx contains all preview data:

- **manifest.json** — sheet list, statistics, bundle integrity
- **sheets/*.hlm** — categorized invoice sheets (submission, duplicate, late, FOC, unusual, possible B2B, failed)
- **report.json** — red flag summary, phase timings, confidence breakdown
- **metadata.json** — identity, pipeline versions, audit trail

The SDK downloads this single .hlx file, decrypts with `company_id`, unpacks, and renders in Float.

**Encryption:** AES-256-GCM with HKDF key derivation from `company_id`. Implementation in `helium_formats/hlx/crypto.py`.

**Note:** `hlx_blob_uuid` is `null` on the immediate finalize path (no preview generated).

### 1.4 Response: 202 Accepted (Exceeded Timeout)

Returned when the soft timeout (280 seconds) is reached before all phases complete. Processing continues in the background.

```json
{
    "queue_id": "01JQXYZ123456789ABCDEF",
    "data_uuid": "01JQXYZ987654321FEDCBA",
    "status": "processing",
    "message": "Processing in progress. Monitor via SSE or poll status endpoint.",
    "estimated_completion_seconds": 120,
    "phases_completed": 4,
    "phases_total": 7,
    "current_phase": "enrich",
    "progress": {
        "invoices_ready": 45,
        "invoices_total": 150
    }
}
```

#### Response Fields (202)

| Field | Type | Description |
|-------|------|-------------|
| queue_id | string | Echo of request queue_id |
| data_uuid | string | Echo of request data_uuid |
| status | string | Always `"processing"` for 202 |
| message | string | Human-readable status message |
| estimated_completion_seconds | int | Estimated seconds until completion |
| phases_completed | int | Number of phases fully completed (0-7) |
| phases_total | int | Always 7 |
| current_phase | string | Name of the phase that was running at timeout |
| progress | object | Current progress counters |

### 1.5 Response: 200 OK (Immediate Finalize Path)

Returned when `immediate_processing` is enabled and no critical red flags exist. Skips preview generation.

```json
{
    "queue_id": "01JQXYZ123456789ABCDEF",
    "data_uuid": "01JQXYZ987654321FEDCBA",
    "status": "finalized",
    "statistics": {
        "total_invoices": 150,
        "valid_count": 150,
        "failed_count": 0,
        "duplicate_count": 0,
        "skipped_count": 0,
        "processing_time_ms": 32000,
        "confidence": 0.97,
        "batch_count": 2,
        "worker_count": 10
    },
    "red_flags": [],
    "hlx_blob_uuid": null
}
```

`hlx_blob_uuid` is `null` on the immediate finalize path because no preview .hlx was generated.

### 1.6 Error Responses

#### 400 Bad Request

Missing or invalid fields in the request body.

```json
{
    "error_code": "ORCH_001",
    "message": "Invalid request: queue_id is required",
    "details": {
        "field": "queue_id",
        "reason": "missing"
    }
}
```

#### 404 Not Found

The queue_id does not exist in core_queue.

```json
{
    "error_code": "ORCH_002",
    "message": "Queue entry not found",
    "details": {
        "queue_id": "01JQXYZ123456789ABCDEF"
    }
}
```

#### 409 Conflict

The queue entry is already being processed or has already been processed.

```json
{
    "error_code": "ORCH_003",
    "message": "Queue entry already processing",
    "details": {
        "queue_id": "01JQXYZ123456789ABCDEF",
        "current_status": "processing"
    }
}
```

#### 500 Internal Server Error

Unrecoverable pipeline failure.

```json
{
    "error_code": "ORCH_004",
    "message": "Pipeline failed during parse phase",
    "details": {
        "phase": "parse",
        "error": "Textract API returned 503",
        "queue_id": "01JQXYZ123456789ABCDEF",
        "invoices_processed": 0,
        "invoices_total": 0
    }
}
```

---

## 2. ERROR CODES

| Code | Name | HTTP Status | Description |
|------|------|-------------|-------------|
| ORCH_001 | InvalidRequest | 400 | Missing/invalid request fields |
| ORCH_002 | QueueEntryNotFound | 404 | queue_id not found in core_queue |
| ORCH_003 | AlreadyProcessing | 409 | Queue entry is not in `queued` status |
| ORCH_004 | PipelineFailed | 500 | Unrecoverable phase failure |
| ORCH_005 | WorkerPoolExhausted | 503 | All workers busy, cannot accept new tasks |
| ORCH_006 | BlobUploadFailed | 500 | Preview file upload to HeartBeat failed |
| ORCH_007 | SSEEmitFailed | 500 | Failed to emit SSE event (non-fatal, logged) |
| ORCH_008 | CancellationRequested | 499 | Processing cancelled by user |

---

## 3. SSE EVENT CONTRACTS

### 3.1 processing.log

```
event: processing.log
data: {"data_uuid": "01JQXYZ987654321FEDCBA", "message": "Parsing upload.xlsx...", "level": "info"}
```

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| data_uuid | string | UUIDv7 | Identifies the processing request |
| message | string | Free-form | Human-readable status message |
| level | string | `info`, `success`, `warning`, `error` | Log level |

### 3.2 processing.progress

```
event: processing.progress
data: {"data_uuid": "01JQXYZ987654321FEDCBA", "invoices_ready": 45, "invoices_total": 150}
```

| Field | Type | Description |
|-------|------|-------------|
| data_uuid | string | Identifies the processing request |
| invoices_ready | int | Invoices that have completed all phases |
| invoices_total | int | Total invoices to process (known after parse phase) |

**Contract:** `invoices_ready` is monotonically increasing. `invoices_total` is stable after the first emission where it is non-zero.

---

## 4. INTERNAL INTERFACES (Called by WS3)

> **Updated 2026-03-24:** Signatures now match actual code (with `PipelineContext`).

### 4.1 WS1: HeartBeatBlobClient.fetch_blob

```python
async def fetch_blob(self, blob_uuid: str) -> BlobResponse:
    """Download file bytes from HeartBeat. GET /api/blobs/{blob_uuid}/download."""
```

### 4.2 WS1: Parser Registry

```python
parser = parser_registry.get(file_type)  # FileType enum
result = await parser.parse(blob_response)  # BlobResponse -> ParseResult
```

### 4.3 WS2: Transformer

```python
async def transform(self, parse_result: ParseResult, context: PipelineContext) -> TransformResult:
    """Apply Transforma scripts. Returns TransformResult(invoices, customers, inventory, red_flags)."""
```

### 4.4 WS2: Enricher

```python
async def enrich(self, transform_result: TransformResult, context: PipelineContext) -> EnrichResult:
    """Enrich via HIS. Returns EnrichResult(invoices, red_flags, api_stats)."""
```

### 4.5 WS2: Resolver

```python
async def resolve(self, enrich_result: EnrichResult, context: PipelineContext) -> ResolveResult:
    """Entity resolution. Returns ResolveResult(invoices, customers, inventory, red_flags)."""
```

### 4.6 WS0: SSE Manager

```python
async def publish(self, event: SSEEvent) -> None:
    """Publish SSE event to connected clients. SSEEvent(event_type, data, data_uuid)."""
```

### 4.7 WS1: HeartBeatBlobClient.upload_blob (NEW — added by WS3)

```python
async def upload_blob(
    self, blob_uuid: str, filename: str, data: bytes,
    content_type: str = "application/x-helium-exchange",
    company_id: str | None = None, metadata: dict | None = None,
) -> str:
    """Upload to HeartBeat. POST /api/blobs/write. Returns blob_uuid."""
```

### 4.8 WS6: PDF Overlay (STUB — WS6 not built)

```python
async def overlay_irn_qr(self, pdf_bytes: bytes, irn: str, qr_data: str) -> bytes | None:
    """Stub — returns None. WS6 not implemented."""
```

### 4.9 helium_formats: HLX Crypto (NEW — added by WS3)

```python
from helium_formats.hlx.crypto import encrypt_hlx, decrypt_hlx
encrypted = encrypt_hlx(hlx_bytes, company_id)  # AES-256-GCM
decrypted = decrypt_hlx(encrypted, company_id)   # Raises InvalidTag on wrong key
```
