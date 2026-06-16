# Helium Relay — External API Reference

**Version**: 1.0 (2026-06-16)
**Audience**: External systems (ERP software, accounting platforms, business applications) integrating directly with the Helium invoice submission pipeline.
**Base URL**: `http://<your-relay-host>:8082`
**Status**: Live on EC2 at `http://13.247.224.147:8082`

> This document covers the **external / API caller path** only. Float desktop (bulk upload) uses a separate internal path. If you are integrating a third-party ERP or accounting system, you are on the right document.

---

## Table of Contents

1. [Quickstart — Submit Your First Invoice](#1-quickstart--submit-your-first-invoice)
2. [Authentication](#2-authentication)
3. [Endpoints](#3-endpoints)
   - [POST /api/ingest — Submit Invoice](#31-post-apiingest--submit-invoice)
   - [POST /api/finalize — Finalize by Reference](#32-post-apifinalize--finalize-by-reference)
   - [POST /api/artifacts/fetch — Fetch Artifact](#33-post-apiartifactsfetch--fetch-artifact)
   - [GET /health — Health Check](#34-get-health--health-check)
4. [Response Shapes](#4-response-shapes)
5. [Error Codes](#5-error-codes)
6. [Rate Limits](#6-rate-limits)
7. [Version Drift Headers (Optional)](#7-version-drift-headers-optional)
8. [End-to-End Flow Diagram](#8-end-to-end-flow-diagram)
9. [SDK Examples](#9-sdk-examples)

---

## 1. Quickstart — Submit Your First Invoice

This is the minimal sequence to submit an invoice and get back an IRN + QR code:

```bash
# 1. Compute the HMAC signature (see §2 for full explanation)
API_KEY="ab_prod_a1b2c3d4e5f6..."
API_SECRET="your-api-secret"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
BODY_HASH=$(sha256sum invoice.pdf | awk '{print $1}')
MESSAGE="${API_KEY}:${TIMESTAMP}:${BODY_HASH}"
SIGNATURE=$(echo -n "$MESSAGE" | openssl dgst -sha256 -hmac "$API_SECRET" | awk '{print $2}')

# 2. Submit the invoice
curl -X POST http://13.247.224.147:8082/api/ingest \
  -H "X-API-Key: $API_KEY" \
  -H "X-Timestamp: $TIMESTAMP" \
  -H "X-Signature: $SIGNATURE" \
  -F "files=@invoice.pdf;type=application/pdf" \
  -F "call_type=external" \
  -F 'invoice_data_json={"supplier_name":"Acme Ltd","invoice_number":"INV-2026-001","amount":150000}'
```

**Success response (HTTP 200):**

```json
{
  "status": "processed",
  "data_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "queue_id": "queue-7f3d4a1e-...",
  "filenames": ["invoice.pdf"],
  "file_count": 1,
  "file_hash": "a3f5d7b2...",
  "file_uuids": ["blob-a1b2c3-..."],
  "trace_id": "trc-f9e8d7c6-...",
  "irn": "HELIUM-2026-001-NGN-A3F5D7",
  "qr_code": "iVBORw0KGgoAAAANSUhEUgAA..."
}
```

The `irn` is your Invoice Reference Number. The `qr_code` is the Base64-encoded QR payload to stamp onto your invoice PDF.

---

## 2. Authentication

All mutating endpoints (`/api/ingest`, `/api/finalize`, `/api/artifacts/fetch`) require **HMAC-SHA256** authentication. Health check is public.

### 2.1 Required Headers

| Header | Example | Description |
|--------|---------|-------------|
| `X-API-Key` | `ab_prod_a1b2c3...` | API key provided by your Helium tenant admin |
| `X-Timestamp` | `2026-06-16T10:00:00Z` | ISO 8601 UTC timestamp (current time) |
| `X-Signature` | `3a4f5b6c...` | HMAC-SHA256 hex signature (see below) |

### 2.2 Signature Computation

```
body_hash  = SHA256(raw_request_body_bytes)           ← hex string
message    = "{api_key}:{timestamp}:{body_hash}"
signature  = HMAC-SHA256(api_secret, message)         ← hex string
```

**For multipart/form-data requests** (`/api/ingest`): `body_hash` is SHA256 of the **entire raw multipart body** as received by the server. Read the file and form fields into the body bytes, then hash the whole body. Do not hash individual fields.

**For JSON body requests** (`/api/finalize`, `/api/artifacts/fetch`): `body_hash` is SHA256 of the JSON string bytes.

**Timestamp window**: The server rejects requests with a timestamp older than **5 minutes** from server time. This prevents replay attacks.

### 2.3 Python Example

```python
import hashlib
import hmac
import time
from datetime import datetime, timezone

def compute_signature(api_key: str, api_secret: str, body_bytes: bytes) -> tuple[str, str]:
    """Returns (timestamp, signature) for use in X-Timestamp and X-Signature headers."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    message = f"{api_key}:{timestamp}:{body_hash}"
    signature = hmac.new(
        api_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return timestamp, signature
```

### 2.4 API Key Format

```
{2-letter-prefix}_{env}_{32-hex-chars}

Examples:
  ab_prod_a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6
  zy_dev_0011223344556677889900aabbccddeeff
```

Your API key and secret are provisioned by your Helium tenant administrator and scoped to your company and environment.

> **OAuth 2.0 Note**: A machine-to-machine OAuth 2.0 flow (client credentials) is on the roadmap. When available, it will be an alternative to HMAC signing. Current external integrations use HMAC.

---

## 3. Endpoints

### 3.1 `POST /api/ingest` — Submit Invoice

The primary endpoint for external system invoice submission. Accepts one PDF invoice, runs it through the compliance pipeline, and returns an IRN + QR code.

**Request**: `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `files` | File (PDF) | Yes | Invoice PDF. One file per request for external callers. Max 10 MB. |
| `call_type` | String | Yes | Must be `"external"` |
| `invoice_data_json` | String | No | JSON string with invoice metadata (see below) |
| `metadata` | String | No | JSON string with caller identity fields (see below) |

**`invoice_data_json` fields** (all optional, used for audit + compliance enrichment):

```json
{
  "supplier_name": "Acme Ltd",
  "invoice_number": "INV-2026-001",
  "amount": 150000,
  "currency": "NGN",
  "buyer_name": "Buyer Corp",
  "invoice_date": "2026-06-16"
}
```

**`metadata` fields** (all optional, used for tracing):

```json
{
  "user_trace_id": "your-system-trace-uuid",
  "helium_user_id": "usr-your-system-user-id"
}
```

**HMAC body signing**: Sign the full raw multipart body (not just the file bytes).

**Success Response (HTTP 200)**:

```json
{
  "status": "processed",
  "data_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "queue_id": "queue-7f3d4a1e-b3c2-...",
  "filenames": ["invoice.pdf"],
  "file_count": 1,
  "file_hash": "a3f5d7b2c1e4f8a9...",
  "file_uuids": ["blob-a1b2c3d4-..."],
  "trace_id": "trc-f9e8d7c6-...",
  "irn": "HELIUM-2026-001-NGN-A3F5D7",
  "qr_code": "iVBORw0KGgoAAAANSUhEUgAA..."
}
```

**Status values**:

| Status | Meaning | What to do |
|--------|---------|------------|
| `processed` | Invoice accepted, IRN generated, QR returned. | Store `irn`, stamp `qr_code` onto your invoice PDF. |
| `queued` | Invoice stored but backend processing is still pending. IRN/QR not yet available. | Retry later via polling (use `queue_id` with Core's status endpoint). |
| `error` | Submission failed. | Inspect `error_code` and `message`. Fix and retry. |

**Finalize flag** (optional, advanced): To request immediate fiscalization during ingest, include `"finalize": true` in the `metadata` JSON:

```json
{ "finalize": true, "user_trace_id": "my-trace-id" }
```

This runs ingest + finalize as a single atomic operation (#2 combined call). If omitted, finalize is a separate step via `POST /api/finalize` (#3 call).

---

**cURL Example**:

```bash
curl -X POST http://13.247.224.147:8082/api/ingest \
  -H "X-API-Key: ab_prod_a1b2c3d4e5f6..." \
  -H "X-Timestamp: 2026-06-16T10:00:00Z" \
  -H "X-Signature: 3a4f5b6c7d8e9f0a..." \
  -F "files=@invoice.pdf;type=application/pdf" \
  -F "call_type=external" \
  -F 'invoice_data_json={"supplier_name":"Acme Ltd","invoice_number":"INV-2026-001","amount":150000}'
```

---

### 3.2 `POST /api/finalize` — Finalize by Reference

Fiscalizes an **already-ingested** invoice by reference — no PDF bytes are sent. Use this when you have already submitted via `/api/ingest` (getting a `data_uuid` or `file_hash` back) and now want to fiscalize it.

**When to use**: Your integration previously called `/api/ingest` and got back a `data_uuid`/`file_hash`. You are now ready to fiscalize (submit to FIRS) without re-uploading the bytes.

**Request**: `application/json`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `ref` | String | At least one of `ref`/`trace_id` | File SHA-256 hash or `doc_ref` from the prior `/api/ingest` response |
| `trace_id` | String | At least one of `ref`/`trace_id` | Your UUIDv7 correlation ID — echoed back on the lifecycle SSE event |
| `doc_ref` | String | No | Optional explicit document reference (defaults to `ref`) |

**HMAC body signing**: Sign the raw JSON body string.

**Success Response (HTTP 202)**:

```json
{
  "status": "accepted",
  "call": "finalize",
  "finalize_by_reference": true,
  "raw_bytes_sent": false,
  "ref": "a3f5d7b2c1e4f8a9...",
  "doc_ref": "a3f5d7b2c1e4f8a9...",
  "trace_id": "your-trace-id",
  "event_id": "evt-1234...",
  "event_family": "relay.finalize.accepted",
  "idempotent_replay": false
}
```

**Idempotency**: Sending the same `trace_id` twice returns `409 ALREADY_FINALIZED`. **Treat 409 as success** — the document is already fiscalized. Your system should log it and move on.

```json
{
  "status": "error",
  "error_code": "ALREADY_FINALIZED",
  "message": "Document already finalized."
}
```

---

**cURL Example**:

```bash
curl -X POST http://13.247.224.147:8082/api/finalize \
  -H "X-API-Key: ab_prod_a1b2c3d4e5f6..." \
  -H "X-Timestamp: 2026-06-16T10:00:00Z" \
  -H "X-Signature: 3a4f5b6c7d8e9f0a..." \
  -H "Content-Type: application/json" \
  -d '{"ref": "a3f5d7b2c1e4f8a9...", "trace_id": "01904e5a-1234-7890-abcd-ef1234567890"}'
```

---

### 3.3 `POST /api/artifacts/fetch` — Fetch Artifact

Retrieve an invoice artifact — either raw bytes (signed PDF, QR payload, backend copy) or lifecycle metadata JSON.

> **Security note**: `artifact_ref` acts as a bearer capability token for raw PDF/QR data. It **must** travel in the POST body — never in a URL path, query parameter, or GET request. There is no GET variant.

**Request**: `application/json`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `artifact_ref` | String | Yes | Artifact reference from a prior ingest/finalize response or SSE event |
| `artifact_type` | String | Recommended | Artifact kind (see table below) |

**Artifact types**:

| `artifact_type` | Returns | Content-Type |
|-----------------|---------|--------------|
| `signed_pdf` | Signed invoice PDF bytes | `application/pdf` |
| `fixed_pdf` | Fixed (stamped) PDF bytes | `application/pdf` |
| `original_pdf` | Original unmodified PDF bytes | `application/pdf` |
| `backend_copy` | Approver backend copy PDF | `application/pdf` |
| `qr_invoice` | Durable QR invoice payload | `application/vnd.helium.invoice-qr+json` |
| `hlx` | HLX lifecycle metadata JSON | `application/json` |
| `approval_lifecycle` | Approval state JSON | `application/json` |
| `firs_returned_artifact` | FIRS response data JSON | `application/json` |

**HMAC body signing**: Sign the raw JSON body string.

**Success Response (HTTP 200)**:

For **hard artifacts** (PDFs, QR bytes): raw bytes in the response body.

For **lifecycle artifacts** (JSON): a JSON object.

**Response Headers** (always present on success):

| Header | Example | Description |
|--------|---------|-------------|
| `X-Relay-Artifact` | `true` | Confirms this is a Relay artifact response |
| `X-Relay-Artifact-Ref` | `art-a1b2c3...` | Echoed artifact reference |
| `X-Relay-Artifact-Kind` | `hard` or `lifecycle` | Whether bytes or JSON were returned |
| `X-Relay-Artifact-ETag` | `sha256:a3f5...` | SHA-256 of the returned body (use for caching) |
| `X-Relay-Durable-Invoice-Data` | `qr_bytes` | Present only for `qr_invoice` / `qr_blob` types |

**Not found (HTTP 404)**:

```json
{
  "code": "ARTIFACT_NOT_FOUND",
  "artifact_ref": "art-that-does-not-exist"
}
```

---

**cURL Example — fetch signed PDF**:

```bash
curl -X POST http://13.247.224.147:8082/api/artifacts/fetch \
  -H "X-API-Key: ab_prod_a1b2c3d4e5f6..." \
  -H "X-Timestamp: 2026-06-16T10:00:00Z" \
  -H "X-Signature: 3a4f5b6c7d8e9f0a..." \
  -H "Content-Type: application/json" \
  -d '{"artifact_ref": "art-a1b2c3d4-...", "artifact_type": "signed_pdf"}' \
  --output signed_invoice.pdf
```

---

### 3.4 `GET /health` — Health Check

No authentication required. Returns service health and upstream connectivity.

```bash
curl http://13.247.224.147:8082/health
```

**Response (HTTP 200 always)**:

```json
{
  "status": "healthy",
  "instance_id": "relay-api-1",
  "relay_type": "bulk",
  "version": "2.0.0",
  "services": {
    "heartbeat": "healthy",
    "module_cache": "loaded",
    "redis": "connected"
  },
  "timestamp": "2026-06-16T10:00:00Z",
  "message": null
}
```

| `status` | Meaning |
|----------|---------|
| `healthy` | All critical services reachable, module cache loaded. |
| `degraded` | HeartBeat unreachable OR module cache not yet loaded. `/api/ingest` (external flow) will return `503 MODULE_NOT_LOADED` until the cache loads. |

> Redis disconnected is **not** degraded — rate limiting falls back gracefully.

---

## 4. Response Shapes

### 4.1 Ingest Response (`POST /api/ingest`)

```json
{
  "status": "processed | queued | error",
  "data_uuid": "uuid-v4",
  "queue_id": "queue-uuid-v4",
  "filenames": ["invoice.pdf"],
  "file_count": 1,
  "file_hash": "sha256-hex",
  "file_uuids": ["blob-uuid-v4"],
  "file_hashes": ["sha256-hex"],
  "trace_id": "trc-uuid-v4",

  "irn": "HELIUM-...",         // external flow only, when status=processed
  "qr_code": "base64-string"  // external flow only, when status=processed
}
```

> **Note**: The `preview_data` field from older Relay versions has been removed. Invoice preview is now delivered asynchronously via the SSE stream (Scout/Transforma Reader receives it via `batch.status.preview_ready` event).

### 4.2 Finalize Response (`POST /api/finalize`) — HTTP 202

```json
{
  "status": "accepted",
  "call": "finalize",
  "finalize_by_reference": true,
  "raw_bytes_sent": false,
  "ref": "sha256-hex",
  "doc_ref": "sha256-hex",
  "trace_id": "your-trace-id",
  "event_id": "evt-uuid-v4",
  "event_family": "relay.finalize.accepted",
  "idempotent_replay": false
}
```

### 4.3 Error Response (all endpoints)

```json
{
  "status": "error",
  "error_code": "VALIDATION_FAILED",
  "message": "File size exceeds 10 MB limit.",
  "details": [
    { "field": "files[0]", "error": "File size 12.3 MB exceeds maximum 10 MB" }
  ]
}
```

### 4.4 Version Drift 409

```json
{
  "code": "version_drift",
  "axis": "policy_revision",
  "expected": "v15",
  "got": "v12"
}
```

---

## 5. Error Codes

| HTTP | `error_code` | When |
|------|-------------|------|
| 400 | `VALIDATION_FAILED` | File extension, size, format, or field validation failed |
| 400 | `NO_FILES_PROVIDED` | No files in the request |
| 400 | `TOO_MANY_FILES` | More than the per-request file limit (external: 1 file recommended) |
| 401 | `AUTHENTICATION_FAILED` | HMAC signature mismatch or timestamp window expired (>5 min) |
| 401 | `INVALID_API_KEY` | API key not found or not provisioned for this tenant |
| 403 | `ENCRYPTION_REQUIRED` | Server requires payload encryption (`X-Encrypted: true`) and it was absent |
| 404 | `ARTIFACT_NOT_FOUND` | Artifact ref does not resolve in blob storage (artifacts/fetch only) |
| 409 | `DUPLICATE_FILE` | Same file content was already uploaded (content-hash deduplication) |
| 409 | `ALREADY_FINALIZED` | `trace_id` was already finalized (treat as success) |
| 409 | `version_drift` | A version axis in your `X-Helium-*` headers is stale (see §7) |
| 429 | `RATE_LIMIT_EXCEEDED` | Company daily file quota exhausted |
| 500 | `INTERNAL_ERROR` | Unexpected server error — contact support with `trace_id` |
| 503 | `MODULE_NOT_LOADED` | IRN/QR module cache not yet loaded at startup — retry in ~30s |

---

## 6. Rate Limits

| Limit | Default | Scope |
|-------|---------|-------|
| Daily file submissions | **500 files / day** | Per company (API key) |
| Files per request | **3 files** | Per HTTP request |
| Max file size | **10 MB** per file | Per uploaded file |
| Max request size | **30 MB** total | Sum of all files in one request |
| HMAC timestamp window | **5 minutes** | Clock skew tolerance |

Rate limits are enforced via Redis (atomic counter). If Redis is unavailable, limits fall back to HeartBeat-side enforcement; if both are unavailable, requests are allowed through (graceful degradation).

When you hit `429 RATE_LIMIT_EXCEEDED`:
- The daily counter resets automatically at **midnight UTC**.
- Your tenant admin can request a quota increase from the Helium platform team.

---

## 7. Version Drift Headers (Optional)

These request headers let you detect when your local state (policy version, license state, user permissions) is out of sync with the server's authoritative values — before a submission is forwarded to the processing backend.

**If you send a version header and it is stale, the server returns HTTP 409** with `{"code": "version_drift", "axis": ..., "expected": ..., "got": ...}` and the request is NOT forwarded. No invoice is processed, no side effects occur. Refresh your local state and re-submit.

If you omit these headers, requests proceed normally.

| Header | Axis | Description |
|--------|------|-------------|
| `X-Helium-Policy-Revision` | `policy_revision` | Your cached policy version from HeartBeat |
| `X-Helium-License-State` | `license_state_id` | Your cached license state |
| `X-Helium-Usage-State` | `usage_state_id` | Your cached usage state |
| `X-Helium-Auth-Policy-Revision` | `auth_policy_revision` | Your cached auth policy version |
| `X-Helium-User-Permissions-Revision` | `user_permissions` | Your cached user permissions revision |

**Example** (sending policy revision on ingest):

```bash
curl -X POST http://13.247.224.147:8082/api/ingest \
  -H "X-API-Key: ..." \
  -H "X-Timestamp: ..." \
  -H "X-Signature: ..." \
  -H "X-Helium-Policy-Revision: v15" \
  -F "files=@invoice.pdf" \
  -F "call_type=external"
```

---

## 8. End-to-End Flow Diagram

### Standard External API Flow (Submit + Fiscalize in one call)

```
Your ERP/System                Relay API               HeartBeat           Core
      │                           │                        │                  │
      │── POST /api/ingest ──────>│                        │                  │
      │   call_type=external      │── validate files ──────│                  │
      │   files=invoice.pdf       │── rate limit check ───>│ (Redis/HB)       │
      │                           │── dedup check ────────>│                  │
      │                           │── write blob ─────────>│ ★ COMMIT POINT   │
      │                           │── enqueue ─────────────│─────────────────>│
      │                           │── register blob ───────>│ (fire+forget)   │
      │                           │── audit log ───────────>│ (fire+forget)   │
      │                           │                        │                  │
      │                           │── generate IRN (local IQC module)         │
      │                           │── generate QR  (local IQC module)         │
      │                           │                        │                  │
      │<── 200 OK ────────────────│                        │                  │
      │    status=processed        │                        │                  │
      │    irn="HELIUM-..."       │                        │                  │
      │    qr_code="iVBO..."      │                        │                  │
```

### Separate Ingest + Finalize Flow (#2 + #3)

```
Your ERP/System                Relay API
      │                           │
      │── POST /api/ingest ──────>│  (get data_uuid + file_hash)
      │<── 200 OK (queued) ───────│
      │                           │
      │  [... later, when ready to fiscalize ...]
      │                           │
      │── POST /api/finalize ────>│  (body: {ref: file_hash, trace_id: ...})
      │<── 202 Accepted ──────────│
      │                           │  event_family = relay.finalize.accepted
```

---

## 9. SDK Examples

### Python (requests library)

```python
import hashlib
import hmac
import json
from datetime import datetime, timezone

import requests


class HeliumRelayClient:
    def __init__(self, base_url: str, api_key: str, api_secret: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret

    def _sign(self, body_bytes: bytes) -> dict:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        body_hash = hashlib.sha256(body_bytes).hexdigest()
        message = f"{self.api_key}:{timestamp}:{body_hash}"
        sig = hmac.new(
            self.api_secret.encode(), message.encode(), hashlib.sha256
        ).hexdigest()
        return {
            "X-API-Key": self.api_key,
            "X-Timestamp": timestamp,
            "X-Signature": sig,
        }

    def submit_invoice(self, pdf_path: str, invoice_metadata: dict = None) -> dict:
        """Submit an invoice PDF and get back IRN + QR code."""
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        # For multipart, compute signature over the full body.
        # Build the multipart body first, then sign it.
        files = {"files": ("invoice.pdf", pdf_bytes, "application/pdf")}
        data = {"call_type": "external"}
        if invoice_metadata:
            data["invoice_data_json"] = json.dumps(invoice_metadata)

        # Prepare the request to get the raw body for signing
        req = requests.Request("POST", f"{self.base_url}/api/ingest", files=files, data=data)
        prepared = req.prepare()
        body_bytes = prepared.body if isinstance(prepared.body, bytes) else prepared.body.encode()

        headers = self._sign(body_bytes)
        headers["Content-Type"] = prepared.headers["Content-Type"]  # preserve multipart boundary

        resp = requests.post(
            f"{self.base_url}/api/ingest",
            headers=headers,
            data=body_bytes,
        )
        resp.raise_for_status()
        return resp.json()

    def finalize(self, ref: str, trace_id: str) -> dict:
        """Finalize an already-ingested invoice by SHA-256 reference."""
        body = {"ref": ref, "trace_id": trace_id}
        body_bytes = json.dumps(body).encode("utf-8")
        headers = self._sign(body_bytes)
        headers["Content-Type"] = "application/json"

        resp = requests.post(
            f"{self.base_url}/api/finalize",
            headers=headers,
            data=body_bytes,
        )
        if resp.status_code == 409 and resp.json().get("error_code") == "ALREADY_FINALIZED":
            return {"status": "already_finalized", "idempotent": True}
        resp.raise_for_status()
        return resp.json()

    def fetch_artifact(self, artifact_ref: str, artifact_type: str, output_path: str = None):
        """Fetch artifact bytes or lifecycle JSON by reference."""
        body = {"artifact_ref": artifact_ref, "artifact_type": artifact_type}
        body_bytes = json.dumps(body).encode("utf-8")
        headers = self._sign(body_bytes)
        headers["Content-Type"] = "application/json"

        resp = requests.post(
            f"{self.base_url}/api/artifacts/fetch",
            headers=headers,
            data=body_bytes,
        )
        resp.raise_for_status()

        kind = resp.headers.get("X-Relay-Artifact-Kind", "hard")
        if kind == "lifecycle":
            return resp.json()
        # Hard artifact — save bytes
        if output_path:
            with open(output_path, "wb") as f:
                f.write(resp.content)
        return resp.content


# Usage
client = HeliumRelayClient(
    base_url="http://13.247.224.147:8082",
    api_key="ab_prod_a1b2c3d4e5f6...",
    api_secret="your-api-secret",
)

result = client.submit_invoice(
    pdf_path="invoice.pdf",
    invoice_metadata={"supplier_name": "Acme Ltd", "invoice_number": "INV-001", "amount": 150000},
)
print("IRN:", result["irn"])
print("QR (base64):", result["qr_code"][:40], "...")
```

### JavaScript / Node.js

```javascript
const crypto = require("crypto");
const FormData = require("form-data");
const fs = require("fs");
const fetch = require("node-fetch");

function sign(apiKey, apiSecret, bodyBuffer) {
  const timestamp = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  const bodyHash = crypto.createHash("sha256").update(bodyBuffer).digest("hex");
  const message = `${apiKey}:${timestamp}:${bodyHash}`;
  const signature = crypto
    .createHmac("sha256", apiSecret)
    .update(message)
    .digest("hex");
  return { timestamp, signature };
}

async function submitInvoice(baseUrl, apiKey, apiSecret, pdfPath, invoiceData) {
  const form = new FormData();
  form.append("files", fs.createReadStream(pdfPath), "invoice.pdf");
  form.append("call_type", "external");
  if (invoiceData) {
    form.append("invoice_data_json", JSON.stringify(invoiceData));
  }

  const bodyBuffer = await new Promise((resolve) => {
    const chunks = [];
    form.on("data", (chunk) => chunks.push(chunk));
    form.on("end", () => resolve(Buffer.concat(chunks)));
    form.resume();
  });

  const { timestamp, signature } = sign(apiKey, apiSecret, bodyBuffer);

  const response = await fetch(`${baseUrl}/api/ingest`, {
    method: "POST",
    headers: {
      "X-API-Key": apiKey,
      "X-Timestamp": timestamp,
      "X-Signature": signature,
      ...form.getHeaders(),
    },
    body: bodyBuffer,
  });

  if (!response.ok) {
    const err = await response.json();
    throw new Error(`Relay error ${response.status}: ${err.error_code} — ${err.message}`);
  }

  return response.json();
}

// Usage
submitInvoice(
  "http://13.247.224.147:8082",
  "ab_prod_a1b2c3d4e5f6...",
  "your-api-secret",
  "./invoice.pdf",
  { supplier_name: "Acme Ltd", invoice_number: "INV-001", amount: 150000 }
).then((result) => {
  console.log("IRN:", result.irn);
  console.log("QR (base64):", result.qr_code.substring(0, 40) + "...");
});
```

---

## Appendix A: File Requirements

| Property | Constraint |
|----------|-----------|
| Format | PDF only (external flow) |
| Encoding | Must be a valid PDF file (basic header check) |
| Size | Max 10 MB per file |
| Files per request | 1 recommended for external callers |
| Extension | `.pdf` |

> **Non-PDF formats** (`.xml`, `.json`, `.csv`, `.xlsx`) are accepted by the bulk flow (Float desktop) but not guaranteed for external API callers in the current configuration. Confirm with your tenant admin.

---

## Appendix B: Getting Your API Credentials

1. Log in to Float as an **Admin** or **Owner** role.
2. Navigate to **Settings → API Keys**.
3. Click **Generate API Key**.
4. Copy the `api_key` and `api_secret` — the secret is only shown once.
5. Pass both values to your integration team.

API keys are scoped per tenant and environment. Production and staging keys are separate.

---

*Last updated: 2026-06-16 | Relay API version: 2.0.0 | Document version: 1.0*
