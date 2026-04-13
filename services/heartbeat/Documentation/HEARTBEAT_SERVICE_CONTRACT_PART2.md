# HeartBeat Service Contract — Part 2: DataBox, BulkContainer & SDK Integration

**Version:** 3.1
**Date:** 2026-02-19
**Status:** AUTHORITATIVE
**Audience:** Float App team, SDK team, Relay team
**Changelog:** v3.1 — Updated response shapes (data_uuid, filenames[], call_type, trace_id), removed stale finalize endpoint, added E2EE to upload flow, formal status definitions, added rapid-fire/concurrency section

---

## 1. Architecture Overview

```
┌───────────────────────────────────────────────────────────────────────┐
│ Float Application (PySide6)                                           │
│                                                                       │
│  ┌─────────────────────┐   ┌──────────────────────────────────────┐  │
│  │ BulkContainer       │   │ DataBox (one per tab)                │  │
│  │ (QFrame)             │   │ ├── SuperWhiteDataBox (SWDB table)  │  │
│  │                      │   │ ├── SDK Manager (InvoiceManager...) │  │
│  │ • File drag-drop     │   │ └── DataService (transform layer)   │  │
│  │ • "Select Files"     │   │                                     │  │
│  │ • File validation    │   │  5 tabs:                            │  │
│  │ • "Send to Queue"    │   │   Queue     → UploadManager         │  │
│  │   ↓                  │   │   eInvoices → InvoiceManager         │  │
│  │ UploadWorker(QThread)│   │   Contacts  → ContactManager (TBD)  │  │
│  │   ↓                  │   │   Products  → ProductManager (TBD)  │  │
│  │ RelayClient          │   │   Notifs    → NotificationManager   │  │
│  └──────────┬───────────┘   └──────────────────┬─────────────────┘  │
│             │                                   │                     │
│             │ POST /api/ingest                  │ sync.db / invoices.db
│             │ (HMAC-SHA256 + E2EE)              │ (local SQLite)      │
│             │ (call_type=bulk)                  │                     │
└─────────────┼───────────────────────────────────┼─────────────────────┘
              │                                   │
              ▼                                   │
┌─────────────────────────┐                       │
│ Relay (port 8082)       │                       │
│ • Decrypts E2EE payload │                       │
│ • Validates HMAC        │                       │
│ • Validates files       │──────────────────────►│
│ • Dedup check           │                       │
│ • Writes to HeartBeat   │                       │
│ • Enqueues to Core      │                       │
└──────────┬──────────────┘                       │
           │                                      │
           ▼                                      │
┌─────────────────────────┐                       │
│ HeartBeat (port 9000)   │                       │
│ • Stores blob           │                       │
│ • Tracks status         │───── SSE events ─────►│ (Phase 2: replaces polling)
│ • Registry              │                       │
│ • Audit trail           │                       │
└──────────┬──────────────┘                       │
           │                                      │
           ▼                                      │
┌─────────────────────────┐                       │
│ Core (port 8080)        │                       │
│ • Extracts invoice data │                       │
│ • Validates content     │                       │
│ • Updates sync.db ──────┼───────────────────────┘
│ • Updates blob status   │
│ • Owns queue status     │
│ • Owns finalization     │
└─────────────────────────┘
```

---

## 2. BulkContainer → Relay Upload Flow

### 2.1 What BulkContainer Is

BulkContainer is a `QFrame` widget on the right side of the Queue tab. It provides:
- Drag-and-drop file zone
- "Select Files" button (max 3 files)
- File validation (type, size)
- "Send to Queue" button

It is **not** a standalone component — it's part of `BulkUploadMixin` in `Float/App/src/application/bulk_upload_panel.py`.

### 2.2 Upload Flow (Step by Step)

