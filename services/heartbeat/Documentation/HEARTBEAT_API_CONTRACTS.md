# HeartBeat API Contracts - Complete Specification

**Service Name:** HeartBeat
**Version:** 1.0.0
**Last Updated:** 2026-02-07
**Status:** Phase 2 Complete, Reconciliation In Progress

---

## Table of Contents

1. [Authentication](#authentication)
2. [Blob Storage APIs](#blob-storage-apis)
3. [Configuration Management APIs (config.db)](#configuration-management-apis-configdb)
4. [License Management APIs (license.db)](#license-management-apis-licensedb)
5. [Notifications APIs (notifications.db)](#notifications-apis-notificationsdb)
6. [Audit APIs (audit.db)](#audit-apis-auditdb)
7. [Health & Monitoring APIs](#health--monitoring-apis)
8. [Reconciliation APIs](#reconciliation-apis)
9. [Parent-Client APIs](#parent-client-apis)
10. [Error Responses](#error-responses)
11. [Rate Limiting](#rate-limiting)

---

## Authentication

All HeartBeat APIs require authentication via Bearer token.

**Header:**
```
Authorization: Bearer <token>
```

**Error Response (401 Unauthorized):**
```json
{
  "status": "error",
  "error_code": "UNAUTHORIZED",
  "message": "Missing or invalid authorization token"
}
```

---

## Trace ID Convention (Platform-Wide)

All HeartBeat API calls should include the `X-Trace-ID` header for distributed tracing across Helium services.

**Header:**
```
X-Trace-ID: 550e8400-e29b-41d4-a716-446655440000
```

**Behavior:**
- If provided: HeartBeat uses the given trace ID for all logging and downstream calls
- If absent: HeartBeat generates a new UUID v4 trace ID
- Always returned in response headers

**Purpose:** End-to-end request tracing across Relay -> HeartBeat -> Core. This is a **platform-wide convention** — all Helium services must support it. See `RELAY_INTEGRATION_REQUIREMENTS.md` Section 5 for full specification.

---

## Blob Storage APIs

### POST /api/v1/heartbeat/blob/register

Register blob after MinIO write (Phase 2 - IMPLEMENTED ✅).

**Request Body:**
```json
{
  "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "blob_path": "/files_blob/2026/02/07/550e8400-...-invoice.pdf",
  "file_size_bytes": 2048576,
  "file_hash": "sha256:abc123def456...",
  "content_type": "application/pdf",
  "source": "execujet-bulk-1",
  "original_filename": "invoice_12345.pdf",
  "source_type": "bulk"
}
```

**Field Validations:**
- `blob_uuid`: Required, valid UUID v4
- `blob_path`: Required, absolute path starting with `/files_blob/`
- `file_size_bytes`: Required, integer > 0
- `file_hash`: Required, format "algorithm:hash"
- `content_type`: Optional, valid MIME type
- `source`: Required, matches relay_services.service_name
- `original_filename`: Required, non-empty string
- `source_type`: Optional, Relay ingestion method per Decision 1A taxonomy: "bulk", "api", "polling", "watcher", "dbc", "email"

**Response (201 Created):**
```json
{
  "status": "created",
  "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Blob registered successfully",
  "retention_until_iso": "2033-02-07T10:00:00Z"
}
```

**Response (409 Conflict - Duplicate):**
```json
{
  "status": "conflict",
  "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Blob already registered (duplicate blob_uuid)"
}
```

**Response (400 Bad Request):**
```json
{
  "status": "error",
  "error_code": "VALIDATION_ERROR",
  "message": "Invalid blob_uuid format",
  "details": {
    "field": "blob_uuid",
    "provided": "not-a-uuid",
    "expected": "UUID v4 format"
  }
}
```

---

### GET /api/v1/heartbeat/blob/{blob_uuid}

Get blob information by UUID (Phase 2 - IMPLEMENTED ✅).

**Path Parameters:**
- `blob_uuid`: UUID v4 of the blob

**Response (200 OK):**
```json
{
  "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "blob_path": "/files_blob/2026/02/07/550e8400-...-invoice.pdf",
  "original_filename": "invoice_12345.pdf",
  "status": "uploaded",
  "file_size_bytes": 2048576,
  "file_hash": "sha256:abc123def456...",
  "content_type": "application/pdf",
  "source": "execujet-bulk-1",
  "source_type": "bulk",
  "uploaded_at_iso": "2026-02-07T10:00:00Z",
  "retention_until_iso": "2033-02-07T10:00:00Z",
  "deleted_at_iso": null
}
```

**Response (404 Not Found):**
```json
{
  "status": "error",
  "error_code": "NOT_FOUND",
  "message": "Blob not found",
  "blob_uuid": "550e8400-e29b-41d4-a716-446655440000"
}
```

---

### DELETE /api/v1/heartbeat/blob/{blob_uuid}

Soft delete blob (mark for deletion, 24-hour recovery window) (Phase 3 - PLANNED 📅).

**Path Parameters:**
- `blob_uuid`: UUID v4 of the blob

**Request Body (Optional):**
```json
{
  "reason": "user_requested",
  "deleted_by": "admin@execujet.com"
}
```

**Response (200 OK):**
```json
{
  "status": "deleted",
  "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Blob soft-deleted successfully",
  "deleted_at_iso": "2026-02-07T10:00:00Z",
  "hard_delete_at_iso": "2026-02-08T10:00:00Z"
}
```

**Response (404 Not Found):**
```json
{
  "status": "error",
  "error_code": "NOT_FOUND",
  "message": "Blob not found"
}
```

---

### POST /api/v1/heartbeat/blob/{blob_uuid}/restore

Restore soft-deleted blob within 24-hour window (Phase 3 - PLANNED 📅).

**Path Parameters:**
- `blob_uuid`: UUID v4 of the blob

**Response (200 OK):**
```json
{
  "status": "restored",
  "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Blob restored successfully"
}
```

**Response (410 Gone - Past Recovery Window):**
```json
{
  "status": "error",
  "error_code": "RECOVERY_WINDOW_EXPIRED",
  "message": "Blob recovery window expired (>24 hours since deletion)"
}
```

---

### GET /api/v1/heartbeat/blob/health

Health check endpoint (Phase 2 - IMPLEMENTED ✅).

**Response (200 OK):**
```json
{
  "status": "healthy",
  "service": "heartbeat-blob",
  "version": "1.0.0",
  "database": "connected",
  "blob_entries_count": 15234,
  "minio_connection": "healthy",
  "timestamp": "2026-02-07T10:00:00Z"
}
```

**Response (503 Service Unavailable):**
```json
{
  "status": "unhealthy",
  "service": "heartbeat-blob",
  "database": "disconnected",
  "error": "Unable to connect to blob.db",
  "timestamp": "2026-02-07T10:00:00Z"
}
```

---

## Configuration Management APIs (config.db)

HeartBeat owns **config.db** for centralized system configuration management.

### GET /api/v1/heartbeat/config

Get all configuration entries (PLANNED 📅).

**Query Parameters:**
- `namespace`: Optional, filter by namespace (e.g., "helium.relay", "helium.core")
- `environment`: Optional, filter by environment ("production", "staging", "development")

**Response (200 OK):**
```json
{
  "status": "success",
  "count": 3,
  "configs": [
    {
      "config_id": "cfg_001",
      "namespace": "helium.relay",
      "key": "max_file_size_mb",
      "value": "500",
      "data_type": "integer",
      "environment": "production",
      "description": "Maximum file size for bulk uploads",
      "is_secret": false,
      "created_at": "2026-01-15T10:00:00Z",
      "updated_at": "2026-02-01T15:30:00Z",
      "updated_by": "admin@execujet.com"
    },
    {
      "config_id": "cfg_002",
      "namespace": "helium.core",
      "key": "processing_timeout_seconds",
      "value": "3600",
      "data_type": "integer",
      "environment": "production",
      "description": "Core processing timeout",
      "is_secret": false,
      "created_at": "2026-01-15T10:00:00Z",
      "updated_at": "2026-01-15T10:00:00Z",
      "updated_by": "system"
    },
    {
      "config_id": "cfg_003",
      "namespace": "helium.relay",
      "key": "minio_access_key",
      "value": "***REDACTED***",
      "data_type": "string",
      "environment": "production",
      "description": "MinIO access key",
      "is_secret": true,
      "created_at": "2026-01-15T10:00:00Z",
      "updated_at": "2026-01-15T10:00:00Z",
      "updated_by": "system"
    }
  ]
}
```

---

### GET /api/v1/heartbeat/config/{config_id}

Get specific configuration entry by ID (PLANNED 📅).

**Path Parameters:**
- `config_id`: Unique configuration identifier

**Response (200 OK):**
```json
{
  "status": "success",
  "config": {
    "config_id": "cfg_001",
    "namespace": "helium.relay",
    "key": "max_file_size_mb",
    "value": "500",
    "data_type": "integer",
    "environment": "production",
    "description": "Maximum file size for bulk uploads",
    "is_secret": false,
    "created_at": "2026-01-15T10:00:00Z",
    "updated_at": "2026-02-01T15:30:00Z",
    "updated_by": "admin@execujet.com",
    "version": 3,
    "change_history": [
      {
        "version": 1,
        "value": "100",
        "updated_at": "2026-01-15T10:00:00Z",
        "updated_by": "system"
      },
      {
        "version": 2,
        "value": "250",
        "updated_at": "2026-01-20T14:00:00Z",
        "updated_by": "admin@execujet.com"
      },
      {
        "version": 3,
        "value": "500",
        "updated_at": "2026-02-01T15:30:00Z",
        "updated_by": "admin@execujet.com"
      }
    ]
  }
}
```

**Response (404 Not Found):**
```json
{
  "status": "error",
  "error_code": "NOT_FOUND",
  "message": "Configuration not found",
  "config_id": "cfg_999"
}
```

---

### GET /api/v1/heartbeat/config/namespace/{namespace}

Get all configuration entries for a namespace (PLANNED 📅).

**Path Parameters:**
- `namespace`: Namespace identifier (e.g., "helium.relay", "helium.core")

**Query Parameters:**
- `environment`: Optional, filter by environment

**Response (200 OK):**
```json
{
  "status": "success",
  "namespace": "helium.relay",
  "environment": "production",
  "count": 5,
  "configs": [
    {
      "config_id": "cfg_001",
      "key": "max_file_size_mb",
      "value": "500",
      "data_type": "integer",
      "description": "Maximum file size for bulk uploads",
      "is_secret": false
    },
    {
      "config_id": "cfg_005",
      "key": "enable_deduplication",
      "value": "true",
      "data_type": "boolean",
      "description": "Enable blob deduplication",
      "is_secret": false
    }
  ]
}
```

---

### POST /api/v1/heartbeat/config

Create new configuration entry (PLANNED 📅).

**Request Body:**
```json
{
  "namespace": "helium.relay",
  "key": "max_concurrent_uploads",
  "value": "10",
  "data_type": "integer",
  "environment": "production",
  "description": "Maximum concurrent uploads per service",
  "is_secret": false,
  "created_by": "admin@execujet.com"
}
```

**Field Validations:**
- `namespace`: Required, format "helium.{service}"
- `key`: Required, snake_case format
- `value`: Required, string representation
- `data_type`: Required, one of: "string", "integer", "float", "boolean", "json"
- `environment`: Required, one of: "production", "staging", "development"
- `is_secret`: Optional, default false

**Response (201 Created):**
```json
{
  "status": "created",
  "config_id": "cfg_101",
  "message": "Configuration created successfully",
  "config": {
    "config_id": "cfg_101",
    "namespace": "helium.relay",
    "key": "max_concurrent_uploads",
    "value": "10",
    "data_type": "integer",
    "environment": "production",
    "created_at": "2026-02-07T10:00:00Z"
  }
}
```

**Response (409 Conflict):**
```json
{
  "status": "error",
  "error_code": "DUPLICATE_KEY",
  "message": "Configuration key already exists in namespace",
  "namespace": "helium.relay",
  "key": "max_concurrent_uploads",
  "environment": "production"
}
```

---

### PUT /api/v1/heartbeat/config/{config_id}

Update configuration entry (PLANNED 📅).

**Path Parameters:**
- `config_id`: Configuration identifier

**Request Body:**
```json
{
  "value": "20",
  "updated_by": "admin@execujet.com",
  "change_reason": "Increased limit for new hardware"
}
```

**Response (200 OK):**
```json
{
  "status": "updated",
  "config_id": "cfg_101",
  "message": "Configuration updated successfully",
  "previous_value": "10",
  "new_value": "20",
  "version": 2,
  "updated_at": "2026-02-07T10:15:00Z"
}
```

---

### DELETE /api/v1/heartbeat/config/{config_id}

Delete configuration entry (PLANNED 📅).

**Path Parameters:**
- `config_id`: Configuration identifier

**Request Body:**
```json
{
  "deleted_by": "admin@execujet.com",
  "deletion_reason": "No longer needed"
}
```

**Response (200 OK):**
```json
{
  "status": "deleted",
  "config_id": "cfg_101",
  "message": "Configuration deleted successfully",
  "deleted_at": "2026-02-07T10:20:00Z"
}
```

---

### GET /api/v1/heartbeat/config/{config_id}/history

Get configuration change history (PLANNED 📅).

**Path Parameters:**
- `config_id`: Configuration identifier

**Response (200 OK):**
```json
{
  "status": "success",
  "config_id": "cfg_001",
  "current_value": "500",
  "history_count": 5,
  "history": [
    {
      "version": 5,
      "value": "500",
      "updated_at": "2026-02-07T10:00:00Z",
      "updated_by": "admin@execujet.com",
      "change_reason": "Performance optimization"
    },
    {
      "version": 4,
      "value": "400",
      "updated_at": "2026-02-05T14:30:00Z",
      "updated_by": "admin@execujet.com",
      "change_reason": "Client request"
    }
  ]
}
```

---

## License Management APIs (license.db)

HeartBeat owns **license.db** for centralized license validation and tracking.

### GET /api/v1/heartbeat/license

Get current license information (PLANNED 📅).

**Response (200 OK):**
```json
{
  "status": "success",
  "license": {
    "license_id": "lic_execujet_ent_001",
    "client_name": "ExecuJet Aviation",
    "license_tier": "enterprise",
    "license_key": "HELIUM-ENT-2026-XXXX-XXXX-XXXX",
    "issued_at": "2026-01-01T00:00:00Z",
    "expires_at": "2027-01-01T00:00:00Z",
    "days_until_expiry": 328,
    "status": "active",
    "features": {
      "max_locations": 50,
      "max_users": 500,
      "blob_storage_tb": 100,
      "api_rate_limit": 10000,
      "support_tier": "platinum",
      "modules": [
        "relay_bulk",
        "relay_nas",
        "relay_erp",
        "relay_email",
        "core_processing",
        "edge_analytics",
        "float_ui"
      ]
    },
    "usage": {
      "active_locations": 12,
      "active_users": 234,
      "blob_storage_used_tb": 23.5,
      "api_calls_today": 5432
    },
    "compliance": {
      "is_valid": true,
      "is_expired": false,
      "is_trial": false,
      "requires_renewal_notice": false
    }
  }
}
```

**Response (402 Payment Required - Expired License):**
```json
{
  "status": "error",
  "error_code": "LICENSE_EXPIRED",
  "message": "License has expired",
  "license_id": "lic_execujet_ent_001",
  "expired_at": "2026-01-01T00:00:00Z",
  "days_expired": 37,
  "renewal_url": "https://helium.prodeus.com/license/renew",
  "contact_email": "licensing@prodeus.com"
}
```

---

### POST /api/v1/heartbeat/license/validate

Validate license key (PLANNED 📅).

**Request Body:**
```json
{
  "license_key": "HELIUM-ENT-2026-XXXX-XXXX-XXXX",
  "client_name": "ExecuJet Aviation",
  "installation_id": "inst_execujet_hq_001"
}
```

**Response (200 OK - Valid License):**
```json
{
  "status": "valid",
  "license_id": "lic_execujet_ent_001",
  "message": "License validated successfully",
  "license_tier": "enterprise",
  "expires_at": "2027-01-01T00:00:00Z",
  "features_enabled": [
    "relay_bulk",
    "relay_nas",
    "core_processing",
    "edge_analytics"
  ]
}
```

**Response (403 Forbidden - Invalid License):**
```json
{
  "status": "error",
  "error_code": "INVALID_LICENSE",
  "message": "License key is invalid or revoked",
  "contact_email": "licensing@prodeus.com"
}
```

---

### GET /api/v1/heartbeat/license/features

Get enabled features for current license (PLANNED 📅).

**Response (200 OK):**
```json
{
  "status": "success",
  "license_tier": "enterprise",
  "features": {
    "relay_bulk": {
      "enabled": true,
      "max_file_size_mb": 500,
      "max_concurrent_uploads": 100
    },
    "relay_nas": {
      "enabled": true,
      "max_watched_directories": 50
    },
    "core_processing": {
      "enabled": true,
      "max_concurrent_jobs": 50,
      "ocr_enabled": true
    },
    "edge_analytics": {
      "enabled": true,
      "retention_days": 365
    },
    "float_ui": {
      "enabled": true,
      "max_concurrent_users": 500
    }
  }
}
```

---

### GET /api/v1/heartbeat/license/usage

Get current license usage statistics (PLANNED 📅).

**Query Parameters:**
- `period`: Optional, one of "today", "week", "month", "year" (default: "today")

**Response (200 OK):**
```json
{
  "status": "success",
  "period": "today",
  "date": "2026-02-07",
  "usage": {
    "locations": {
      "active": 12,
      "limit": 50,
      "percentage": 24
    },
    "users": {
      "active": 234,
      "limit": 500,
      "percentage": 46.8
    },
    "storage": {
      "used_tb": 23.5,
      "limit_tb": 100,
      "percentage": 23.5
    },
    "api_calls": {
      "count": 5432,
      "limit": 10000,
      "percentage": 54.32
    },
    "blobs_uploaded": {
      "count": 234,
      "size_gb": 45.2
    }
  },
  "warnings": [
    {
      "type": "approaching_limit",
      "resource": "api_calls",
      "current": 5432,
      "limit": 10000,
      "message": "API calls at 54% of daily limit"
    }
  ]
}
```

---

### POST /api/v1/heartbeat/license/renew

Renew license (PLANNED 📅).

**Request Body:**
```json
{
  "renewal_code": "RENEW-2026-XXXX-XXXX",
  "payment_reference": "PAY-2026-001",
  "renewed_by": "admin@execujet.com"
}
```

**Response (200 OK):**
```json
{
  "status": "renewed",
  "license_id": "lic_execujet_ent_001",
  "message": "License renewed successfully",
  "previous_expiry": "2027-01-01T00:00:00Z",
  "new_expiry": "2028-01-01T00:00:00Z",
  "renewed_at": "2026-02-07T10:00:00Z"
}
```

---

## Notifications APIs (notifications.db)

HeartBeat owns **notifications.db** for system-wide notification management (reconciliation alerts, system events).

### GET /api/v1/heartbeat/notifications

Get notifications (PLANNED 📅).

**Query Parameters:**
- `severity`: Optional, filter by severity ("critical", "warn", "info")
- `status`: Optional, filter by status ("unread", "read", "acknowledged")
- `category`: Optional, filter by category ("reconciliation", "health", "license", "system")
- `limit`: Optional, max results (default: 100)
- `offset`: Optional, pagination offset

**Response (200 OK):**
```json
{
  "status": "success",
  "count": 3,
  "unread_count": 2,
  "notifications": [
    {
      "notification_id": "notif_001",
      "severity": "critical",
      "category": "reconciliation",
      "title": "Orphaned blobs detected",
      "message": "Found 5 orphaned blobs in MinIO (not registered in blob_entries)",
      "details": {
        "blob_count": 5,
        "detection_time": "2026-02-07T09:00:00Z",
        "affected_paths": [
          "/files_blob/2026/02/06/orphan-1.pdf",
          "/files_blob/2026/02/06/orphan-2.pdf"
        ]
      },
      "status": "unread",
      "created_at": "2026-02-07T09:05:00Z",
      "read_at": null,
      "acknowledged_at": null,
      "acknowledged_by": null
    },
    {
      "notification_id": "notif_002",
      "severity": "warn",
      "category": "health",
      "title": "Edge service degraded",
      "message": "Edge service response time above threshold",
      "details": {
        "service": "edge",
        "avg_response_time_ms": 3500,
        "threshold_ms": 2000
      },
      "status": "read",
      "created_at": "2026-02-07T08:30:00Z",
      "read_at": "2026-02-07T08:45:00Z",
      "acknowledged_at": null,
      "acknowledged_by": null
    },
    {
      "notification_id": "notif_003",
      "severity": "info",
      "category": "system",
      "title": "Reconciliation completed",
      "message": "Hourly reconciliation completed successfully",
      "details": {
        "blobs_checked": 15234,
        "orphans_found": 0,
        "duration_ms": 1234
      },
      "status": "read",
      "created_at": "2026-02-07T10:00:00Z",
      "read_at": "2026-02-07T10:01:00Z",
      "acknowledged_at": "2026-02-07T10:02:00Z",
      "acknowledged_by": "admin@execujet.com"
    }
  ]
}
```

---

### GET /api/v1/heartbeat/notifications/{notification_id}

Get specific notification (PLANNED 📅).

**Path Parameters:**
- `notification_id`: Notification identifier

**Response (200 OK):**
```json
{
  "status": "success",
  "notification": {
    "notification_id": "notif_001",
    "severity": "critical",
    "category": "reconciliation",
    "title": "Orphaned blobs detected",
    "message": "Found 5 orphaned blobs in MinIO",
    "details": {
      "blob_count": 5,
      "affected_paths": [...]
    },
    "status": "unread",
    "created_at": "2026-02-07T09:05:00Z"
  }
}
```

---

### POST /api/v1/heartbeat/notifications/{notification_id}/read

Mark notification as read (PLANNED 📅).

**Path Parameters:**
- `notification_id`: Notification identifier

**Response (200 OK):**
```json
{
  "status": "success",
  "notification_id": "notif_001",
  "message": "Notification marked as read",
  "read_at": "2026-02-07T10:00:00Z"
}
```

---

### POST /api/v1/heartbeat/notifications/{notification_id}/acknowledge

Acknowledge notification (PLANNED 📅).

**Path Parameters:**
- `notification_id`: Notification identifier

**Request Body:**
```json
{
  "acknowledged_by": "admin@execujet.com",
  "resolution_notes": "Orphaned blobs manually registered"
}
```

**Response (200 OK):**
```json
{
  "status": "success",
  "notification_id": "notif_001",
  "message": "Notification acknowledged",
  "acknowledged_at": "2026-02-07T10:00:00Z",
  "acknowledged_by": "admin@execujet.com"
}
```

---

### POST /api/v1/heartbeat/notifications

Create notification (internal use) (PLANNED 📅).

**Request Body:**
```json
{
  "severity": "critical",
  "category": "reconciliation",
  "title": "Unexpected blob deletion",
  "message": "Blob deleted from MinIO without soft-delete record",
  "details": {
    "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
    "blob_path": "/files_blob/2026/02/07/deleted.pdf",
    "detection_time": "2026-02-07T10:00:00Z"
  },
  "created_by_service": "heartbeat-reconciliation"
}
```

**Response (201 Created):**
```json
{
  "status": "created",
  "notification_id": "notif_004",
  "message": "Notification created successfully"
}
```

---

## Audit APIs (audit.db)

HeartBeat provides audit trail for compliance (FIRS 7-year retention).

### GET /api/v1/heartbeat/audit

Get audit entries (PLANNED 📅).

**Query Parameters:**
- `entity_type`: Optional, filter by entity ("blob", "config", "license", "user")
- `action`: Optional, filter by action ("created", "updated", "deleted", "accessed")
- `actor`: Optional, filter by actor email
- `start_date`: Optional, ISO date
- `end_date`: Optional, ISO date
- `limit`: Optional, max results (default: 100)
- `offset`: Optional, pagination offset

**Response (200 OK):**
```json
{
  "status": "success",
  "count": 250,
  "audit_entries": [
    {
      "audit_id": "audit_001",
      "timestamp": "2026-02-07T10:00:00Z",
      "entity_type": "blob",
      "entity_id": "550e8400-e29b-41d4-a716-446655440000",
      "action": "created",
      "actor": "system",
      "actor_type": "service",
      "service_name": "relay-bulk",
      "details": {
        "blob_path": "/files_blob/2026/02/07/invoice.pdf",
        "file_size_bytes": 2048576,
        "source": "execujet-bulk-1"
      },
      "ip_address": "10.0.1.50",
      "user_agent": null
    },
    {
      "audit_id": "audit_002",
      "timestamp": "2026-02-07T10:05:00Z",
      "entity_type": "config",
      "entity_id": "cfg_001",
      "action": "updated",
      "actor": "admin@execujet.com",
      "actor_type": "user",
      "service_name": null,
      "details": {
        "key": "max_file_size_mb",
        "old_value": "400",
        "new_value": "500"
      },
      "ip_address": "203.45.67.89",
      "user_agent": "Mozilla/5.0..."
    }
  ]
}
```

---

### POST /api/v1/heartbeat/audit

Create audit entry (internal use) (PLANNED 📅).

**Request Body:**
```json
{
  "entity_type": "blob",
  "entity_id": "550e8400-e29b-41d4-a716-446655440000",
  "action": "deleted",
  "actor": "admin@execujet.com",
  "actor_type": "user",
  "details": {
    "reason": "user_requested",
    "blob_path": "/files_blob/2026/02/07/invoice.pdf"
  },
  "ip_address": "203.45.67.89"
}
```

**Response (201 Created):**
```json
{
  "status": "created",
  "audit_id": "audit_003",
  "message": "Audit entry created successfully"
}
```

---

## Health & Monitoring APIs

### GET /api/v1/heartbeat/health

Overall health check (Phase 2 - IMPLEMENTED ✅).

**Response (200 OK):**
```json
{
  "status": "healthy",
  "service": "heartbeat",
  "version": "1.0.0",
  "timestamp": "2026-02-07T10:00:00Z",
  "components": {
    "database": "healthy",
    "minio": "healthy",
    "scheduler": "healthy"
  },
  "metrics": {
    "blob_entries_count": 15234,
    "notifications_unread": 2,
    "reconciliation_last_run": "2026-02-07T09:00:00Z"
  }
}
```

---

### GET /api/v1/heartbeat/health/services

Get health status of all Helium services (PLANNED 📅).

**Response (200 OK):**
```json
{
  "status": "success",
  "timestamp": "2026-02-07T10:00:00Z",
  "services": {
    "relay": {
      "status": "healthy",
      "response_time_ms": 45,
      "uptime_seconds": 864000,
      "last_check": "2026-02-07T10:00:00Z"
    },
    "core": {
      "status": "healthy",
      "response_time_ms": 120,
      "uptime_seconds": 864000,
      "queue_length": 5,
      "processing_jobs": 3,
      "last_check": "2026-02-07T10:00:00Z"
    },
    "edge": {
      "status": "degraded",
      "response_time_ms": 3500,
      "uptime_seconds": 864000,
      "last_check": "2026-02-07T10:00:00Z",
      "warning": "Response time above threshold"
    },
    "float": {
      "status": "healthy",
      "active_sessions": 45,
      "last_check": "2026-02-07T10:00:00Z"
    }
  }
}
```

---

## Reconciliation APIs

### POST /api/v1/heartbeat/reconcile/trigger

Manually trigger reconciliation (PLANNED 📅).

**Request Body (Optional):**
```json
{
  "force": true,
  "triggered_by": "admin@execujet.com"
}
```

**Response (202 Accepted):**
```json
{
  "status": "triggered",
  "job_id": "reconcile_20260207_100000",
  "message": "Reconciliation job started",
  "started_at": "2026-02-07T10:00:00Z",
  "estimated_duration_seconds": 120
}
```

---

### GET /api/v1/heartbeat/reconcile/status

Get reconciliation status (PLANNED 📅).

**Query Parameters:**
- `job_id`: Optional, get specific job status

**Response (200 OK):**
```json
{
  "status": "success",
  "current_job": {
    "job_id": "reconcile_20260207_100000",
    "status": "running",
    "started_at": "2026-02-07T10:00:00Z",
    "phase": "checking_orphaned_blobs",
    "progress_percentage": 45,
    "blobs_checked": 6855,
    "total_blobs": 15234
  },
  "last_completed": {
    "job_id": "reconcile_20260207_090000",
    "status": "completed",
    "started_at": "2026-02-07T09:00:00Z",
    "completed_at": "2026-02-07T09:02:14Z",
    "duration_seconds": 134,
    "results": {
      "blobs_checked": 15234,
      "orphaned_blobs_found": 0,
      "stale_processing_found": 0,
      "unexpected_deletions": 0,
      "core_entries_cleaned": 23
    }
  }
}
```

---

### GET /api/v1/heartbeat/reconcile/history

Get reconciliation history (PLANNED 📅).

**Query Parameters:**
- `limit`: Optional, max results (default: 50)
- `offset`: Optional, pagination offset

**Response (200 OK):**
```json
{
  "status": "success",
  "count": 100,
  "reconciliations": [
    {
      "job_id": "reconcile_20260207_090000",
      "status": "completed",
      "started_at": "2026-02-07T09:00:00Z",
      "completed_at": "2026-02-07T09:02:14Z",
      "duration_seconds": 134,
      "blobs_checked": 15234,
      "issues_found": 0
    },
    {
      "job_id": "reconcile_20260207_080000",
      "status": "completed_with_warnings",
      "started_at": "2026-02-07T08:00:00Z",
      "completed_at": "2026-02-07T08:02:05Z",
      "duration_seconds": 125,
      "blobs_checked": 15229,
      "issues_found": 2,
      "warnings": [
        "2 orphaned blobs detected"
      ]
    }
  ]
}
```

---

## Parent-Client APIs

### POST /api/v1/heartbeat/client/report

Client HeartBeat reports to parent (Enterprise only) (PLANNED 📅).

**Request Body:**
```json
{
  "location_id": "execujet-location-a",
  "installation_id": "inst_execujet_loc_a_001",
  "timestamp": "2026-02-07T10:00:00Z",
  "health": {
    "heartbeat": "healthy",
    "relay": "healthy",
    "core": "healthy",
    "edge": "degraded",
    "float": "healthy"
  },
  "blob_stats": {
    "total_blobs": 15234,
    "blobs_today": 42,
    "storage_used_gb": 2340,
    "orphaned_blobs_found": 0,
    "reconciliation_duration_ms": 1234,
    "last_reconciliation": "2026-02-07T09:00:00Z"
  },
  "alerts": [
    {
      "severity": "warn",
      "category": "health",
      "message": "Edge service response time degraded"
    }
  ],
  "license": {
    "license_id": "lic_execujet_ent_001",
    "expires_at": "2027-01-01T00:00:00Z",
    "status": "active"
  }
}
```

**Response (200 OK):**
```json
{
  "status": "received",
  "message": "Report received successfully",
  "location_id": "execujet-location-a",
  "parent_instructions": {
    "reconciliation_interval_hours": 1,
    "health_check_interval_minutes": 5,
    "report_interval_minutes": 60
  }
}
```

---

### GET /api/v1/heartbeat/parent/dashboard

Get global dashboard (Parent only, Enterprise) (PLANNED 📅).

**Response (200 OK):**
```json
{
  "status": "success",
  "timestamp": "2026-02-07T10:00:00Z",
  "overview": {
    "total_locations": 12,
    "healthy_locations": 11,
    "degraded_locations": 1,
    "offline_locations": 0,
    "total_blobs": 182808,
    "total_storage_gb": 28080,
    "total_users": 234
  },
  "locations": [
    {
      "location_id": "execujet-location-a",
      "location_name": "ExecuJet HQ",
      "status": "healthy",
      "last_report": "2026-02-07T10:00:00Z",
      "blob_count": 15234,
      "alerts_count": 0
    },
    {
      "location_id": "execujet-location-b",
      "location_name": "ExecuJet Branch B",
      "status": "degraded",
      "last_report": "2026-02-07T09:58:00Z",
      "blob_count": 8923,
      "alerts_count": 1
    }
  ],
  "critical_alerts": [
    {
      "location_id": "execujet-location-b",
      "severity": "warn",
      "message": "Edge service degraded"
    }
  ]
}
```

---

## Error Responses

### Standard Error Format

All errors follow this format:

```json
{
  "status": "error",
  "error_code": "ERROR_CODE",
  "message": "Human-readable error message",
  "details": {
    "field": "specific_field",
    "provided": "invalid_value",
    "expected": "valid_format"
  },
  "timestamp": "2026-02-07T10:00:00Z",
  "request_id": "req_abc123"
}
```

### Common Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `UNAUTHORIZED` | 401 | Missing or invalid auth token |
| `FORBIDDEN` | 403 | Insufficient permissions |
| `NOT_FOUND` | 404 | Resource not found |
| `VALIDATION_ERROR` | 400 | Invalid request data |
| `DUPLICATE_KEY` | 409 | Resource already exists |
| `LICENSE_EXPIRED` | 402 | License expired |
| `RATE_LIMIT_EXCEEDED` | 429 | Rate limit exceeded |
| `INTERNAL_ERROR` | 500 | Internal server error |
| `SERVICE_UNAVAILABLE` | 503 | Service temporarily unavailable |

---

## Rate Limiting

All APIs are rate-limited based on license tier.

**Rate Limit Headers:**
```
X-RateLimit-Limit: 10000
X-RateLimit-Remaining: 9543
X-RateLimit-Reset: 1707307200
```

**Rate Limit Error (429 Too Many Requests):**
```json
{
  "status": "error",
  "error_code": "RATE_LIMIT_EXCEEDED",
  "message": "API rate limit exceeded",
  "limit": 10000,
  "window": "1 day",
  "retry_after_seconds": 3600
}
```

**Rate Limits by Tier:**

| Tier | Daily Limit | Burst Limit |
|------|-------------|-------------|
| Standard | 1,000 | 50/min |
| Pro | 5,000 | 100/min |
| Enterprise | 10,000 | 200/min |

---

## Documentation Status

| API Category | Status | Phase |
|-------------|--------|-------|
| Blob Storage | ✅ Implemented | Phase 2 |
| Configuration (config.db) | 📅 Planned | Future |
| License (license.db) | 📅 Planned | Future |
| Notifications | 📅 Planned | Phase 3 |
| Audit | 📅 Planned | Future |
| Health & Monitoring | ✅ Partial | Phase 2 |
| Reconciliation | 📅 Planned | Phase 3 |
| Parent-Client | 📅 Planned | Future |

---

---

## Script Category Access Control (Added 2026-03-25)

**CRITICAL: HeartBeat must enforce script_category-based access control.**

HeartBeat serves two categories of scripts to downstream services. Each has different access policies:

### Script Categories

| Category | Value | Allowed Callers | Hash Provided | Purpose |
|----------|-------|----------------|---------------|---------|
| **IQC** | `"IQC"` | Core + Relay | No | IRN/QR/CSID format specs (FIRS-mandated, not proprietary) |
| **Transforma** | `"TRANSFORMA"` | **Core ONLY** | Yes (SHA-256) | Tenant-specific transformation scripts (proprietary) |

### API Endpoints

#### GET /api/v1/heartbeat/scripts/{tenant_id}?category=TRANSFORMA

**Access:** Core only. All other callers receive 403 Forbidden.

**Response (200 OK):**
```json
{
  "status": "success",
  "script_category": "TRANSFORMA",
  "tenant_id": "tenant_greyhouse_001",
  "scripts": [...],
  "script_hash": "sha256:a1b2c3d4..."
}
```

**Response (403 Forbidden — non-Core caller):**
```json
{
  "status": "error",
  "error_code": "FORBIDDEN",
  "message": "TRANSFORMA scripts are restricted to Core service only"
}
```

#### GET /api/v1/heartbeat/scripts/{tenant_id}?category=IQC

**Access:** Core and Relay. No restrictions.

**Response (200 OK):**
```json
{
  "status": "success",
  "script_category": "IQC",
  "tenant_id": "tenant_greyhouse_001",
  "config": {
    "service_id": "AABE9444",
    "irn_formula_version": "1.0",
    "qr_tlv_version": "1.0",
    "csid_endpoint": "https://firs.gov.ng/api/v1/csid"
  }
}
```

No `script_hash` in IQC responses — these are not proprietary.

#### POST /api/v1/heartbeat/webhooks/config-changed

HeartBeat pushes notifications when scripts change:

**Request Body:**
```json
{
  "tenant_id": "tenant_greyhouse_001",
  "category": "IQC",
  "changed_at": "2026-03-25T14:30:00Z",
  "change_reason": "Updated CSID endpoint for FIRS v2"
}
```

**Notification routing:**
- `category: "IQC"` → Notify **Core + Relay**
- `category: "TRANSFORMA"` → Notify **Core only**

### Middleware Enforcement

HeartBeat's API middleware must:
1. Identify caller by service identity (from Bearer token claims)
2. Check `script_category` parameter
3. If `category == "TRANSFORMA"` and caller is not Core → return 403
4. Log all script access attempts (caller, category, tenant_id, timestamp)

---

**Document Version:** 1.1
**Last Updated:** 2026-03-25
**Maintained By:** HeartBeat Team
**Status:** ✅ Complete and Current
