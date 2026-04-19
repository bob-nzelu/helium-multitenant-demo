# HeartBeat Implementation Roadmap

**Version:** 1.0
**Date:** 2026-02-01
**Status:** ✅ Aligned - Ready for Phased Implementation

---

## Implementation Strategy

We will implement HeartBeat in **two phases**:

1. **Option A (Now):** Simple centralized architecture
2. **Option B (Future):** Distributed architecture with API routing

---

## Option A: Simple Centralized (IMPLEMENT NOW)

### Architecture

```
TEST/STANDARD or SIMPLE PRO/ENTERPRISE
┌────────────────────────────────────┐
│  HQ Server (One Installation)      │
│                                    │
│  Relay, Core, Edge                 │
│  MinIO (local or networked)        │
│  blob.db (local or networked)      │
│                                    │
│  ┌──────────────────────────────┐  │
│  │ HeartBeat PARENT             │  │
│  │                              │  │
│  │ • Auto keep-alive (all svcs) │  │
│  │ • Direct access to blob.db   │  │
│  │ • Direct access to MinIO     │  │
│  │ • Deduplication logic        │  │
│  │ • External APIs              │  │
│  │ • Reconciliation             │  │
│  └──────────────────────────────┘  │
└────────────────────────────────────┘
```

### Key Characteristics:

✅ **One installation** (all services on one server or networked)
✅ **HeartBeat in PARENT mode only**
✅ **Direct database access** (SQLite local or PostgreSQL networked)
✅ **Direct MinIO access** (local or networked via MinIO client)
✅ **No client HeartBeats** (not needed for single installation)
✅ **Simple deployment** (Docker Compose or single server)

### What HeartBeat Does:

```python
# Direct access (no routing)

@app.post("/api/v1/heartbeat/blob/register")
async def register_blob(request):
    # Direct write to blob.db
    db = get_blob_database()  # Local SQLite or networked PostgreSQL
    db.register_blob(...)
    return {"status": "created"}

@app.get("/api/v1/heartbeat/reconcile")
async def reconcile():
    # Direct MinIO access
    minio = get_minio_client()  # Local or networked MinIO
    blobs = minio.list_objects("helium-invoices")

    # Direct blob.db access
    db_entries = db.list_all_blobs()

    # Compare and reconcile
    orphans = find_orphans(blobs, db_entries)
    ...
```

### Configuration:

```yaml
heartbeat:
  mode: "parent"  # No clients
  port: 9000

  # Direct access to storage
  storage:
    # Option 1: Local SQLite
    blob_db:
      type: "sqlite"
      path: "./databases/blob.db"

    # Option 2: Networked PostgreSQL
    blob_db:
      type: "postgresql"
      host: "db.execujet.com"
      port: 5432
      database: "helium_blob"
      user: "${DB_USER}"
      password: "${DB_PASSWORD}"

    # Option 3: Local MinIO
    minio:
      endpoint: "localhost:9001"
      access_key: "${MINIO_ACCESS_KEY}"
      secret_key: "${MINIO_SECRET_KEY}"

    # Option 4: Networked MinIO
    minio:
      endpoint: "minio.execujet.com:9001"
      access_key: "${MINIO_ACCESS_KEY}"
      secret_key: "${MINIO_SECRET_KEY}"

  # Auto keep-alive for local services
  services:
    relay:
      url: "http://localhost:8082/health"
    core:
      url: "http://localhost:8080/api/v1/health"
    edge:
      url: "http://localhost:8083/health"
    minio:
      url: "http://localhost:9001/minio/health/live"
```

### Deployment:

**Docker Compose (Test/Standard):**
```yaml
version: '3.8'
services:
  heartbeat:
    image: helium-heartbeat:1.0
    environment:
      HEARTBEAT_MODE: parent
      BLOB_DB_TYPE: sqlite
      MINIO_ENDPOINT: minio:9000
    volumes:
      - ./data/blob.db:/app/databases/blob.db
    ports:
      - "9000:9000"
    depends_on:
      - minio

  minio:
    image: minio/minio:latest
    command: server /data
    ports:
      - "9001:9000"
    volumes:
      - ./data/minio:/data
```

### Scope:

