# Simulator Integration Guide

**Service:** Simulator (port 8090)
**Base URL:** `http://<host>:8090`
**Auth:** None (demo service, no authentication on Simulator endpoints)

---

## Part A: Backend Team (Relay / Core / HeartBeat)

### How the Simulator Sends Data Into the Pipeline

The Simulator generates invoices and sends them to **Relay** `/api/ingest` exactly as a real external API client would. It never writes to databases directly.

```
Simulator ──HMAC-signed POST──> Relay /api/ingest ──> HeartBeat (blob) ──> Core (parse/process)
```

### HMAC Authentication

Every request the Simulator sends to Relay carries three headers:

| Header | Value |
|--------|-------|
| `X-API-Key` | `ABBEY-2026-AQ1P6JMS` |
| `X-Timestamp` | ISO 8601 UTC, e.g. `2026-04-13T12:00:00Z` |
| `X-Signature` | HMAC-SHA256 hex digest |

Signature is computed over the **raw multipart body bytes**:

```
body_hash = SHA256(raw_body_bytes).hex()
message   = "{api_key}:{timestamp}:{body_hash}"
signature = HMAC-SHA256(api_secret, message).hex()
```

Secret: `Ysas2LCrs0ttlwm0N5Y0u44OPABFn0yT`

### Multipart Payload Sent to Relay

```
POST /api/ingest HTTP/1.1
Content-Type: multipart/form-data; boundary=----SimulatorBoundary...
X-API-Key: ABBEY-2026-AQ1P6JMS
X-Timestamp: 2026-04-13T12:00:00Z
X-Signature: <hex>

Parts:
  files          = <invoice>.json (single JSON file, application/json)
  call_type      = "external"
  invoice_data_json = {"invoice_number": "...", "tin": "...", "issue_date": "..."}
```

The `call_type=external` triggers Relay's external flow: ingest -> IQC (IRN + QR) -> return immediately.

### Invoice JSON Shape (TransformedInvoice-aligned)

Every invoice file the Simulator uploads conforms to Core's `TransformedInvoice` canonical schema. Both outbound and inbound invoices use the same field set.

---

#### Outbound Invoice (Abbey sells to borrower)

```json
{
  "invoice_number": "ABB-0000001",
  "direction": "OUTBOUND",
  "document_type": "COMMERCIAL_INVOICE",
  "transaction_type": "B2C",
  "firs_invoice_type_code": "380",

  "issue_date": "2026-04-13",
  "due_date": "2026-05-13",

  "currency_code": "NGN",
  "tax_exclusive_amount": "250000.00",
  "total_tax_amount": "18750.00",
  "total_amount": "268750.00",

  "seller_business_name": "Abbey Mortgage Bank PLC",
  "seller_tin": "02345678-0001",
  "seller_rc_number": "RC464532",
  "seller_email": "info@abbeymortgage.com",
  "seller_phone": "+2349012345678",
  "seller_address": "3 Abiola Segun Akinola Crescent, off Obafemi Awolowo Way",
  "seller_city": "Ikeja",
  "seller_state": "25",
  "seller_country": "NG",

  "buyer_business_name": "Adewale Ogundimu",
  "buyer_tin": "00100001-0001",
  "buyer_rc_number": "",
  "buyer_email": "adewale.ogundimu@gmail.com",
  "buyer_phone": "+2348031234501",
  "buyer_address": "14 Admiralty Way",
  "buyer_city": "Lekki",
  "buyer_state": "25",
  "buyer_country": "NG",

  "line_items": [
    {
      "line_number": 1,
      "description": "Mortgage Origination Fee",
      "quantity": "1",
      "unit_price": "250000.00",
      "line_total": "268750.00",
      "unit_of_measure": "unit",
      "tax_amount": "18750.00",
      "tax_rate": "7.5",
      "item_type": "SERVICE",
      "customer_sku": "ABB-FEE-001",
      "hs_code": "S-FIN-001"
    }
  ],

  "source": "Simulator",
  "source_id": "ABBEY-2026-AQ1P6JMS",
  "tenant_id": "abbey",
  "branch_name": "Victoria Island",
  "branch_city": "Victoria Island"
}
```

- **Seller** is always Abbey (tenant party, non-editable in Core)
- **Buyer** is random: 80% B2C borrower, 20% B2B enterprise
- **Line items**: 1-3 random fees from `fee_catalog.json`, amounts randomized within min/max range
- **VAT**: 7.5% for STANDARD fees, 0% for EXEMPT (insurance premiums)
- **Invoice number**: `ABB-{7-digit zero-padded}`, sequential counter (resets on restart)

