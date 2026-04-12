# Simulator Service — Contract & Boundaries

**Owner:** Dedicated session (not this one)
**Depends on:** Relay API, Core API, PostgreSQL, seed data catalogs
**Port:** 8090
**Framework:** FastAPI + uvicorn

---

## Purpose

The Simulator is a standalone backend service that generates realistic demo invoice data and injects it through the real Helium pipeline. It does NOT write to databases directly — all data flows through Relay (outbound) or Core (inbound), exactly as a real client would.

---

## API Endpoints

### 1. `POST /api/single` — Send single outbound invoice

Generates one outbound invoice (Abbey → random borrower) and sends it to Relay API as a JSON file.

**Request:**
```json
{
  "tenant_id": "abbey"
}
```

**Behavior:**
1. Load fee_catalog.json, pick 1-3 random fees
2. Load borrowers.json + enterprises.json, pick random buyer
3. Generate UBL-format invoice JSON
4. Generate invoice number: `ABB-{7_digit_sequence}` (e.g., `ABB-0199283`)
5. HMAC-sign the request with tenant's API credentials
6. POST to Relay `/api/ingest` as multipart (single .json file)
7. Return Relay's response

**Response:**
```json
{
  "status": "sent",
  "relay_response": { ... },
  "generated_invoice": {
    "invoice_number": "ABB-0199283",
    "buyer": "Adewale Ogundimu",
    "fees": ["Mortgage Origination Fee"],
    "subtotal": 250000.00,
    "vat": 18750.00,
    "total": 268750.00
  }
}
```

---

### 2. `POST /api/burst` — Send multiple outbound invoices at once

Generates N outbound invoices and sends them as a single batch to Relay.

**Request:**
```json
{
  "tenant_id": "abbey",
  "count": 10
}
```

**Behavior:**
- Generate `count` invoices (same logic as /api/single per invoice)
- Pack all into a single JSON array
- Send as one batch to Relay `/api/ingest`
- Return batch result (processed/duplicates/failed)

---

### 3. `POST /api/stream/start` — Start continuous simulation

Starts a background loop that sends invoices at a configurable cadence.

**Request:**
```json
{
  "tenant_id": "abbey",
  "cadence": "1m",
  "include_inbound": true,
  "inbound_ratio": 0.2
}
```

**cadence values:** `"1m"`, `"5m"`, `"30m"`

**Behavior:**
- Spawns async background task
- Every `cadence` interval:
  - Generate 1-3 outbound invoices → send to Relay
  - If `include_inbound`: with probability `inbound_ratio`, also generate 1 inbound invoice → send to Core
- Continues until stopped

**Response:**
```json
{
  "status": "started",
  "stream_id": "sim-abbey-001",
  "cadence": "1m",
  "include_inbound": true
}
```

### `POST /api/stream/stop` — Stop continuous simulation

```json
{
  "stream_id": "sim-abbey-001"
}
```

### `GET /api/stream/status` — Get stream status

```json
{
  "stream_id": "sim-abbey-001",
  "running": true,
  "stats": {
    "outbound_sent": 42,
    "inbound_sent": 8,
    "errors": 1,
    "started_at": "2026-04-12T15:00:00Z",
    "last_sent_at": "2026-04-12T15:42:00Z"
  }
}
```

---

### 4. `POST /api/inbound` — Send single inbound invoice

Generates one inbound invoice (supplier → Abbey) and sends it to Core API.

**Request:**
```json
{
  "tenant_id": "abbey",
  "supplier_index": null
}
```

`supplier_index`: 0-4 to pick a specific supplier, or null for random.

**Behavior:**
1. Load suppliers.json, pick supplier (random or by index)
2. Generate invoice number matching supplier's format pattern:
   - First Central: `{5-digit}` (e.g., `58412`)
   - Starlink: `{7}-{5}-{2}` (e.g., `1923847-42156-03`)
   - Cummins: `{10-digit}` (e.g., `3000058291`)
   - IPNX: `{8}-{4}` (e.g., `01347537-0042`)
   - Avon: `{code}/{dept}/{loc}/{seq}/{year}` (e.g., `AVON/HR/SL/04/2026`)
