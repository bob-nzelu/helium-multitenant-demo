# End-to-End Test Architecture Notes

**Version:** 1.0
**Date:** 2026-03-04
**Status:** Design (not yet implemented -- depends on Relay being built)

---

## 1. Vision

A single pytest session spins up HeartBeat + Relay + (mock) Core as real HTTP services, then runs cross-service auth flow tests. Tests verify that:

- Float SDK can log in via HeartBeat and get a valid JWT
- Relay correctly introspects JWTs via HeartBeat before processing
- Step-up auth flows work end-to-end
- SSE events propagate from HeartBeat to connected clients
- Session revocation cascades correctly across services

---

## 2. Service Topology

```
┌─────────────┐   HMAC + JWT    ┌─────────────┐  JWT introspect  ┌─────────────┐
│  Test Client │ ─────────────→ │    Relay     │ ────────────────→│  HeartBeat   │
│  (httpx)     │                │  :8000       │                  │  :9000       │
└─────────────┘                └──────┬──────┘                  └──────┬──────┘
                                      │                                │
                                      │  Forward to Core               │ PostgreSQL
                                      ↓                                │ (heartbeat DB)
                                ┌─────────────┐                       │
                                │  Mock Core   │                       │
                                │  :8001       │                       │
                                └─────────────┘
```

- **HeartBeat :9000** -- Real service, real PostgreSQL, handles auth + blobs
- **Relay :8000** -- Real service, HMAC auth + JWT introspect via HeartBeat
- **Mock Core :8001** -- Minimal FastAPI stub, returns mock invoice responses
- **PostgreSQL :5432** -- Real database (same as unit/integration tests)

---

## 3. Process Management Strategy

### 3.1 Option A: subprocess.Popen (Recommended for Phase 1)

Session-scoped fixtures start each service as a subprocess:

```python
@pytest.fixture(scope="session")
def heartbeat_service():
    """Start HeartBeat as a real HTTP service."""
    env = {
        **os.environ,
        "HEARTBEAT_PG_PASSWORD": "Technology100",
        "HEARTBEAT_PG_HOST": "localhost",
        "HEARTBEAT_PG_PORT": "5432",
        "HEARTBEAT_PG_DATABASE": "heartbeat",
        "HEARTBEAT_MODE": "primary",
        "HEARTBEAT_PORT": "9000",
    }

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.main:app",
         "--host", "127.0.0.1", "--port", "9000"],
        cwd=HEARTBEAT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    _wait_for_health("http://127.0.0.1:9000/health", timeout=30)

    yield {"proc": proc, "url": "http://127.0.0.1:9000"}

    proc.terminate()
    proc.wait(timeout=10)
```

Health-check polling:

```python
def _wait_for_health(url: str, timeout: int = 30):
    """Poll health endpoint until service is ready."""
    import httpx, time
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=2)
            if r.status_code == 200:
                return
        except httpx.ConnectError:
            pass
        time.sleep(0.5)
    raise TimeoutError(f"Service at {url} did not start within {timeout}s")
```

### 3.2 Option B: Docker Compose (Recommended for CI)

```yaml
# docker-compose.e2e.yml
version: '3.8'
services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_PASSWORD: Technology100
      POSTGRES_DB: heartbeat
    ports: ["5432:5432"]

  heartbeat:
    build: ./HeartBeat
    depends_on: [postgres]
    environment:
      HEARTBEAT_PG_PASSWORD: Technology100
      HEARTBEAT_PG_HOST: postgres
    ports: ["9000:9000"]

  relay:
    build: ./Relay
    depends_on: [heartbeat]
    environment:
      RELAY_HEARTBEAT_API_KEY: rl_e2e_key
      RELAY_HEARTBEAT_API_SECRET: e2e_secret
      RELAY_HEARTBEAT_URL: http://heartbeat:9000
    ports: ["8000:8000"]
```

### 3.3 Option C: TestClient In-Process (NOT Recommended for E2E)

FastAPI `TestClient` runs ASGI in-memory (no real HTTP). Cannot test inter-service HTTP calls. Only suitable for single-service integration tests (which we already have in `tests/integration/`).

