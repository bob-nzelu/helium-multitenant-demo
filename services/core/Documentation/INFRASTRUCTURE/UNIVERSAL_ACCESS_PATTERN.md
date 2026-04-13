# UNIVERSAL ACCESS PATTERN - HELIUM SERVICES

**Version:** 1.0
**Date:** 2026-02-05
**Phase:** Phase 0a (Infrastructure Architecture)
**Status:** MANDATORY - All Helium services MUST implement this pattern
**Applies To:** Core, Relay, Edge, HeartBeat, Float SDK

---

## OVERVIEW

All Helium services use a **universal access pattern** for connecting to resources (databases, queues, blob storage, APIs). This pattern provides a single abstraction that automatically detects whether a resource is local (localhost) or remote and uses the appropriate connection method.

**Key Principle**: Same code works across all deployment tiers (Test/Standard/Pro/Enterprise)

**Benefits**:
- ✅ Uniform architecture across all tiers
- ✅ No code changes when scaling from Test to Enterprise
- ✅ Clean service boundaries (API-first design)
- ✅ Easy testing (mock remote calls in unit tests)

---

## CORE CONCEPT: ResourceClient

The `ResourceClient` is a universal abstraction that connects to any resource (database, blob storage, queue, API) using auto-detection logic.

### Auto-Detection Logic

```python
class ResourceClient:
    """Universal client for any resource"""

    def __init__(self, resource_name: str, config: dict):
        self.resource_name = resource_name
        self.connection_type = self._auto_detect(config)

        if self.connection_type == "direct":
            self.client = DirectClient(config)
        elif self.connection_type == "api":
            self.client = APIClient(config)
        else:
            raise ValueError(f"Unknown connection type: {self.connection_type}")

    def _auto_detect(self, config: dict) -> str:
        """
        Auto-detect: local or remote?

        Rules:
        1. If host is localhost/127.0.0.1/::1 → direct connection
        2. If api_endpoint is provided → API connection
        3. Default → direct connection (for embedded resources)
        """
        host = config.get("host", "").lower()

        # Rule 1: Localhost always uses direct connection
        if host in ["localhost", "127.0.0.1", "::1", ""]:
            return "direct"

        # Rule 2: Explicit API endpoint
        if config.get("api_endpoint"):
            return "api"

        # Rule 3: Remote host uses API
        if host and host not in ["localhost", "127.0.0.1", "::1"]:
            return "api"

        # Default: direct
        return "direct"
```

---

## CONNECTION PATTERNS BY DEPLOYMENT TIER

### Test Tier (Single Machine)

**Configuration**:
```json
{
    "invoices_db": {
        "type": "sqlite",
        "path": "./data/test/invoices_test.db",
        "host": "localhost"
    },
    "core_api": {
        "host": "localhost",
        "port": 8080
    }
}
```

**Result**: All connections use **direct** access (file paths, localhost sockets)

---

### Standard Tier (Single Machine)

**Configuration**:
```json
{
    "invoices_db": {
        "type": "postgresql",
        "host": "localhost",
        "port": 5432,
        "database": "invoices"
    },
    "core_api": {
        "host": "localhost",
        "port": 8080
    }
}
```

**Result**: All connections use **direct** access (PostgreSQL on localhost)

---

### Pro Tier (Multiple Machines)

**Configuration**:
```json
{
    "invoices_db": {
        "type": "postgresql",
        "host": "10.174.0.10",
        "port": 5432,
        "database": "invoices"
    },
    "core_api": {
        "host": "10.174.0.10",
        "port": 8080,
        "api_endpoint": "http://10.174.0.10:8080/api/v1"
    }
}
```

**Result**:
- **Database**: Direct PostgreSQL connection (remote but native protocol)
- **Core API**: HTTP API calls (RESTful)

---

### Enterprise Tier (Multi-Location)

**Configuration**:
```json
{
    "invoices_db": {
        "type": "postgresql",
        "host": "db.core.company.com",
        "port": 5432,
        "database": "invoices",
        "api_endpoint": "https://api.core.company.com/db/invoices"
    },
    "core_api": {
        "host": "core.company.com",
        "port": 443,
        "api_endpoint": "https://api.core.company.com/api/v1"
    }
}
```