```
User drops files onto BulkContainer
    │
    ▼
1. BulkContainer validates files (type, size, count ≤ 3)
    │
    ▼
2. User clicks "Send to Queue"
    │
    ▼
3. UploadManager.stage_files(file_paths)
   • Copies files to {uploads_dir}/{upload_id}/
   • Writes row to sync.db uploads table
   • Status: "staged"
    │
    ▼
4. UploadWorker(QThread) starts
   • Runs in background thread (UI stays responsive)
   • Calls RelayClient.upload_files(file_paths)
    │
    ▼
5. RelayClient encrypts + signs request:
   a. Encrypt body with Relay's E2EE public key (see Part 1 §3.7)
   b. Set header: X-Encrypted: true
   c. Sign with HMAC-SHA256:
      • body_hash = SHA256(encrypted_body_bytes)
      • message = "{api_key}:{timestamp}:{body_hash}"
      • signature = HMAC-SHA256(secret, message)
   d. Set headers: X-API-Key, X-Timestamp, X-Signature
    │
    ▼
6. POST http://{relay_url}/api/ingest (multipart/form-data)
   • call_type=bulk (form field)
   • files[] (multipart file uploads)
    │
    ▼
7. Relay processes request:
   a. Decrypts E2EE payload (if X-Encrypted: true)
   b. Validates HMAC signature (on decrypted body)
   c. Validates files (count, size, extensions)
   d. Checks daily rate limit: Redis (primary) → HeartBeat (fallback) → allow
   e. Checks dedup: SHA256 hash → session cache → HeartBeat
   f. ★ COMMIT: Writes blob to HeartBeat (POST /api/blobs/write)
   g. Enqueues to Core for processing (best-effort)
   h. Registers blob metadata in HeartBeat (fire-and-forget)
   i. Logs audit event in HeartBeat (fire-and-forget)
   j. Waits for Core preview response (up to 5 min)
    │
    ▼
8. Relay returns response to Float:
   {
     "status": "processed | queued | error",
     "data_uuid": "uuid-v4",
     "queue_id": "queue_uuid-v4",
     "filenames": ["invoice1.pdf", "invoice2.xlsx"],
     "file_count": 2,
     "file_hash": "sha256-hex",
     "trace_id": "trc_uuid-v4",
     "preview_data": { ... }   // Only when status=processed
   }
    │
    ▼
9. UploadManager updates sync.db:
   • Status: "staged" → "queued" (if status=queued) or "staged" → "processed" (if status=processed)
   • Stores data_uuid, queue_id, trace_id
    │
    ▼
10. DataBox (Queue tab) refreshes from UploadManager.list_uploads()
    • User sees file status in SWDB table
```

### 2.3 Response Status Definitions (CANONICAL)

These are the formally defined status values in Relay's response:

| Status | Meaning | When It Happens | Caller Action |
|---|---|---|---|
| `processed` | Core returned a response within the timeout window. | Bulk: Core returned preview data within 5 min. External: Core accepted + IRN/QR generated locally. | Bulk: display `preview_data`. External: use `irn` and `qr_code`. |
| `queued` | Data is safely stored but Core did not respond in time. | Core timed out (5 min), Core unreachable, or Core returned a transient error. Blob IS committed in HeartBeat — data is NOT lost. | Poll Core directly for queue status. File will be processed eventually. Orphan recovery handles worst case. |
| `error` | A failure occurred. The request was NOT successfully processed. | Validation failed (400), rate limit exceeded (429), duplicate detected (409), internal error (500), or Core returned an explicit permanent error. | Inspect `error_code` and `message` fields in the error response. Fix the issue and retry, or report to the user. |

> **There is no "accepted" status.** Both bulk and external flows always attempt to contact Core. The only difference is whether Relay waits for Core's response (bulk: yes, up to 5 min) or fires-and-forgets (external: returns immediately with locally-generated IRN/QR). Either way, if Core responds → `processed`. If Core doesn't respond → `queued`.

### 2.4 Where Credentials Come From

The upload flow needs API credentials at two points:

**Point A: Float → Relay (HMAC signing + E2EE)**
```python
# Today: hardcoded or env var
api_key = os.environ.get("HELIUM_API_KEY", "client_api_key_12345")
api_secret = os.environ.get("HELIUM_API_SECRET", "shared_secret_xyz")

# Phase 2: fetched from HeartBeat at startup
config = GET /api/registry/config/float-sdk:{instance_id}
api_key = config["api_key"]
relay_public_key = config["encryption_public_key"]  # For E2EE
# api_secret comes from env var (never stored in config API response)
```