---

#### Inbound Invoice (Supplier sells to Abbey) -- FIRS Shape

```json
{
  "invoice_number": "3000058291",
  "direction": "INBOUND",
  "document_type": "COMMERCIAL_INVOICE",
  "transaction_type": "B2B",
  "firs_invoice_type_code": "380",

  "issue_date": "2026-04-13",
  "due_date": "2026-05-13",

  "currency_code": "NGN",
  "tax_exclusive_amount": "3500000.00",
  "total_tax_amount": "262500.00",
  "total_amount": "3762500.00",

  "seller_business_name": "Cummins West Africa Limited",
  "seller_tin": "01531063-0001",
  "seller_rc_number": "RC672182",
  "seller_email": "service@cummins.com",
  "seller_phone": "+2349012340003",
  "seller_address": "Plot Y, Mobolaji Johnson Avenue, Alausa",
  "seller_city": "Ikeja",
  "seller_state": "25",
  "seller_country": "NG",

  "buyer_business_name": "Abbey Mortgage Bank PLC",
  "buyer_tin": "02345678-0001",
  "buyer_rc_number": "RC464532",
  "buyer_email": "info@abbeymortgage.com",
  "buyer_phone": "+2349012345678",
  "buyer_address": "3 Abiola Segun Akinola Crescent, off Obafemi Awolowo Way",
  "buyer_city": "Ikeja",
  "buyer_state": "25",
  "buyer_country": "NG",

  "line_items": [
    {
      "line_number": 1,
      "description": "Generator Overhaul",
      "quantity": "1",
      "unit_price": "3500000.00",
      "line_total": "3762500.00",
      "tax_amount": "262500.00",
      "tax_rate": "7.5",
      "item_type": "SERVICE"
    }
  ],

  "source": "FIRS",
  "source_id": "firs-delivery",
  "tenant_id": "abbey"
}
```

- **Seller** is the external supplier (editable party in Core)
- **Buyer** is always Abbey (tenant party, non-editable in Core)
- **`source: "FIRS"`** and **`source_id: "firs-delivery"`** -- marks this as a FIRS-delivered inbound invoice
- **`direction: "INBOUND"`** -- Core uses this to flip provenance (buyer fields become non-editable)
- Core should store the raw payload in `inbound_payload_json` and set `inbound_status: "PENDING_REVIEW"`
- Invoice numbers follow each supplier's real format pattern (see table below)

### Supplier Invoice Number Formats

| Supplier | Format | Example |
|----------|--------|---------|
| First Central Credit Bureau | 5-digit numeric | `56599` |
| Starlink Internet Services | 7-5-2 | `1811582-35322-86` |
| Cummins West Africa | 10-digit numeric | `3000052834` |
| IPNX Nigeria | 8-4 | `01347537-0042` |
| Avon Medicals | code/dept/loc/seq/year | `AVON/HR/SL/04/2026` |

### What Core Should Do with Inbound Invoices

When Core encounters an invoice file with `direction: "INBOUND"`:

1. Parse the JSON (schema-agnostic JSONParser, same as outbound)
2. Set `inbound_received_at` to current timestamp
3. Set `inbound_status` to `PENDING_REVIEW`
4. Store the full JSON in `inbound_payload_json` for audit
5. Apply provenance rules: `buyer_*` fields are TENANT (non-editable), `seller_*` fields are ORIGINAL
6. Skip Transforma -- the FIRS payload is already in canonical shape

### What Relay Returns to the Simulator

For both outbound and inbound, Relay returns:

```json
{
  "status": "processed",
  "data_uuid": "019d86d0-4382-71ef-...",
  "queue_id": "queue_019d86d0-4392-...",
  "filenames": ["ABB-0000001.json"],
  "file_count": 1,
  "file_hash": "f3cd29d1...",
  "trace_id": "019d86d0-4377-...",
  "file_uuids": ["019d86d0-4383-..."],
  "file_hashes": ["f3cd29d1..."],
  "preview_data": null,
  "irn": "ABB0000001-A8BM72KQ-20260413",
  "qr_code": "NRS:ABB0000001-A8BM72KQ-20260413:F336F224"
}
```

### Rate Limits to Be Aware Of

- Relay daily limit: **500 requests** per API key
- Max files per request: **3** (Simulator sends 1 per request)
- Max file size: **10 MB**
- Timestamp window: **5 minutes** (HMAC expires after this)