**Result**: All connections use **API** access (HTTPS with mTLS)

---

## RESOURCE TYPES & ACCESS METHODS

### 1. Database Access

**Direct Access** (Test/Standard, localhost):
```python
# PostgreSQL direct connection
client = ResourceClient("invoices_db", {
    "type": "postgresql",
    "host": "localhost",
    "port": 5432,
    "database": "invoices",
    "user": "helium_admin",
    "password": os.getenv("HELIUM_DB_PASSWORD")
})

# SQLite direct connection
client = ResourceClient("invoices_test_db", {
    "type": "sqlite",
    "path": "./data/test/invoices_test.db"
})

# Usage
async with client.transaction():
    await client.execute(
        "INSERT INTO invoices (invoice_id, total_amount) VALUES ($1, $2)",
        ("INV_001", 1000.00)
    )
```

**API Access** (Pro/Enterprise, remote):
```python
# Database API access
client = ResourceClient("invoices_db", {
    "type": "postgresql",
    "host": "db.core.company.com",
    "api_endpoint": "https://api.core.company.com/db/invoices"
})

# Usage (same API!)
async with client.transaction():
    await client.execute(
        "INSERT INTO invoices (invoice_id, total_amount) VALUES ($1, $2)",
        ("INV_001", 1000.00)
    )
```

**Note**: Application code is identical; ResourceClient handles routing automatically.

---

### 2. Blob Storage Access

**Direct Access** (Test/Standard, local filesystem):
```python
client = ResourceClient("blob_storage", {
    "type": "filesystem",
    "base_path": "./data/blobs",
    "host": "localhost"
})

# Usage
blob_uuid = await client.write(
    filename="invoice.pdf",
    content=file_bytes
)
```

**API Access** (Pro/Enterprise, MinIO):
```python
client = ResourceClient("blob_storage", {
    "type": "minio",
    "host": "minio.company.com",
    "api_endpoint": "https://api.heartbeat.company.com/blob",
    "bucket": "helium-raw-data"
})

# Usage (same API!)
blob_uuid = await client.write(
    filename="invoice.pdf",
    content=file_bytes
)
```

---

### 3. Queue Access

**Direct Access** (Test/Standard, database table):
```python
client = ResourceClient("core_queue", {
    "type": "database",
    "host": "localhost",
    "table": "core_queue"
})

# Usage
queue_id = await client.enqueue({
    "blob_uuid": "550e8400-...",
    "company_id": "execujet-ng",
    "immediate_processing": True
})
```

**API Access** (Pro/Enterprise, RabbitMQ + API):
```python
client = ResourceClient("core_queue", {
    "type": "rabbitmq",
    "host": "rabbitmq.company.com",
    "api_endpoint": "https://api.core.company.com/queue"
})

# Usage (same API!)
queue_id = await client.enqueue({
    "blob_uuid": "550e8400-...",
    "company_id": "execujet-ng",
    "immediate_processing": True
})
```

---

### 4. Service API Access

**Direct Access** (Test/Standard, localhost):
```python
client = ResourceClient("core_api", {
    "host": "localhost",
    "port": 8080,
    "base_url": "http://localhost:8080/api/v1"
})

# Usage
response = await client.post("/process", json={
    "queue_id": "queue_123",
    "immediate_processing": False
})
```

**API Access** (Pro/Enterprise, remote):
```python
client = ResourceClient("core_api", {
    "host": "core.company.com",
    "port": 443,
    "api_endpoint": "https://api.core.company.com/api/v1"
})

# Usage (same API!)
response = await client.post("/process", json={
    "queue_id": "queue_123",
    "immediate_processing": False
})
```

---

## ATOMIC TRANSACTIONS ACROSS RESOURCES

### Problem

Relay needs to atomically write:
1. Blob storage (file bytes)
2. core_queue (database table)
3. audit.db (audit log)

If any operation fails, ALL must rollback.

### Solution: Transaction Coordinator