---

## 4. Database Isolation Strategy

### 4.1 Transaction Rollback per Test (for Process-Shared DB)

For tests that share a PostgreSQL instance:

```python
@pytest.fixture(autouse=True)
def _db_transaction(pg_pool):
    """Wrap each test in a transaction, rollback after."""
    with pg_pool.get_connection() as conn:
        conn.autocommit = False
        yield conn
        conn.rollback()
```

**Challenge:** E2E services run in separate processes with their own connections, so they can't share a test transaction.

### 4.2 Test Data Seeding + Cleanup (Recommended)

Create test-specific users/data before each test, delete after:

```python
@pytest.fixture
def e2e_test_user(heartbeat_url):
    """Create a test user via HeartBeat admin API, delete after."""
    # Create user
    user_id = f"e2e-{uuid.uuid4().hex[:8]}"
    # ... seed via SQL or admin endpoint ...
    yield user_info
    # Cleanup via SQL
```

This is the pattern already used in `tests/unit/test_pg_auth.py` and `tests/integration/conftest.py`.

### 4.3 Schema-per-Test-Run (Alternative)

Each test run creates a unique PostgreSQL schema:

```sql
CREATE SCHEMA auth_test_abc123;
SET search_path TO auth_test_abc123, public;
```

**Challenge:** HeartBeat code hardcodes `auth.` prefix. Would need `search_path` configuration at connection level.

---

## 5. SSE Testing Patterns

### 5.1 Using aiohttp SSE Client

```python
async def test_sse_cipher_refresh(heartbeat_url, test_user_token):
    """Verify cipher_refresh event arrives within 9-minute window."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{heartbeat_url}/api/sse/stream",
            headers={"Authorization": f"Bearer {test_user_token}"},
        ) as resp:
            assert resp.status == 200

            async for line in resp.content:
                decoded = line.decode("utf-8").strip()
                if "auth.cipher_refresh" in decoded:
                    # Parse the data line that follows
                    # Verify cipher_text matches expected derivation
                    break
```

### 5.2 Testing Permission-Filtered Events

```python
async def test_admin_event_not_delivered_to_operator():
    """Subscribe two clients, verify role filtering."""
    # 1. Login as admin -> get admin_token
    # 2. Login as operator -> get operator_token
    # 3. Connect both to SSE
    # 4. Publish admin-only event
    # 5. Verify admin receives, operator does not
```

### 5.3 Testing Session Revocation via SSE

```python
async def test_session_revocation_sse():
    """Admin revokes session, user receives session.revoked event."""
    # 1. Login user A, connect to SSE
    # 2. Admin revokes user A's session
    # 3. Verify user A receives session.revoked event
    # 4. Verify user A's token no longer introspects as active
```

---

## 6. Multi-Service Auth Flow Tests

### 6.1 Relay Introspects via HeartBeat

```python
async def test_relay_introspects_via_heartbeat(
    heartbeat_url, relay_url, test_user
):
    """Full flow: login -> send to Relay -> Relay introspects -> success."""
    # 1. Login via HeartBeat
    login_resp = await httpx.post(
        f"{heartbeat_url}/api/auth/login",
        json={"email": test_user["email"], "password": test_user["password"]},
    )
    token = login_resp.json()["access_token"]

    # 2. Send file to Relay with JWT
    relay_resp = await httpx.post(
        f"{relay_url}/api/ingest",
        headers={
            "Authorization": f"Bearer {token}",
            "X-API-Key": RELAY_API_KEY,
            "X-Timestamp": str(int(time.time())),
            "X-Signature": compute_hmac(...),
        },
        files={"file": ("test.pdf", b"content", "application/pdf")},
    )

    # 3. Relay should have introspected JWT and processed successfully
    assert relay_resp.status_code == 200
```

### 6.2 Relay Rejects Expired Token

```python
async def test_relay_rejects_expired_token():
    """Relay returns 401 when JWT is expired."""
    # Use a pre-expired test token (or mock time)
    # Send to Relay -> expect 401
```

### 6.3 Relay Handles Step-Up Required