---

## Part B: Frontend Team (Demo Dashboard)

### Overview

The Simulator is a backend service with a simple JSON API. The frontend calls Simulator endpoints to trigger invoice generation, and the Simulator handles all Relay communication (HMAC signing, multipart encoding, etc.) internally. The frontend never talks to Relay directly.

```
Frontend ──JSON POST──> Simulator :8090 ──HMAC──> Relay :8082 ──> Pipeline
```

### Base URL

- **Local dev:** `http://localhost:8090`
- **EC2 deployed:** `http://13.247.224.147:8090`

All requests use `Content-Type: application/json`. No authentication required on Simulator endpoints.

---

### Endpoints

#### 1. Health Check

```
GET /health
```

**Response:**
```json
{
  "status": "healthy",
  "service": "simulator",
  "version": "0.1.0",
  "relay_url": "http://13.247.224.147:8082",
  "active_streams": ["sim-abbey-001"]
}
```

Use `active_streams` to show which simulation streams are currently running.

---

#### 2. Send Single Outbound Invoice

```
POST /api/single
```

**Request body:**
```json
{
  "tenant_id": "abbey"
}
```

`tenant_id` defaults to `"abbey"` if omitted.

**Response (success):**
```json
{
  "status": "sent",
  "relay_response": {
    "status_code": 200,
    "body": {
      "status": "processed",
      "data_uuid": "019d86d0-4382-...",
      "irn": "ABB0000001-A8BM72KQ-20260413",
      "qr_code": "NRS:ABB0000001-A8BM72KQ-20260413:F336F224",
      "..."
    }
  },
  "generated_invoice": {
    "invoice_number": "ABB-0000001",
    "buyer": "Adewale Ogundimu",
    "fees": ["Mortgage Origination Fee", "Property Valuation Fee"],
    "subtotal": "644109.35",
    "vat": "48308.20",
    "total": "692417.55"
  }
}
```

**Key fields for display:**
- `generated_invoice.invoice_number` -- the invoice ID
- `generated_invoice.buyer` -- who was billed
- `generated_invoice.fees` -- list of fee names
- `generated_invoice.total` -- total amount (string, NGN)
- `relay_response.body.irn` -- the IRN assigned by Relay
- `relay_response.body.qr_code` -- the QR code string
- `relay_response.status_code` -- 200 = success

**Error response (Relay unreachable):**
```json
{
  "status": "relay_error",
  "error": "...",
  "generated_invoice": { "invoice_number": "...", "buyer": "...", "total": "..." }
}
```
HTTP status: 502

---

#### 3. Send Burst (Multiple Invoices)

```
POST /api/burst
```

**Request body:**
```json
{
  "tenant_id": "abbey",
  "count": 10
}
```

`count`: 1-100. Invoices are sent sequentially with 1-second spacing.

**Response:**
```json
{
  "status": "completed",
  "tenant_id": "abbey",
  "total": 10,
  "sent": 10,
  "failed": 0,
  "results": [
    {
      "invoice_number": "ABB-0000002",
      "buyer": "Ngozi Eze",
      "total": "185432.10",
      "relay_status_code": 200
    },
    ...
  ]
}
```

**Note:** This is a long-running request. A burst of 10 takes ~10 seconds. Show a loading state. Consider polling `/health` to track active count.

---

#### 4. Start Continuous Stream

```
POST /api/stream/start
```