**What we implement:**
- ✅ HeartBeat Phase 2 (Blob Registration) - DONE
- ✅ HeartBeat Reconciliation (hourly MinIO sync) - NEXT
- ✅ Auto keep-alive (monitor and restart local services)
- ✅ External APIs (for SIEM, audits)
- ✅ Direct blob.db access
- ✅ Direct MinIO access

**What we DON'T implement:**
- ❌ Client HeartBeat mode
- ❌ Parent-client communication
- ❌ API routing to other HeartBeats
- ❌ Service discovery
- ❌ Distributed deduplication

---

## Option B: Distributed Architecture (FUTURE)

### Architecture

```
COMPLEX ENTERPRISE (Multiple Installations)
┌──────────────────────────────────────────────────────────┐
│  EXECUJET COMPANY                                        │
│                                                          │
│  ┌────────────┐  ┌────────────┐  ┌─────────┐  ┌──────┐ │
│  │ HQ Server  │  │MinIO Server│  │DB Server│  │Branch│ │
│  │ Install#1  │  │ Install#2  │  │Install#3│  │ #4   │ │
│  │            │  │            │  │         │  │      │ │
│  │ Relay,Core │  │ MinIO ONLY │  │ blob.db │  │Relay │ │
│  │ Edge       │  │            │  │ ONLY    │  │Core  │ │
│  │            │  │            │  │         │  │      │ │
│  │ ┌────────┐ │  │ ┌────────┐ │  │┌───────┐│  │┌────┐││
│  │ │HB      │ │  │ │HB      │ │  ││HB     ││  ││HB  │││
│  │ │PARENT  │ │  │ │CLIENT  │ │  ││CLIENT ││  ││CLI │││
│  │ │        │ │  │ │        │ │  ││       ││  ││ENT │││
│  │ │•Auto   │ │  │ │•Auto   │ │  ││•Auto  ││  ││•Auto││
│  │ │ keep   │ │  │ │ keep   │ │  ││ keep  ││  ││ keep││
│  │ │ alive  │ │  │ │ MinIO  │ │  ││ blob  ││  ││ svcs││
│  │ │ local  │ │  │ │ alive  │ │  ││ .db   ││  ││alive││
│  │ │        │ │  │ │        │ │  ││ alive ││  ││     ││
│  │ │•Coord. │ │  │ │•Expose │ │  ││•Expose││  ││     ││
│  │ │•Route  │◄├──┼─┤ MinIO  │ │  ││ DB    ││  ││     ││
│  │ │ to API │ │  │ │ APIs   │ │  ││ APIs  ││  ││     ││
│  │ │•Dedup  │ │  │ │        │ │  ││       ││  ││     ││
│  │ └────────┘ │  │ └────────┘ │  │└───────┘│  │└────┘││
│  └────────────┘  └────────────┘  └─────────┘  └──────┘ │
│       │               │               │            │     │
│       └───────────────┴───────────────┴────────────┘     │
│              Parent coordinates via APIs                 │
└──────────────────────────────────────────────────────────┘
```

### Key Characteristics:

✅ **Multiple installations** (4 separate servers in this example)
✅ **Each has HeartBeat** (auto keep-alive for local services)
✅ **Each exposes APIs** for MinIO or blob.db in that installation
✅ **Parent discovers** which HeartBeat has blob.db, which has MinIO
✅ **Parent routes** blob registration to DB server HeartBeat
✅ **Parent coordinates** reconciliation across installations

### What Changes from Option A:

**Parent HeartBeat:**
```python
# Option B: Route to other HeartBeats

@app.post("/api/v1/heartbeat/blob/register")
async def register_blob(request):
    # Discover which HeartBeat has blob.db
    db_server_url = await discover_service("blob_db")

    # Route to that HeartBeat's API
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{db_server_url}/api/v1/blob_db/register",
            json=request.dict(),
            headers={"Authorization": f"Bearer {INTER_HEARTBEAT_TOKEN}"}
        )

    return response.json()

@app.get("/api/v1/heartbeat/reconcile")
async def reconcile():
    # Discover MinIO server
    minio_server_url = await discover_service("minio")
    blobs = await fetch_from_heartbeat(f"{minio_server_url}/api/v1/minio/list_all")

    # Discover DB server
    db_server_url = await discover_service("blob_db")
    db_entries = await fetch_from_heartbeat(f"{db_server_url}/api/v1/blob_db/list_all")

    # Compare and reconcile
    orphans = find_orphans(blobs, db_entries)
    ...
```