**Point B: Relay → HeartBeat (Bearer token)**
```python
# Relay uses its own credentials (set at install time)
headers = {"Authorization": f"Bearer {relay_api_key}:{relay_api_secret}"}
```

Float never talks directly to HeartBeat for blob operations. Relay is the intermediary.

### 2.5 Tier-Specific Relay Startup

| Tier | How Relay Runs | Float's Responsibility |
|---|---|---|
| **Test/Standard** | Float starts Relay as subprocess on localhost:8082 | Start on init, stop on close, check /health |
| **Pro/Enterprise** | Relay runs as separate service (Docker or remote) | Just connect — no subprocess management |

```python
# Test/Standard: Float manages Relay lifecycle
if tier in ["test", "standard"]:
    relay_launcher = RelayBulkLauncher(port=8082)
    relay_launcher.start(timeout=6)  # Wait for /health

# Pro/Enterprise: Relay is remote
else:
    relay_url = config["relay_url"]  # From HeartBeat config
```

---

## 3. DataBox → SDK Data Fetch Flow

### 3.1 What DataBox Is

DataBox is a `QWidget` container that bridges Float's layout to the `SuperWhiteDataBox` (SWDB) spreadsheet. There are **5 DataBox instances** (lazy-created, one per tab):

| Tab | DataBox SDK Source | Data Source DB |
|---|---|---|
| Queue | `UploadManager.list_uploads()` | sync.db (uploads table) |
| eInvoices | `InvoiceManager.list_invoices()` via `DataService.get_invoices()` | invoices.db / sync.db |
| Contacts | `InvoiceDatabase.list_customers()` via `DataService.get_customers()` | sync.db |
| Products | `InvoiceDatabase.list_products()` via `DataService.get_products()` | sync.db |
| Notifications | SSE event callbacks | HeartBeat SSE stream |

### 3.2 Data Loading Priority Chain

DataBox's `load_data()` tries sources in order:

```
1. Queue tab       → UploadManager.list_uploads()
2. DataService     → DataService.get_invoices() / get_customers() / get_products()
3. Raw SDK         → InvoiceManager.list_invoices() (backwards-compatible)
4. Sample data     → Hardcoded fixtures (dev mode fallback)
```

### 3.3 DataService Transform Layer

`DataService` (Float App team builds this) converts SDK Pydantic models to `list[dict]` for SWDB:

```python
# eInvoices tab
response = db.list_invoices(filters=..., limit=1000)
rows = [invoice_to_row(inv) for inv in response.invoices]
data_box.set_data(rows)

# Contacts tab
result = db.list_customers(limit=1000)
rows = [customer_to_row(c) for c in result["customers"]]
data_box.set_data(rows)

# Products tab
result = db.list_products(limit=1000)
rows = [product_to_row(p) for p in result["products"]]
data_box.set_data(rows)

# Queue/Files tab
result = db.list_files(limit=200, status_filter=["uploaded", "processing", "finalized"])
rows = [file_to_row(f) for f in result["files"]]
data_box.set_data(rows)
```

### 3.4 SDK Database Initialization

```python
from SDK.src.ws1_database.database import InvoiceDatabase

db = InvoiceDatabase(
    invoices_db_path="data/invoices.db",     # From HeartBeat config or env
    sync_db_path="data/sync.db",             # From HeartBeat config or env
    auto_init=True,                          # Create schema if missing
)
db.initialize()
```

**Phase 2 (HeartBeat-driven):**
```python
# Resolve paths from HeartBeat
config = GET /api/registry/config/float-sdk:{instance_id}
shared = GET /api/registry/config/_shared
tenant_id = shared["tenant_id"]
base_path = shared["data_base_path"]
instance_id = config["instance_id"]

sync_path = f"{base_path}/sync_{tenant_id}_{instance_id}.db"
queue_path = f"{base_path}/core_queue_{tenant_id}.db"

db = InvoiceDatabase(
    invoices_db_path=...,   # Core's DB path (from database catalog)
    sync_db_path=sync_path,
    auto_init=True,
)
```

### 3.5 Live Updates via SSE (Phase 2)