**Request body:**
```json
{
  "tenant_id": "abbey",
  "cadence": "1m",
  "include_inbound": true,
  "inbound_ratio": 0.2
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `tenant_id` | string | `"abbey"` | Tenant to simulate |
| `cadence` | string | `"1m"` | Interval: `"1m"`, `"5m"`, or `"30m"` |
| `include_inbound` | bool | `true` | Also generate inbound (supplier) invoices |
| `inbound_ratio` | float | `0.2` | Probability of inbound per tick (0.0-1.0) |

**Response:**
```json
{
  "status": "started",
  "stream_id": "sim-abbey-001",
  "cadence": "1m",
  "include_inbound": true,
  "inbound_ratio": 0.2
}
```

Save `stream_id` -- you need it to check status or stop.

Each tick generates 1-3 outbound invoices + (with `inbound_ratio` probability) 1 inbound invoice.

---

#### 5. Check Stream Status

```
GET /api/stream/status?stream_id=sim-abbey-001
```

**Response:**
```json
{
  "stream_id": "sim-abbey-001",
  "running": true,
  "cadence": "1m",
  "include_inbound": true,
  "stats": {
    "outbound_sent": 42,
    "inbound_sent": 8,
    "errors": 1,
    "started_at": "2026-04-13T12:00:00+00:00",
    "last_sent_at": "2026-04-13T12:42:00+00:00"
  }
}
```

Poll this to update a live counter in the dashboard.

---

#### 6. Stop Stream

```
POST /api/stream/stop
```

**Request body:**
```json
{
  "stream_id": "sim-abbey-001"
}
```

**Response:**
```json
{
  "status": "stopped",
  "stream_id": "sim-abbey-001"
}
```

---

#### 7. Send Single Inbound Invoice

```
POST /api/inbound
```

**Request body:**
```json
{
  "tenant_id": "abbey",
  "supplier_index": null
}
```

`supplier_index`: `0`-`4` to pick a specific supplier, or `null`/omit for random.

| Index | Supplier |
|-------|----------|
| 0 | First Central Credit Bureau |
| 1 | Starlink Internet Services |
| 2 | Cummins West Africa |
| 3 | IPNX Nigeria |
| 4 | Avon Medicals |

**Response:**
```json
{
  "status": "sent",
  "relay_response": {
    "status_code": 200,
    "body": {
      "irn": "3556796796-A8BM72KQ-20260413",
      "qr_code": "NRS:3556796796-A8BM72KQ-20260413:19516032",
      "..."
    }
  },
  "generated_invoice": {
    "invoice_number": "3556796796",
    "supplier": "Cummins West Africa Limited",
    "description": "Maintenance Service",
    "subtotal": "2086798.68",
    "vat": "156509.90",
    "total": "2243308.58"
  }
}
```

---

#### 8. Test Error Scenarios

```
POST /api/bad
```

**Request body:**
```json
{
  "tenant_id": "abbey",
  "error_type": "auth_failure"
}
```

**Available error types:**

| error_type | What Happens | Expected Relay Response |
|-----------|-------------|----------------------|
| `auth_failure` | Wrong HMAC signature | 401 |
| `expired_timestamp` | Timestamp 10 min old | 401 |
| `invalid_api_key` | Nonexistent API key | 401 |
| `rate_limit` | 10 rapid requests | 429 (if daily limit hit) |
| `empty_batch` | Empty JSON array file | Varies |
| `malformed_json` | Invalid bytes as .json | Varies |
| `missing_fields` | Invoice with no fields | Varies |
| `duplicate` | Same invoice sent twice | 409 on second send |
| `oversized_file` | 11 MB file (limit=10 MB) | 413 |

**Response (example: auth_failure):**
```json
{
  "error_type": "auth_failure",
  "description": "Sent with invalid HMAC signature",
  "relay_response": {
    "status_code": 401,
    "body": {
      "status": "error",
      "error_code": "AUTHENTICATION_FAILED",
      "message": "HMAC signature verification failed."
    }
  }
}
```

**Response (example: duplicate):**
```json
{
  "error_type": "duplicate",
  "description": "Sent invoice ABB-0000006 twice",
  "first_response": { "status_code": 200, "..." },
  "second_response": { "status_code": 409, "..." }
}
```

---

#### 9. Attack Simulation (Future)

```
POST /api/attack
```

Returns `501 Not Implemented`. Reserved for future security testing.

---

### Frontend Quick-Start Cheat Sheet

```javascript
const SIM = "http://13.247.224.147:8090";

// Send one outbound invoice
const single = await fetch(`${SIM}/api/single`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ tenant_id: "abbey" }),
}).then(r => r.json());

// Send 5 invoices in burst
const burst = await fetch(`${SIM}/api/burst`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ tenant_id: "abbey", count: 5 }),
}).then(r => r.json());

// Start continuous stream
const stream = await fetch(`${SIM}/api/stream/start`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ tenant_id: "abbey", cadence: "1m" }),
}).then(r => r.json());
const streamId = stream.stream_id;

// Poll stream status
const status = await fetch(`${SIM}/api/stream/status?stream_id=${streamId}`)
  .then(r => r.json());

// Stop stream
await fetch(`${SIM}/api/stream/stop`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ stream_id: streamId }),
});

// Send inbound (supplier invoice)
const inbound = await fetch(`${SIM}/api/inbound`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ tenant_id: "abbey", supplier_index: 2 }),
}).then(r => r.json());

// Test error scenario
const bad = await fetch(`${SIM}/api/bad`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ tenant_id: "abbey", error_type: "auth_failure" }),
}).then(r => r.json());
```