3. Generate UBL-format invoice with supplier as accountingSupplierParty
4. Vary amounts within supplier's typical_amount_range
5. POST to Core `/api/v1/ingest` with `direction: "INBOUND"`
6. Return Core's response

**Response:**
```json
{
  "status": "sent",
  "core_response": { ... },
  "generated_invoice": {
    "invoice_number": "3000058291",
    "supplier": "Cummins West Africa Limited",
    "description": "Generator Overhaul",
    "subtotal": 3500000.00,
    "vat": 262500.00,
    "total": 3762500.00
  }
}
```

---

### 5. `POST /api/bad` — Send intentionally bad calls

Generates requests that should trigger specific error responses from Relay/Core.

**Request:**
```json
{
  "tenant_id": "abbey",
  "error_type": "auth_failure"
}
```

**error_type values:**

| error_type | What it does | Expected response |
|-----------|-------------|-------------------|
| `auth_failure` | Send with wrong API secret (bad HMAC signature) | 401 Unauthorized |
| `expired_timestamp` | Send with timestamp >5 min old | 401 Timestamp expired |
| `invalid_api_key` | Send with nonexistent API key | 401 Unknown API key |
| `rate_limit` | Send 501+ requests rapidly (exceed daily quota) | 429 Rate limit |
| `empty_batch` | Send empty JSON array | 400 Empty batch |
| `malformed_json` | Send invalid JSON bytes | 400 Malformed |
| `missing_fields` | Send invoice missing required fields | 422 Validation error |
| `duplicate` | Send same invoice_number twice | 207 with duplicate entry |
| `oversized_file` | Send file exceeding max_file_size_mb | 413 File too large |

**Response:**
```json
{
  "error_type": "auth_failure",
  "request_sent": { "api_key": "ABBEY-2026-AQ1P6JMS", "signature": "(wrong)" },
  "relay_response": {
    "status_code": 401,
    "body": { "status": "error", "error_code": "AUTHENTICATION_FAILED", "message": "..." }
  }
}
```

---

### 6. `POST /api/attack` — (Future) Simulate nefarious calls

**Status:** NOT IMPLEMENTED — stub only. Reserved for future security testing.

Placeholder endpoint that returns 501 Not Implemented.

---

### `GET /health` — Health check

```json
{
  "status": "healthy",
  "service": "simulator",
  "version": "0.1.0",
  "active_streams": ["sim-abbey-001"]
}
```

---

## Boundaries — What the Simulator Does and Does NOT Do

### DOES:
- Read seed catalogs from `/data/{tenant_id}/` (fee_catalog, borrowers, suppliers, enterprises, branches)
- Generate realistic invoice data by mix-matching catalog entries
- Generate invoice numbers in correct formats (per-tenant for outbound, per-supplier for inbound)
- HMAC-sign requests using tenant credentials from config
- Call Relay API (outbound) and Core API (inbound) over HTTP
- Track its own statistics (sent counts, errors)
- Run background streams at configurable cadence

### DOES NOT:
- Write to any database directly
- Generate IRN or QR codes (that's Relay/Core's job)
- Parse responses beyond status codes
- Manage tenant configuration (reads from config/tenants.json)
- Handle authentication of its own endpoints (demo service, no auth required)

---

## Dependencies

### Upstream Services (Simulator calls these):
- **Relay API** at `{SIMULATOR_RELAY_URL}/api/ingest` — for outbound invoices
- **Core API** at `{SIMULATOR_CORE_URL}/api/v1/ingest` — for inbound invoices