```python
class TransactionCoordinator:
    """Coordinates transactions across multiple resources"""

    def __init__(self, *clients: ResourceClient):
        self.clients = clients

    async def __aenter__(self):
        """Begin transaction on all clients"""
        self.transactions = []
        for client in self.clients:
            tx = await client.transaction().__aenter__()
            self.transactions.append(tx)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Commit or rollback all clients"""
        if exc_type is None:
            # Commit all
            for tx in self.transactions:
                await tx.__aexit__(None, None, None)
        else:
            # Rollback all
            for tx in self.transactions:
                await tx.__aexit__(exc_type, exc_val, exc_tb)
```

### Usage Example (Relay Atomic Write)

```python
# Initialize clients
blob_client = ResourceClient("blob_storage", blob_config)
queue_client = ResourceClient("core_queue", queue_config)
audit_client = ResourceClient("audit_db", audit_config)

# Atomic transaction across all three
async with TransactionCoordinator(blob_client, queue_client, audit_client):
    # Step 1: Write blob
    blob_uuid = await blob_client.write(
        filename="invoice.pdf",
        content=file_bytes
    )

    # Step 2: Write core_queue
    queue_id = await queue_client.enqueue({
        "blob_uuid": blob_uuid,
        "company_id": "execujet-ng"
    })

    # Step 3: Write audit.db
    await audit_client.log({
        "event_type": "relay.queue_written",
        "queue_id": queue_id,
        "blob_uuid": blob_uuid
    })

    # If any step fails, ALL rollback automatically
```

---

## RETRY LOGIC & ERROR HANDLING

### Transient Errors (Retry)

```python
class ResourceClient:
    async def _execute_with_retry(self, operation, max_attempts=5):
        """Execute operation with exponential backoff"""
        attempt = 1
        backoff_seconds = 1

        while attempt <= max_attempts:
            try:
                return await operation()
            except TransientError as e:
                if attempt == max_attempts:
                    raise

                # Exponential backoff: 1s, 2s, 4s, 8s, 16s
                await asyncio.sleep(backoff_seconds)
                backoff_seconds *= 2
                attempt += 1
```

**Transient Errors**:
- Network timeouts
- Connection refused
- 503 Service Unavailable
- Database deadlock
- Temporary resource unavailable

---

### Permanent Errors (Fail Immediately)

```python
class ResourceClient:
    async def execute(self, query, params=None):
        try:
            return await self._execute_with_retry(lambda: self._do_execute(query, params))
        except PermanentError as e:
            # Log and fail immediately (no retry)
            logger.error(f"Permanent error: {e}")
            raise
```

**Permanent Errors**:
- 400 Bad Request
- 401 Unauthorized
- 404 Not Found
- SQL syntax error
- Foreign key violation

---

## CONFIGURATION SCHEMA

### Complete Configuration Example

```json
{
    "resources": {
        "invoices_db": {
            "type": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "invoices",
            "user": "helium_admin",
            "password_env": "HELIUM_DB_PASSWORD",
            "pool_size": 10,
            "timeout_seconds": 30
        },
        "invoices_test_db": {
            "type": "sqlite",
            "path": "./data/test/invoices_test.db"
        },
        "customers_db": {
            "type": "postgresql",
            "host": "localhost",
            "port": 5433,
            "database": "customers",
            "user": "helium_admin",
            "password_env": "HELIUM_DB_PASSWORD"
        },
        "blob_storage": {
            "type": "filesystem",
            "base_path": "./data/blobs",
            "host": "localhost"
        },
        "core_queue": {
            "type": "database",
            "host": "localhost",
            "database": "core",
            "table": "core_queue"
        },
        "audit_db": {
            "type": "postgresql",
            "host": "localhost",
            "port": 5435,
            "database": "audit",
            "user": "helium_admin",
            "password_env": "HELIUM_DB_PASSWORD"
        },
        "core_api": {
            "host": "localhost",
            "port": 8080,
            "base_url": "http://localhost:8080/api/v1",
            "timeout_seconds": 30
        }
    }
}
```

---

## IMPLEMENTATION REQUIREMENTS

### All Helium Services MUST:

1. **Use ResourceClient** for all resource access (databases, APIs, blob storage)
2. **Support Auto-Detect** (localhost → direct, remote → API)
3. **Implement Retry Logic** (5 attempts with exponential backoff)
4. **Use Transaction Coordinator** for atomic multi-resource operations
5. **Log All Operations** (INFO level for success, ERROR for failures)
6. **Handle Graceful Degradation** (continue with warnings if non-critical resource unavailable)

---

## SERVICE-SPECIFIC IMPLEMENTATIONS

### Relay Service

**Resources Used**:
- blob_storage (write raw files)
- core_queue (enqueue work for Core)
- audit_db (log ingestion events)
- daily_usage_db (check upload limits)

**Atomic Transaction**:
```python
async with TransactionCoordinator(blob_client, queue_client, audit_client):
    blob_uuid = await blob_client.write(filename, content)
    queue_id = await queue_client.enqueue({...})
    await audit_client.log({...})
```

---

### Core Service

**Resources Used**:
- invoices_db (read/write invoices)
- customers_db (read/write customers)
- inventory_db (read/write products)
- notifications_db (create notifications)
- core_queue (read work items)
- blob_storage (read raw files, write processed data)
- edge_queue (enqueue work for Edge)
- audit_db (log processing events)

**Example**:
```python
# Read from core_queue
queue_entry = await queue_client.dequeue()

# Fetch blob
file_bytes = await blob_client.read(queue_entry["blob_uuid"])

# Process invoice
invoice_data = extract_invoice(file_bytes)

# Atomic write: invoices.db + edge_queue + audit.db
async with TransactionCoordinator(invoices_client, edge_queue_client, audit_client):
    invoice_id = await invoices_client.create_invoice(invoice_data)
    await edge_queue_client.enqueue({"invoice_id": invoice_id})
    await audit_client.log({"event_type": "core.processing_completed"})
```

---

### Edge Service

**Resources Used**:
- invoices_db (read invoices, update FIRS status)
- audit_db (log FIRS submissions)
- firs_api (external API)

**Example**:
```python
# Read invoice
invoice = await invoices_client.get_invoice(invoice_id)

# Submit to FIRS
firs_response = await firs_api_client.submit(invoice)

# Update invoice status
async with TransactionCoordinator(invoices_client, audit_client):
    await invoices_client.update_invoice(invoice_id, {
        "firs_status": "SUBMITTED",
        "firs_confirmation": firs_response["confirmation_number"]
    })
    await audit_client.log({"event_type": "edge.firs_submission_success"})
```

---

### HeartBeat Service

**Resources Used**:
- audit_db (query for reconciliation)
- blob_storage (reconcile blobs)
- all_services (health checks)

**Example**:
```python
# Query audit.db for stuck processing
stuck_entries = await audit_client.query("""
    SELECT queue_id, blob_uuid, created_at
    FROM audit_log
    WHERE event_type = 'core.processing_started'
    AND created_at < NOW() - INTERVAL '1 hour'
    AND queue_id NOT IN (
        SELECT queue_id FROM audit_log
        WHERE event_type IN ('core.processing_completed', 'core.processing_failed')
    )
""")

# Alert on stuck entries
for entry in stuck_entries:
    await notifications_client.create_notification({
        "type": "WARNING",
        "message": f"Stuck processing: {entry['queue_id']}"
    })
```

---

### Float SDK

**Resources Used**:
- sync.db (local SQLite cache)
- core_api (WebSocket sync, REST API calls)
- relay_api (bulk upload)

**Example**:
```python
# Connect to Core WebSocket (auto-detect localhost vs remote)
core_client = ResourceClient("core_api", core_config)

# Subscribe to events
await core_client.websocket_subscribe("invoice.created", on_invoice_created)

# Sync sync.db with Core
invoices = await core_client.get("/invoices?limit=1000")
await sync_db_client.bulk_upsert("invoices", invoices)
```

---

## TESTING STRATEGY

### Unit Tests: Mock ResourceClient

