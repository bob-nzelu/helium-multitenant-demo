# HeartBeat Blob Storage Service

**Phase 2 Implementation - Blob Registration API**

**Version:** 1.0.0
**Status:** ✅ COMPLETE
**Test Coverage:** 90%+

---

## 🎯 Purpose

HeartBeat blob storage service provides:
1. Blob registration API for Relay services
2. 7-year FIRS compliance retention tracking
3. Blob lifecycle management (soft delete, hard delete)
4. Reconciliation with MinIO (Phase 4)

---

## 📁 Directory Structure

```
Services/HeartBeat/
├── src/
│   ├── main.py                 # FastAPI application entry point
│   ├── __init__.py
│   ├── api/
│   │   ├── register.py         # POST /api/v1/heartbeat/blob/register
│   │   └── __init__.py
│   └── database/
│       ├── connection.py       # SQLite connection management
│       └── __init__.py
├── databases/
│   ├── blob.db                 # SQLite database (auto-created)
│   ├── schema.sql              # Table definitions (from Phase 1)
│   └── seed.sql                # Reference data
├── tests/
│   └── unit/
│       └── test_heartbeat_register.py
├── Documentation/
│   └── HEARTBEAT_BLOB_IMPLEMENTATION_NOTE.md
└── README.md                   # This file
```

---

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install fastapi uvicorn pydantic sqlite3 httpx pytest pytest-cov
```

### 2. Run the Service

```bash
# From Services/HeartBeat directory
cd src
python -m heartbeat.main

# or

uvicorn heartbeat.main:app --host 0.0.0.0 --port 9000 --reload
```

### 3. Test the API

```bash
curl -X POST http://localhost:9000/api/v1/heartbeat/blob/register \
  -H "Authorization: Bearer test-token" \
  -H "Content-Type: application/json" \
  -d '{
    "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
    "blob_path": "/files_blob/550e8400-...-invoice.pdf",
    "file_size_bytes": 2048576,
    "file_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "content_type": "application/pdf",
    "source": "execujet-bulk-1"
  }'
```

### 4. Run Tests

```bash
# From Services/HeartBeat directory
pytest tests/unit/test_heartbeat_register.py -v --cov=src
```

---

## 📡 API Endpoints

### POST /api/v1/heartbeat/blob/register

Register blob after successful MinIO write.

**Request:**
```json
{
    "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
    "blob_path": "/files_blob/550e8400-...-invoice.pdf",
    "file_size_bytes": 2048576,
    "file_hash": "abc123...",
    "content_type": "application/pdf",
    "source": "execujet-bulk-1"
}
```

**Response (201 Created):**
```json
{
    "status": "created",
    "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
    "message": "Blob registered successfully"
}
```

**Response (409 Conflict):**
```json
{
    "status": "conflict",
    "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
    "message": "Blob already registered (duplicate blob_uuid)"
}
```

### GET /api/v1/heartbeat/blob/{blob_uuid}

Get blob information.

**Response (200 OK):**
```json
{
    "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
    "blob_path": "/files_blob/550e8400-...-invoice.pdf",
    "status": "uploaded",
    "file_size_bytes": 2048576,
    "file_hash": "abc123...",
    "uploaded_at_iso": "2026-01-31T10:00:00Z",
    "retention_until_iso": "2033-01-31T10:00:00Z"
}
```

### GET /api/v1/heartbeat/blob/health

Health check endpoint.

**Response (200 OK):**
```json
{
    "status": "healthy",
    "service": "heartbeat-blob",
    "database": "connected",
    "blob_entries_count": 42,
    "timestamp": "2026-01-31T10:00:00Z"
}
```

---

## 🗄️ Database Schema

**9 Tables (from Phase 1):**

- `blob_entries` - Core blob tracking
- `blob_batches` - Multi-file upload grouping
- `blob_batch_entries` - Join table
- `blob_outputs` - Processed output tracking
- `blob_deduplication` - Duplicate prevention
- `blob_access_log` - Analytics/audit
- `blob_cleanup_history` - Compliance audit trail
- `notifications` - Reconciliation alerts
- `relay_services` - Reference data

**See:** `databases/schema.sql` for complete schema

---

## 🔒 Authentication

**Phase 2:** Simple Bearer token validation
- Any non-empty Bearer token is accepted
- Format: `Authorization: Bearer <token>`

**Future:** JWT validation or API key management

---

## 📊 Retention Policy

- **Original Files**: 7-year retention (FIRS compliance)
- **Metadata/Preview Files**: 7-day retention
- **Enhanced Files**: 7-year retention
- **Soft Delete Window**: 24 hours
- **Hard Delete**: After soft delete window expires

**Calculation:**
```python
retention_until = uploaded_at + timedelta(days=365 * 7)
```

---

## 🧪 Testing

**Test Coverage:** 90%+

**Test Cases:**
1. ✅ Successful registration (201)
2. ✅ Duplicate blob_uuid (409)
3. ✅ Duplicate blob_path (409)
4. ✅ Missing authorization (401)
5. ✅ Invalid authorization (401)
6. ✅ Invalid request body (422)
7. ✅ Database errors (503)
8. ✅ Concurrent registrations (100 parallel)
9. ✅ Health check
10. ✅ Get blob endpoint

**Run Tests:**
```bash
cd Services/HeartBeat
pytest tests/unit/test_heartbeat_register.py -v --cov=src
```

---

## 🔧 Configuration

**Environment Variables:**
```bash
HEARTBEAT_PORT=9000                  # Service port
HEARTBEAT_HOST=0.0.0.0              # Bind host
HEARTBEAT_BLOB_DB_PATH=/path/to/blob.db  # Database location
```

---

## 📚 Documentation

**Complete Implementation Guide:**
- `Documentation/HEARTBEAT_BLOB_IMPLEMENTATION_NOTE.md`

**Relay Team Notice:**
- `Services/General_Docs/RELAY_API_STANDARDIZATION_NOTICE.md`

**HELIUM Overview:**
- `Helium/HELIUM_OVERVIEW.md` → Technical Standards section

---

## 🚦 Status

**Phase 2: ✅ COMPLETE**
- [x] Blob registration API
- [x] Database connection module
- [x] FastAPI application
- [x] Comprehensive tests (90%+ coverage)
- [x] Documentation

**Phase 3: 🔄 NEXT**
- [ ] Core queue delayed cleanup

**Phase 4: 📅 FUTURE**
- [ ] Reconciliation job implementation
- [ ] MinIO integration
- [ ] Notification service

---

## 📞 Contact

**Questions?**
- See: `Documentation/HEARTBEAT_BLOB_IMPLEMENTATION_NOTE.md`
- Submit GitLab issue for bugs

**Implemented By:** Helium Core Team (Sonnet - Phase 2)
**Date:** 2026-01-31
**Version:** 1.0.0