**DB Server HeartBeat (Client):**
```python
# Exposes blob.db APIs

@app.post("/api/v1/blob_db/register")
async def register_blob_local(request):
    # This HeartBeat owns blob.db
    db = get_blob_database()  # Local SQLite or PostgreSQL
    db.register_blob(...)
    return {"status": "created"}

@app.get("/api/v1/blob_db/list_all")
async def list_all_blobs():
    db = get_blob_database()
    return db.list_all()
```

**MinIO Server HeartBeat (Client):**
```python
# Exposes MinIO APIs

@app.get("/api/v1/minio/list_all")
async def list_all_minio_blobs():
    minio = get_minio_client()  # Local MinIO
    objects = minio.list_objects("helium-invoices")
    return [obj.object_name for obj in objects]

@app.delete("/api/v1/minio/delete/{blob_uuid}")
async def delete_blob(blob_uuid):
    minio = get_minio_client()
    minio.remove_object("helium-invoices", blob_path)
    return {"status": "deleted"}
```

### Service Discovery:

```python
# Parent discovers services via registration or config

# Option 1: Auto-registration (clients announce themselves)
@app.post("/api/v1/client/register")
async def register_client(client_info):
    """
    Clients register their capabilities:
    - "I have blob.db at http://db-server:9000"
    - "I have MinIO at http://minio-server:9000"
    """
    service_registry[client_info.service_type] = client_info.url

# Option 2: Static configuration
config = {
    "blob_db_server": "http://db-server.execujet.com:9000",
    "minio_server": "http://minio-server.execujet.com:9000"
}
```

### When to Use Option B:

**Use cases:**
- Very large enterprise with dedicated infrastructure servers
- MinIO on separate storage cluster
- PostgreSQL on dedicated database cluster
- Geographic distribution (different data centers)
- High availability (MinIO cluster, DB cluster)

**Complexity trade-off:**
- ✅ More flexible deployment
- ✅ Dedicated hardware for storage/DB
- ❌ More network calls (parent → client APIs)
- ❌ More complex service discovery
- ❌ Inter-HeartBeat authentication needed

---

## Implementation Timeline

### Phase 1: Option A (NOW - 2 weeks)
- ✅ HeartBeat Phase 2 (Blob Registration) - DONE
- 🔄 HeartBeat Reconciliation - NEXT
- ⏭️ Auto keep-alive
- ⏭️ External APIs (basic)

### Phase 2: Client Mode Foundation (FUTURE - 2 weeks)
- Add "client" mode to HeartBeat
- Client→Parent health reporting
- Service discovery mechanism
- Client registration API

### Phase 3: Option B (FUTURE - 3 weeks)
- Parent routes blob registration to DB server HeartBeat
- DB server HeartBeat exposes blob.db APIs
- MinIO server HeartBeat exposes MinIO APIs
- Distributed reconciliation
- Inter-HeartBeat authentication

---

## Current Focus: Option A

**What we're implementing NOW:**

1. **HeartBeat Reconciliation** (hourly MinIO sync)
   - Parent directly accesses blob.db
   - Parent directly accesses MinIO
   - No routing to other HeartBeats
   - Simple, works for 90% of deployments

2. **Auto Keep-Alive**
   - Monitor local Helium services
   - Restart if down
   - Auto-start with OS

3. **External APIs** (basic)
   - Audit log queries
   - Blob statistics
   - For SIEM integration

**What we're NOT implementing yet:**
- ❌ Client HeartBeat mode
- ❌ Service discovery
- ❌ API routing
- ❌ Distributed deduplication

---

## Summary

| Feature | Option A (Now) | Option B (Future) |
|---------|----------------|-------------------|
| **Deployment** | Single installation | Multiple installations |
| **HeartBeat instances** | 1 (parent only) | Multiple (1 parent + N clients) |
| **blob.db access** | Direct | Via DB server HeartBeat API |
| **MinIO access** | Direct | Via MinIO server HeartBeat API |
| **Service discovery** | Not needed | Required |
| **Complexity** | Low | High |
| **Flexibility** | Medium | Very high |
| **Use cases** | 90% of deployments | Large enterprises |

---

**Status:** ✅ Option A is the current implementation path
**Next Step:** Implement HeartBeat Reconciliation (new chat as you mentioned)

---

**Document Version:** 1.0
**Last Updated:** 2026-02-01