```python
# Mock ResourceClient for testing
class MockResourceClient(ResourceClient):
    def __init__(self, resource_name: str, config: dict):
        self.resource_name = resource_name
        self.config = config
        self.operations = []  # Track all operations

    async def execute(self, query, params=None):
        self.operations.append(("execute", query, params))
        return {"status": "success"}

# Test
async def test_invoice_creation():
    mock_client = MockResourceClient("invoices_db", {})

    await create_invoice(mock_client, {"invoice_id": "INV_001"})

    assert len(mock_client.operations) == 1
    assert mock_client.operations[0][1].startswith("INSERT INTO invoices")
```

---

### Integration Tests: Real ResourceClient

```python
# Test with real databases (Test tier, localhost)
async def test_invoice_creation_integration():
    client = ResourceClient("invoices_test_db", {
        "type": "sqlite",
        "path": "./data/test/invoices_test.db"
    })

    invoice_id = await create_invoice(client, {"invoice_id": "INV_001"})

    invoice = await client.fetch_one(
        "SELECT * FROM invoices WHERE invoice_id = $1",
        ("INV_001",)
    )

    assert invoice["invoice_id"] == "INV_001"
```

---

## SECURITY CONSIDERATIONS

### 1. Credentials Management

**DO NOT**:
- ❌ Hardcode passwords in configuration files
- ❌ Commit credentials to Git
- ❌ Log passwords or API keys

**DO**:
- ✅ Use environment variables for sensitive values
- ✅ Encrypt credentials in config.db (HeartBeat-managed)
- ✅ Use key vault for Pro/Enterprise (Azure Key Vault, HashiCorp Vault)

**Example**:
```json
{
    "invoices_db": {
        "type": "postgresql",
        "host": "localhost",
        "user": "helium_admin",
        "password_env": "HELIUM_DB_PASSWORD"  // Read from environment
    }
}
```

---

### 2. Network Security

**Test/Standard** (Same Machine):
- No TLS required (localhost communication)
- File permissions protect SQLite databases

**Pro** (Multiple Machines):
- TLS for all HTTP API calls (HTTPS)
- PostgreSQL with SSL connections

**Enterprise** (Multi-Location):
- mTLS (mutual TLS) for service-to-service communication
- Client certificates for authentication
- Network segmentation (services in VPC)

---

### 3. SQL Injection Prevention

**ALWAYS use parameterized queries:**

```python
# ✅ GOOD: Parameterized query
await client.execute(
    "SELECT * FROM invoices WHERE invoice_id = $1",
    (invoice_id,)
)

# ❌ BAD: String interpolation (SQL injection risk!)
await client.execute(
    f"SELECT * FROM invoices WHERE invoice_id = '{invoice_id}'"
)
```

---

## PERFORMANCE OPTIMIZATION

### 1. Connection Pooling

```python
# PostgreSQL connection pool
client = ResourceClient("invoices_db", {
    "type": "postgresql",
    "host": "localhost",
    "pool_size": 10,  # 10 connections in pool
    "pool_timeout": 30  # Wait 30s for available connection
})
```

---

### 2. Prepared Statements

```python
# Cache prepared statements for repeated queries
client = ResourceClient("invoices_db", config)

# First execution: prepares statement
await client.execute("SELECT * FROM invoices WHERE invoice_id = $1", ("INV_001",))

# Second execution: reuses prepared statement (faster)
await client.execute("SELECT * FROM invoices WHERE invoice_id = $1", ("INV_002",))
```

---

### 3. Batch Operations

```python
# Batch insert (1 transaction, not N transactions)
async with client.transaction():
    for invoice in invoices:
        await client.execute(
            "INSERT INTO invoices (invoice_id, total_amount) VALUES ($1, $2)",
            (invoice["invoice_id"], invoice["total_amount"])
        )
```

---

## SUMMARY

### Key Takeaways

1. **ResourceClient** is the universal abstraction for all resource access
2. **Auto-detection** enables same code across all deployment tiers
3. **TransactionCoordinator** provides atomic operations across multiple resources
4. **Retry logic** handles transient errors automatically
5. **All services** must implement this pattern (mandatory)

### Reference This Document

All Helium service architecture documents (Core, Relay, Edge, HeartBeat) must:
- Reference UNIVERSAL_ACCESS_PATTERN.md
- Implement ResourceClient for all resource access
- Document service-specific usage patterns

---

**End of Document**