When HeartBeat Phase 2 SSE is ready, DataBox tabs refresh automatically:

```python
# EventProcessor subscribes to HeartBeat SSE stream
# GET /api/v1/events/blobs (text/event-stream)

event_processor.register_callback(
    "blob.status_changed",
    lambda event: refresh_queue_tab(),      # Queue tab: upload status changed
)
event_processor.register_callback(
    "invoice.created",
    lambda event: refresh_einvoices_tab(),  # eInvoices tab: new invoice from Core
)
event_processor.register_callback(
    "customer.created",
    lambda event: refresh_contacts_tab(),   # Contacts tab: new customer
)
event_processor.register_callback(
    "blob.finalized",
    lambda event: refresh_files_tab(),      # Files tab: processing complete
)
```

Until SSE is implemented, DataBox uses polling with configurable interval.

---

## 4. BulkContainer ↔ SDK ↔ Relay Integration Contracts

### 4.1 SDK Components Used by BulkContainer

| Component | File | What It Does |
|---|---|---|
| `RelayClient` | `sdk/relay_client.py` | HMAC-signed + E2EE HTTP client for Relay |
| `UploadWorker` | `sdk/upload_worker.py` | QThread wrapper for non-blocking upload |
| `UploadManager` | `sdk/upload_manager.py` | Local-first staging: stage → send → track |
| `HMACSigner` | Internal to RelayClient | Signs requests with SHA256(key:timestamp:body_hash) |

### 4.2 RelayClient API

```python
class RelayClient:
    def __init__(self, relay_url: str, api_key: str, secret: str,
                 company_id: str, user_id: Optional[str] = None,
                 encryption_public_key: Optional[bytes] = None)

    async def upload_files(self, file_paths: List[Path],
                           call_type: str = "bulk",
                           timeout: int = 300) -> Dict[str, Any]
    # → POST {relay_url}/api/ingest
    # Returns: {status, data_uuid, queue_id, filenames, file_count, file_hash, trace_id}
    # Bulk: may also include preview_data
    # External: may also include irn, qr_code
```

> **REMOVED**: `finalize_batch()` — Finalization is Core's endpoint, not Relay's. Float SDK calls Core directly for finalization via `POST {core_url}/api/finalize`.

### 4.3 UploadManager API

```python
class UploadManager:
    def stage_files(self, file_paths: List[str]) -> str
    # Copies to local store, writes to sync.db, returns upload_id

    def send_to_relay(self, upload_id: str) -> None
    # Starts UploadWorker QThread

    def list_uploads(self, status_filter=None) -> List[Dict]
    # Reads from sync.db uploads table

    def retry_failed(self, upload_id: str) -> None
    # Resets status to "staged", re-sends via POST /api/ingest
    # No separate retry endpoint needed — same /api/ingest endpoint

    def cancel_upload(self, upload_id: str) -> None
    # Removes staged files, updates sync.db
```

### 4.4 UploadWorker Signals

```python
class UploadWorker(QThread):
    upload_finished = Signal(dict)    # Emits relay response on success
    upload_failed = Signal(str)       # Emits error message on failure
    upload_progress = Signal(int)     # Emits percentage (0-100)
```

### 4.5 BulkContainer Wiring (Float App side)

```python
# In BulkUploadMixin._on_upload_clicked()
from sdk import UploadWorker

worker = UploadWorker(
    relay_client=self._relay_client,
    file_paths=validated_file_paths,
    call_type="bulk",
)
worker.upload_finished.connect(self._on_upload_success)
worker.upload_failed.connect(self._on_upload_failed)
worker.start()  # Non-blocking — runs in QThread
```

---

## 5. DataBox ↔ SDK Data Contracts

### 5.1 Field Mappings (SDK Model → SWDB dict)

**eInvoices Tab:**
```python
{
    "invoice_no": inv.invoice_number,
    "issue_date": inv.issue_date.strftime("%d %b %Y"),
    "due_date": inv.due_date.strftime("%d %b %Y"),
    "customer": inv.customer_name,
    "status": inv.status.value.title(),
    "amount": f"₦{inv.total_amount:,.2f}",
    "tax": f"₦{inv.total_tax:,.2f}",
    "items": len(inv.line_items),
    "_invoice_id": inv.invoice_id,       # Hidden — for click-through
}
```