### Data Files (read-only):
- `/data/{tenant_id}/fee_catalog.json` — product/service catalog with price ranges
- `/data/{tenant_id}/borrowers.json` — B2C customer pool
- `/data/{tenant_id}/enterprises.json` — B2B customer pool
- `/data/{tenant_id}/suppliers.json` — inbound supplier pool with invoice formats
- `/data/{tenant_id}/branches.json` — branch locations
- `/data/{tenant_id}/tenant_config.json` — tenant identity (seller party details)
- `/config/tenants.json` — API keys and secrets for HMAC signing

### Environment Variables:
```
SIMULATOR_RELAY_URL=http://relay-api:8082
SIMULATOR_CORE_URL=http://core:8080
SIMULATOR_PORT=8090
SIMULATOR_DATA_DIR=/app/data
SIMULATOR_CONFIG_DIR=/app/config
```

---

## Invoice Generation Rules

### Outbound (Abbey → Borrower)
- **Invoice number:** `ABB-{7_digit_zero_padded}` (e.g., `ABB-0000001`)
- **Sequence:** Global counter per tenant, persisted in memory (resets on restart)
- **Seller:** Always Abbey (from tenant_config.json)
- **Buyer:** Random pick from borrowers.json (80%) or enterprises.json (20%)
- **Line items:** 1-3 random fees from fee_catalog.json
- **Amounts:** Random within each fee's [min_amount, max_amount] range
- **VAT:** 7.5% for STANDARD items, 0% for EXEMPT (insurance premiums)
- **Date:** Today's date (or random date within last 30 days for bulk)
- **Branch:** Random from branches.json
- **IRN:** NOT generated by Simulator — Relay generates it as `{invoice_number}-{firs_service_id}-{YYYYMMDD}`
- **Format:** UBL JSON (matching invoices.json structure)

### Inbound (Supplier → Abbey)
- **Invoice number:** Matches supplier's format pattern (see suppliers.json)
- **Seller:** The supplier (from suppliers.json)
- **Buyer:** Always Abbey (from tenant_config.json, as accountingCustomerParty)
- **Line items:** Random pick from supplier's typical_items
- **Amounts:** Random within supplier's typical_amount_range, multiplied by quantity
- **Quantity:** Random within supplier's typical_quantity_range
- **VAT:** Per supplier's vat_rate (7.5% or 0% for Avon)
- **Direction:** INBOUND (sent to Core, not Relay)

### Bulk XLSX Generation (for /api/burst or pre-generated samples)
- **Format:** ABMFB-style flat columns: `transaction_id, fee_amount, description, transaction_date, vat_amount, branch`
- **transaction_id:** Same as invoice_number (ABB-XXXXXXX)
- **50+ rows** per file
- **Mixed files:** Include 70% valid, 10% duplicates, 10% missing fields, 10% bad values

---

## HMAC Signing Contract

The Simulator must sign outbound requests to Relay exactly as a real client would:

```python
import hashlib, hmac
from datetime import datetime, timezone

timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
body_hash = hashlib.sha256(body_bytes).hexdigest()
message = f"{api_key}:{timestamp}:{body_hash}"
signature = hmac.new(api_secret.encode(), message.encode(), hashlib.sha256).hexdigest()

headers = {
    "X-API-Key": api_key,
    "X-Timestamp": timestamp,
    "X-Signature": signature,
}
```

Timestamp must be within 5 minutes of server time.

---

## Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
EXPOSE 8090
CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8090"]
```

**Volumes:**
- `/app/data` — mounted from `../../data/` (read-only seed catalogs)
- `/app/config` — mounted from `../../config/` (read-only tenants.json)

---

## File Structure

```
services/simulator/
├── SIMULATOR_CONTRACT.md    ← this file
├── Dockerfile
├── requirements.txt
└── src/
    ├── __init__.py
    ├── app.py               # FastAPI app, endpoint handlers
    ├── generators.py         # Invoice generation logic (outbound + inbound)
    ├── hmac_signer.py        # HMAC-SHA256 signing for Relay calls
    ├── catalog.py            # Load and cache seed data catalogs
    ├── stream_manager.py     # Background stream management
    └── bad_calls.py          # Intentional error generation
```
