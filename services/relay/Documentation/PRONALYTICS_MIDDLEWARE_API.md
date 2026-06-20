# Pronalytics Middleware API

**Audience:** External systems — ERP platforms, accounting software, and core-banking systems — that submit invoices for Nigerian FIRS e-invoicing and receive an **IRN** (Invoice Reference Number) and **QR code** in return.

**You talk to exactly two services, both fronted at one base URL:**

| Service | Role | Paths |
|---------|------|-------|
| **Auth** (HeartBeat) | Issues your access token (OAuth 2.0) | `/api/auth/oauth/token`, `/.well-known/jwks.json` |
| **Relay** (Middleware) | Everything else — submit invoices, check status | `/api/ingest`, `/api/status`, `/health` |

You **never** call any other Helium/Pronalytics service directly. Relay handles IRN generation, QR signing, FIRS submission, and all downstream processing on your behalf.

**Base URL:** `https://api.pronalytics.ng` *(provided to you at onboarding; all paths below are relative to it)*

---

## 1. How it works (the whole flow)

```
  Your ERP / accounting system
        │
        │  1. POST /api/auth/oauth/token        (client_id + client_secret)
        ▼
   ┌──────────┐   200 { access_token, expires_in: 900 }
   │   Auth   │ ─────────────────────────────────────────┐
   └──────────┘                                           │
        ▲                                                 ▼
        │                                   Authorization: Bearer <access_token>
        │  2. POST /api/ingest  (your invoice(s) + the bearer token)
        ▼
   ┌──────────┐   200 { summary, processed:[ { irn, qr_code }, … ] }
   │  Relay   │ ─────────────────────────────────────────────────────►  back to you
   └──────────┘
        │
        │  3. POST /api/status  (poll any time, by transaction_id / irn / batch_id)
        ▼
       status per invoice
```

1. **Authenticate** once — exchange your `client_id` + `client_secret` for a short-lived access token (valid 15 minutes).
2. **Submit** one or many invoices to `/api/ingest` with that token. Relay returns an **IRN** and **QR code** for *each* invoice.
3. **Check status** any time via `/api/status` using the `transaction_id`, `irn`, or `batch_id`.