**Contacts Tab:**
```python
{
    "name": c["company_name"],
    "tin": c.get("tin", "—"),
    "email": c.get("email", "—"),
    "phone": c.get("phone", "—"),
    "address": c.get("address_line1", "—"),
    "_customer_id": c["customer_id"],     # Hidden
}
```

**Products Tab:**
```python
{
    "name": p["product_name"],
    "sku": p.get("sku", "—"),
    "barcode": p.get("barcode", "—"),
    "price": f"₦{p.get('unit_price', 0):,.2f}",
    "tax_rate": f"{p.get('tax_rate', 0) * 100:.1f}%",
    "category": p.get("category", "—"),
    "_product_id": p["product_id"],       # Hidden
}
```

**Queue / Files Tab:**
```python
{
    "filename": f.get("display_name") or f["original_filename"],
    "status": f["status"].replace("_", " ").title(),
    "size": format_bytes(f.get("file_size_bytes", 0)),
    "type": f.get("content_type", "—"),
    "uploaded": f.get("uploaded_at_iso", "—"),
    "batch": f.get("batch_uuid", "—")[:8] + "…",
    "_file_id": f["file_id"],             # Hidden
    "_data_uuid": f.get("data_uuid"),     # Hidden (was blob_uuid)
}
```

### 5.2 Pagination

All list methods support `limit` and `offset`:
```python
result = db.list_invoices(limit=50, offset=0)
# result.has_more: bool
# result.total_count: int
```

### 5.3 Filtering

| Method | Filter Options |
|---|---|
| `list_invoices()` | `InvoiceFilter(status, customer_id, date_from, date_to, amount_min, amount_max)` |
| `list_customers()` | `search="query"` — matches name OR email |
| `list_products()` | `category="Beverages"`, `search="query"` — matches name OR sku |
| `list_files()` | `status_filter=["uploaded", "processing", "finalized"]` |

---

## 6. HeartBeat Endpoints Used by Float/SDK

| Caller | HeartBeat Endpoint | When | Purpose |
|---|---|---|---|
| SDK startup | `POST /api/registry/register` | Boot | Register Float instance |
| SDK startup | `GET /api/registry/config/float-sdk:{id}` | Boot | Get config (paths, tenant_id, encryption key) |
| SDK startup | `GET /api/registry/config/_shared` | Boot | Get shared config (tier, base_path) |
| SDK startup | `POST /api/databases/register` | Boot | Register sync.db in catalog |
| SDK polling | `GET /api/v1/heartbeat/blob/{uuid}/status` | Periodic | Track upload progress |
| SDK (Phase 2) | `GET /api/v1/events/blobs` | SSE | Real-time blob status push |
| SDK | `GET /api/registry/discover/relay` | On-demand | Find Relay URL if not in config |
| BulkContainer | (via Relay) | Upload | Indirect — Relay calls HeartBeat |

**Float/SDK never calls HeartBeat blob write/register directly.** That's Relay's job.

---

## 7. Error Handling

### 7.1 BulkContainer Upload Errors

| Error | Source | HTTP | User Impact | Recovery |
|---|---|---|---|---|
| File too large | BulkContainer validation | — | Red text on file item | Remove file, select smaller |
| Wrong file type | BulkContainer validation | — | Red text on file item | Select supported format |
| Relay unreachable | UploadWorker | — | "Service unavailable" dialog | Retry later |
| HMAC auth failure | Relay | 401 | "Authentication failed" | Check credentials |
| Encryption required | Relay | 403 | "Encryption required" | SDK must encrypt (see Part 1 §3.7) |
| Validation failed | Relay | 400 | "Invalid file" | Fix file and retry |
| Dedup rejection | Relay → HeartBeat | 409 | "File already uploaded" | Skip or force re-upload |
| Daily limit exceeded | Relay → Redis/HeartBeat | 429 | "Daily limit reached" | Wait until tomorrow |
| Upload timeout | RelayClient (5 min) | — | "Upload timed out" | Retry — file may be processing |
| Module not loaded | Relay (external only) | 503 | "Service unavailable" | Wait for cache refresh |