```python
async def test_relay_stepup_required():
    """Relay returns 403 when step-up freshness not met."""
    # 1. Login, wait for last_auth_at to become stale
    # 2. Send request requiring step-up (invoice.finalize)
    # 3. Relay introspects with required_within_seconds=300
    # 4. HeartBeat returns step_up_satisfied=false
    # 5. Relay returns 403 with STEP_UP_REQUIRED
```

---

## 7. Test Data Seeding

### 7.1 E2E Seed SQL

Based on existing `databases/schemas/auth/002_auth_seed.sql`:

```sql
-- File: databases/schemas/auth/003_e2e_test_seed.sql

-- E2E test tenant
INSERT INTO auth.tenants (tenant_id, name, max_concurrent_sessions)
VALUES ('e2e-tenant', 'E2E Test Tenant', 3)
ON CONFLICT DO NOTHING;

-- E2E test users (one per role)
INSERT INTO auth.users (user_id, email, password_hash, display_name, role_id, tenant_id, is_first_run)
VALUES
    ('e2e-owner', 'owner@e2e.test', '$2b$04$...', 'E2E Owner', 'owner', 'e2e-tenant', false),
    ('e2e-admin', 'admin@e2e.test', '$2b$04$...', 'E2E Admin', 'admin', 'e2e-tenant', false),
    ('e2e-operator', 'operator@e2e.test', '$2b$04$...', 'E2E Operator', 'operator', 'e2e-tenant', false),
    ('e2e-support', 'support@e2e.test', '$2b$04$...', 'E2E Support', 'support', 'e2e-tenant', false)
ON CONFLICT DO NOTHING;

-- Relay service credential (registered in registry.db)
-- api_key: rl_e2e_key, api_secret_hash: bcrypt hash of e2e_secret
```

---

## 8. Test Pyramid

```
                    ┌──────────────┐
                    │   E2E Tests   │  Multi-service, real HTTP
                    │  (future)     │  HeartBeat + Relay + Core
                    ├──────────────┤
                    │ Integration   │  Real PG, single service
                    │  Tests        │  test_auth_flow.py (35+ tests)
                    ├──────────────┤
                    │  Unit Tests   │  Real PG, isolated per-test
                    │               │  test_pg_auth.py (40 tests)
                    │               │  + existing 434 tests (SQLite)
                    └──────────────┘
```

- **Unit tests** (fast, no network): test_pg_auth.py, test_sse_events (in-memory bus)
- **Integration tests** (real PG, single service): test_auth_flow.py (full auth handler flows)
- **E2E tests** (multi-service, real HTTP): Relay <-> HeartBeat auth flow (future)

---

## 9. CI/CD Integration

### 9.1 GitHub Actions

```yaml
services:
  postgres:
    image: postgres:15
    env:
      POSTGRES_PASSWORD: Technology100
      POSTGRES_DB: heartbeat
    ports: ["5432:5432"]
    options: >-
      --health-cmd pg_isready
      --health-interval 10s
      --health-timeout 5s
      --health-retries 5

steps:
  - name: Run auth schema migration
    run: psql -h localhost -U postgres -d heartbeat -f databases/schemas/auth/001_auth_schema.sql
    env:
      PGPASSWORD: Technology100

  - name: Run auth seed data
    run: psql -h localhost -U postgres -d heartbeat -f databases/schemas/auth/002_auth_seed.sql
    env:
      PGPASSWORD: Technology100

  - name: Run tests
    run: pytest tests/ -v --tb=short
    env:
      HEARTBEAT_PG_PASSWORD: Technology100
```

### 9.2 pytest Markers

```python
# In pytest.ini:
markers =
    e2e: marks tests as end-to-end (requires all services running)

# In tests:
@pytest.mark.e2e
async def test_relay_introspects():
    ...
```

Skip E2E tests when services aren't available:

```python
@pytest.fixture(scope="session")
def _check_services():
    """Skip E2E tests if required services aren't running."""
    try:
        httpx.get("http://127.0.0.1:9000/health", timeout=2)
    except httpx.ConnectError:
        pytest.skip("HeartBeat not running, skipping E2E tests")
```