You can submit over **REST** (this document) or **AMQP** ([§5](#5-amqp-channel-optional)). Both carry the same invoice payload to Relay.

---

## 2. Authentication — OAuth 2.0 (client credentials)

Authentication uses the standard **OAuth 2.0 `client_credentials` grant** (RFC 6749). There are no user logins, passwords, or interactive screens — it is pure machine-to-machine.

### 2.1 Your credentials

You are issued a credential pair by Pronalytics at onboarding:

| Field | Example | Notes |
|-------|---------|-------|
| `client_id` | `oa_3f8a91c2_abm` | Public identifier for your integration. Safe to log. |
| `client_secret` | `Tx9_p7K-mN3oqRsTuVwXyZaBcDeFgHi1J2k3L4m5N6P` | **Shown to you once, at issuance.** Store it in a secret manager. It cannot be recovered — only rotated. |

Your `client_id` is permanently bound to **your tenant**. You cannot submit invoices for any other organisation, even if you wanted to — the tenant is fixed at registration and is never accepted as a request parameter.

### 2.2 Get an access token

**`POST /api/auth/oauth/token`** — `Content-Type: application/x-www-form-urlencoded`

Two equivalent ways to present your credentials:

**(a) Credentials in the form body**
```http
POST /api/auth/oauth/token HTTP/1.1
Host: api.pronalytics.ng
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials&client_id=oa_3f8a91c2_abm&client_secret=YOUR_SECRET&scope=invoice:submit
```

**(b) Credentials via HTTP Basic**
```http
POST /api/auth/oauth/token HTTP/1.1
Host: api.pronalytics.ng
Authorization: Basic <base64(client_id:client_secret)>
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials&scope=invoice:submit
```

**Request parameters**

| Parameter | Required | Meaning |
|-----------|----------|---------|
| `grant_type` | Yes | Must be exactly `client_credentials`. |
| `client_id` | Yes¹ | Your integration identifier. |
| `client_secret` | Yes¹ | Your integration secret. |
| `scope` | Yes | The permission you are requesting. Use `invoice:submit`. |

¹ Supply `client_id`/`client_secret` *either* in the form body (a) *or* via HTTP Basic (b) — not both.

**Success response — `200 OK`**
```json
{
  "access_token": "eyJhbGciOiJFZERTQSIsImtpZCI6Ii4uLiJ9.eyJhdWQiOiJoZWxpdW0ucmVsYXktaW5nZXN0Ii4uLn0...",
  "token_type": "Bearer",
  "expires_in": 900,
  "scope": "invoice:submit"
}
```

| Field | Meaning |
|-------|---------|
| `access_token` | The bearer token. Send it on every Relay call (see [§2.3](#23-use-the-token)). |
| `token_type` | Always `Bearer`. |
| `expires_in` | Lifetime in seconds (**900 = 15 minutes**). There is **no refresh token** — when it expires, request a new one the same way. |
| `scope` | The granted scope (`invoice:submit`). |

> **Token reuse:** Cache the token and reuse it until it nears expiry. Do **not** request a fresh token per invoice — request one, then submit many invoices under it. The token endpoint is rate-limited (60 requests/min per client).

### 2.3 Use the token

Send it as a bearer token on **every Relay request**:

```http
Authorization: Bearer <access_token>
```

The token is a signed JWT carrying `aud: helium.relay-ingest`. Relay verifies it locally on each request — you do not need to do anything beyond presenting it.

### 2.4 Token errors

| HTTP | `error` | Cause | What to do |
|------|---------|-------|------------|
| `401` | `invalid_client` | Unknown `client_id`, wrong `client_secret`, or your client is inactive/expired. | Verify your credentials. The error never says *which* of these failed (anti-enumeration), so re-check both. |
| `400` | `unsupported_grant_type` | `grant_type` was not `client_credentials`. | Fix the `grant_type`. |
| `400` | `invalid_scope` | Requested a scope you are not granted. | Use `invoice:submit`. |
| `429` | *(rate limited)* | More than 60 token requests/min for your client. | Back off; respect `Retry-After`. Cache and reuse tokens. |

---

## 3. Submit invoices — `POST /api/ingest`

The one endpoint you use to submit invoices. Accepts **one or many** invoices in a single call and returns an **IRN + QR code for each**.

**Auth:** `Authorization: Bearer <access_token>` (required)
**Content-Type:** `multipart/form-data`

### 3.1 Request

| Form field | Required | Meaning |
|------------|----------|---------|
| `files` | Yes | A single `.json` file whose body is a **JSON array of invoice records** (one element per invoice — see [§3.2](#32-invoice-record)). Even for a single invoice, send an array of one. Max 10 MB. |
| `batch_id` | Yes | Your identifier for this submission cycle (e.g. one per 10-minute export). Groups all records in this call. Echoed back and usable in `/api/status`. Example: `BATCH202606170930`. |
| `call_type` | Yes | Always `external`. |

> **Why a file and not a raw JSON body?** The invoice array travels as an uploaded `.json` file part so that batches of any size stream cleanly and so the same payload works unchanged over the AMQP channel ([§5](#5-amqp-channel-optional)).

### 3.2 Invoice record

Each element of the JSON array is one invoice/transaction:

| Field | Required | Meaning |
|-------|----------|---------|
| `transaction_id` | **Yes** | Your unique reference for this invoice. Must be unique across all your submissions — it is the key for de-duplication and for `/api/status` lookups, and it seeds the IRN. |
| `fee_amount` | **Yes** | The invoice/charge amount in NGN. Decimal. |
| `vat_amount` | **Yes** | VAT in NGN. Send the actual VAT you charged (Nigeria standard rate is 7.5%). |
| `description` | Recommended | Human-readable description of the charge/goods/service. |
| `transaction_date` | Recommended | Date of the transaction, `YYYY-MM-DD`. |
| `buyer_name` | For B2B | Customer/counterparty legal name. |
| `buyer_tin` | For B2B | Customer Tax Identification Number. Required for business-to-business invoices; omit for B2C. |
| `buyer_address` | For B2B | Customer address. |
| `branch` | Optional | Originating branch identifier (for your own audit/reporting). |

> Field names above are the defaults. If your core system uses different column names, Pronalytics maps them on our side at onboarding — no change on yours.

**Example request**
```bash
curl -X POST https://api.pronalytics.ng/api/ingest \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -F "call_type=external" \
  -F "batch_id=BATCH202606170930" \
  -F "files=@batch.json;type=application/json"
```

where `batch.json` is:
```json
[
  {
    "transaction_id": "TXN20260617LAG00001",
    "fee_amount": 14400.00,
    "vat_amount": 1080.00,
    "description": "Account maintenance fee",
    "transaction_date": "2026-06-17",
    "branch": "LAGOS-01"
  },
  {
    "transaction_id": "TXN20260617LAG00002",
    "fee_amount": 250000.00,
    "vat_amount": 18750.00,
    "description": "Loan origination fee",
    "transaction_date": "2026-06-17",
    "buyer_name": "Acme Industries Ltd",
    "buyer_tin": "02345678-0001",
    "buyer_address": "12 Marina Road, Lagos",
    "branch": "LAGOS-01"
  }
]
```

### 3.3 Response — `200 OK`

One result envelope for the whole batch, with a per-invoice breakdown:

```json
{
  "status": "partial",
  "batch_id": "BATCH202606170930",
  "trace_id": "019d74bc-ce7e-75a2-a79b-aca8f484764e",
  "summary": { "total": 2, "processed": 1, "duplicates": 1, "failed": 0 },
  "processed": [
    {
      "transaction_id": "TXN20260617LAG00002",
      "irn": "TXN20260617LAG00002-B673FBAF-20260617",
      "qr_code": "iVBORw0KGgoAAAANSUhEUgAA...",
      "data_uuid": "550e8400-e29b-41d4-a716-446655440000",
      "fee_amount": 250000.00,
      "vat_amount": 18750.00
    }
  ],
  "duplicates": [
    {
      "transaction_id": "TXN20260617LAG00001",
      "message": "Already received in a previous batch",
      "duplicate_of": { "irn": "TXN20260617LAG00001-B673FBAF-20260617", "data_uuid": "…", "batch_id": "…" }
    }
  ],
  "failed": []
}
```

**Envelope fields**

| Field | Meaning |
|-------|---------|
| `status` | `ok` (all processed), `partial` (some duplicates/failures), or `rejected` (none processed). |
| `batch_id` | Echo of your submitted `batch_id`. |
| `trace_id` | Server-side correlation ID. Quote it in any support request. |
| `summary` | Counts: `total`, `processed`, `duplicates`, `failed`. |
| `processed[]` | One entry per successfully fiscalized invoice (see below). |
| `duplicates[]` | Invoices already seen in a prior batch (idempotent — not re-charged), with `duplicate_of` pointing at the original. |
| `failed[]` | Invoices that could not be processed, each with `transaction_id` and a human-readable `error`. |

**`processed[]` entry**

| Field | Meaning |
|-------|---------|
| `transaction_id` | Your reference, echoed. |
| `irn` | **Invoice Reference Number** — the FIRS-recognised identifier for this invoice. Store it. |
| `qr_code` | The **QR code** for this invoice, Base64-encoded. Decode and stamp it onto the invoice document you issue to your customer. One QR per invoice. |
| `data_uuid` | Relay's internal storage handle for this invoice. Useful for support/reconciliation. |
| `fee_amount`, `vat_amount` | Echoed back for your reconciliation. |

> **Duplicates are safe.** Re-submitting the same `transaction_id` returns it in `duplicates[]` with the original IRN — it is never double-fiscalized. This makes retries idempotent: if a network error leaves you unsure whether a batch landed, just resubmit it.

### 3.4 Ingest errors

The whole request fails (no invoices processed) for these; see [§6](#6-error-reference) for the full table:

| HTTP | `error_code` | Cause |
|------|--------------|-------|
| `401` | `AUTHENTICATION_FAILED` | Missing/invalid/expired bearer token. |
| `400` | `VALIDATION_FAILED` | The `files` part is missing, not valid JSON, or not an array. |
| `413`/`400` | `FILE_SIZE_EXCEEDED` | The `.json` file is over 10 MB. Split the batch. |
| `429` | `RATE_LIMIT_EXCEEDED` | Daily submission quota exhausted (see [§7](#7-limits)). |

Per-invoice problems (bad/missing fields on individual records) do **not** fail the request — they come back in the `failed[]` array so the rest of the batch still processes.

---

## 4. Check status — `POST /api/status`

Look up the current processing/FIRS status of invoices you have submitted.

**Auth:** `Authorization: Bearer <access_token>` (required)
**Content-Type:** `application/json`

### 4.1 Request

Provide **one** of the following selectors:

| Field | Meaning |
|-------|---------|
| `transaction_id` | Status of a single invoice by your reference. |
| `irn` | Status of a single invoice by its IRN. |
| `batch_id` | Status of every invoice in a submission batch. |

```bash
curl -X POST https://api.pronalytics.ng/api/status \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN20260617LAG00002"}'
```

### 4.2 Response — `200 OK`

```json
{
  "results": [
    {
      "transaction_id": "TXN20260617LAG00002",
      "irn": "TXN20260617LAG00002-B673FBAF-20260617",
      "batch_id": "BATCH202606170930",
      "result": "processed",
      "firs_status": "ACCEPTED",
      "received_at": "2026-06-17T09:30:12Z",
      "processed_at": "2026-06-17T09:30:14Z"
    }
  ]
}
```

| Field | Meaning |
|-------|---------|
| `result` | The middleware-side outcome: `processed`, `duplicate`, `failed`, or `pending`. |
| `firs_status` | The downstream FIRS lifecycle state for the invoice (e.g. `PENDING`, `TRANSMITTED`, `ACCEPTED`, `REJECTED`). `null` until transmission begins. |
| `received_at` / `processed_at` | Timestamps (UTC, ISO 8601). |

> A `transaction_id` not found returns an empty `results` array — never an error. A `batch_id` returns one entry per invoice in that batch.

---

## 5. AMQP channel (optional)

For high-volume or fire-and-forget integrations, you may submit the **same invoice payload** over AMQP instead of REST. Everything else — the record schema ([§3.2](#32-invoice-record)), the IRN/QR result shape, de-duplication, and `/api/status` for results — is identical.

| Property | Value |
|----------|-------|
| Broker connection URL | Provided by Pronalytics at onboarding (per-tenant, credentialed). |
| Publish target (exchange / routing key) | Provided at onboarding. |
| Message body | The **same JSON array of invoice records** you would put in the REST `files` part. |
| Message properties | Set `message_id` = your `batch_id`; `content_type` = `application/json`. |
| Results | Delivered to your per-tenant reply queue, **and** queryable any time via `/api/status` ([§4](#4-check-status--apistatus)) using `batch_id`/`transaction_id`. |

> The AMQP terminus is your tenant's Relay ingestion endpoint — you never address Core or any other service. Use REST if you want a synchronous IRN/QR in the HTTP response; use AMQP if you prefer to decouple submission from result retrieval.

---

## 6. Error reference

All REST errors (except OAuth token errors, which follow RFC 6749 — see [§2.4](#24-token-errors)) share this shape:

```json
{ "status": "error", "error_code": "VALIDATION_FAILED", "message": "Human-readable explanation.", "details": [ … ] }
```

| HTTP | `error_code` | Meaning | Action |
|------|--------------|---------|--------|
| `400` | `VALIDATION_FAILED` | The upload is missing, not valid JSON, or not a record array. | Fix the `files` payload. |
| `400` | `NO_FILES_PROVIDED` | No `files` part in the request. | Attach the `.json` file. |
| `400` | `FILE_SIZE_EXCEEDED` | `.json` file over 10 MB. | Split into smaller batches. |
| `401` | `AUTHENTICATION_FAILED` | Bearer token missing, malformed, or expired. | Get a fresh token ([§2.2](#22-get-an-access-token)). |
| `403` | `FORBIDDEN` | Token valid but lacks `invoice:submit` scope. | Contact Pronalytics. |
| `409` | `DUPLICATE_FILE` | The exact batch file was already submitted. | Safe to ignore — already processed. |
| `429` | `RATE_LIMIT_EXCEEDED` | Daily submission quota exhausted. | Resume after midnight UTC, or request a higher quota. |
| `500` | `INTERNAL_ERROR` | Unexpected server error. | Retry with backoff; if persistent, contact support with `trace_id`. |
| `503` | `SERVICE_UNAVAILABLE` | Middleware temporarily unable to process. | Retry with backoff. |

---

## 7. Limits

| Limit | Default | Scope |
|-------|---------|-------|
| Access-token lifetime | 15 minutes (`expires_in: 900`) | Per token |
| Token requests | 60 / minute | Per `client_id` |
| Invoice submissions | 500 invoices / day | Per tenant (`client_id`) |
| Batch file size | 10 MB | Per `/api/ingest` request |

Quota increases are available — contact Pronalytics.

---

## 8. Parameter glossary

| Term | Meaning |
|------|---------|
| **IRN** | Invoice Reference Number — the FIRS-recognised unique identifier returned per invoice. The thing you store and reference. |
| **QR code** | Base64-encoded QR image returned per invoice; stamp it on the invoice you issue. |
| `client_id` / `client_secret` | Your OAuth credentials. The `id` is public; the `secret` is shown once and stored securely. |
| `access_token` | Short-lived (15-min) bearer JWT presented on every Relay call. |
| `transaction_id` | **Your** unique reference per invoice — the de-dup key and status-lookup key. |
| `batch_id` | **Your** identifier for one submission cycle; groups invoices for status lookups. |
| `data_uuid` | Relay's internal storage handle for a submitted invoice. |
| `trace_id` | Server correlation ID for a request — quote it in support tickets. |
| `firs_status` | Downstream FIRS lifecycle state of an invoice (from `/api/status`). |

---

## Appendix A — Worked example (Python)

```python
import requests, json

BASE = "https://api.pronalytics.ng"
CLIENT_ID = "oa_3f8a91c2_abm"
CLIENT_SECRET = "YOUR_SECRET"

# 1. Get a token (cache and reuse until it nears 15-min expiry)
tok = requests.post(
    f"{BASE}/api/auth/oauth/token",
    data={
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "invoice:submit",
    },
).json()
access_token = tok["access_token"]

# 2. Submit a batch
batch = [
    {"transaction_id": "TXN20260617LAG00001", "fee_amount": 14400.00,
     "vat_amount": 1080.00, "description": "Account maintenance fee",
     "transaction_date": "2026-06-17", "branch": "LAGOS-01"},
]
resp = requests.post(
    f"{BASE}/api/ingest",
    headers={"Authorization": f"Bearer {access_token}"},
    data={"call_type": "external", "batch_id": "BATCH202606170930"},
    files={"files": ("batch.json", json.dumps(batch), "application/json")},
).json()

for inv in resp["processed"]:
    print(inv["transaction_id"], "->", inv["irn"])
    # base64-decode inv["qr_code"] and stamp onto your invoice PDF

# 3. Check status later
status = requests.post(
    f"{BASE}/api/status",
    headers={"Authorization": f"Bearer {access_token}"},
    json={"batch_id": "BATCH202606170930"},
).json()
print(status["results"])
```

---

*Pronalytics Middleware API — external integration reference. Endpoints and credentials are provided by Pronalytics at onboarding. For support, quote your `trace_id`.*