### 7.2 DataBox Data Fetch Errors

| Error | Source | Fallback |
|---|---|---|
| sync.db missing | SDK database init | Create from schema (auto_init=True) |
| InvoiceManager not built | Import error | Fall back to sample data |
| HeartBeat config unavailable | Startup config fetch | Use env vars → tier defaults |
| SSE connection lost | EventSource disconnect | Fall back to polling |

---

## 8. Complete Credential + Encryption Flow

End-to-end, showing where every credential and encryption key comes from:

```
INSTALL TIME:
1. Installer calls HeartBeat: POST /api/registry/credentials/generate
   → Gets: fl_test_0e008e8xy0... (api_key) + Kx8mP2qR... (api_secret)
2. Installer writes to Float's env: HELIUM_API_KEY, HELIUM_API_SECRET
3. Installer calls HeartBeat: POST /api/registry/credentials/generate
   → Gets Relay credentials → writes to Relay's env
4. Installer generates Relay X25519 keypair:
   → Private key → RELAY_PRIVATE_KEY_PATH (Relay's env)
   → Public key → HeartBeat service_config: relay.encryption_public_key
5. Installer seeds service_config with tenant_id, instance_id, paths

FLOAT STARTUP:
1. SDK reads HELIUM_API_KEY, HELIUM_API_SECRET from env
2. SDK calls HeartBeat: POST /api/registry/register (Bearer key:secret)
   → Gets peer catalog (finds Relay URL)
3. SDK calls HeartBeat: GET /api/registry/config/float-sdk:{instance_id}
   → Gets tenant_id, data_base_path, relay_url
4. SDK calls HeartBeat: GET /api/registry/config/relay
   → Gets encryption_public_key (base64-encoded 32 bytes)
5. SDK constructs RelayClient(relay_url, api_key, api_secret, encryption_public_key)
6. SDK opens sync.db at resolved path

USER UPLOADS:
1. BulkContainer collects files
2. UploadWorker calls RelayClient.upload_files()
   → E2EE encrypted with Relay's public key
   → HMAC-signed with api_key + api_secret
3. Relay decrypts E2EE payload
4. Relay validates HMAC signature
5. Relay runs 7-step ingestion pipeline
6. Relay calls HeartBeat (Bearer relay_key:relay_secret)
   → Blob write, register, dedup, audit
7. Relay calls Core to enqueue processing
8. Core processes file, updates HeartBeat blob status
9. SDK polls HeartBeat for status (Bearer key:secret)
10. DataBox refreshes when status changes
```

---

## 9. Relay Response Shapes (Cross-Reference)

For the definitive response shape contract, see **Relay Service Contract** (`Relay/Documentation/RELAY_SERVICE_CONTRACT.md`). Key fields summarized here for SDK team convenience:

### 9.1 Ingest Response (POST /api/ingest)

| Field | Type | Always present | Description |
|---|---|---|---|
| `status` | string | Yes | `"processed"`, `"queued"`, or `"error"` |
| `data_uuid` | string | Yes | Unique blob identifier (UUID v4) |
| `queue_id` | string | Yes | Core processing queue ID |
| `filenames` | string[] | Yes | Array of uploaded filenames |
| `file_count` | int | Yes | Number of files in this request |
| `file_hash` | string | Yes | SHA256 hash of blob data |
| `trace_id` | string | Yes | Request trace ID for debugging |
| `preview_data` | object | Bulk + processed only | Invoice preview from Core |
| `irn` | string | External + processed only | Invoice Reference Number |
| `qr_code` | string | External + processed only | QR code data (base64) |

### 9.2 Error Response

| Field | Type | Description |
|---|---|---|
| `status` | string | Always `"error"` |
| `error_code` | string | Machine-readable code (e.g., `DUPLICATE_FILE`) |
| `message` | string | Human-readable message |
| `details` | array | Optional array of `{field, error}` objects |

---

*End of Part 2. See Part 1 for Registry & Credentials foundation, Part 3 for Observability, Updates & Lifecycle.*
