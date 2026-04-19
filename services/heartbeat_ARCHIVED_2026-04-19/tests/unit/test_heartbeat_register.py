"""
Unit Tests for HeartBeat Blob Registration API

Tests for POST /api/v1/heartbeat/blob/register endpoint.

Coverage Target: 90%+

Test Cases:
1. Successful registration (201 Created)
2. Duplicate blob_uuid (409 Conflict)
3. Duplicate blob_path (409 Conflict)
4. Missing authorization header (401 Unauthorized)
5. Invalid authorization token (401 Unauthorized)
6. Invalid request body schema (400 Bad Request)
7. Database errors (5xx)
8. Concurrent registrations (load test)
9. Health check endpoint
10. Get blob endpoint
"""

import pytest
import sqlite3
import os
import tempfile
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

# Import the FastAPI app
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src"))

from src.main import app
from src.database import get_blob_database, BlobDatabase


# Test Fixtures

@pytest.fixture
def test_db(tmp_path):
    """Create temporary test database in a clean temp directory."""
    db_path = str(tmp_path / "blob.db")

    # Initialize database with schema
    schema_path = os.path.join(
        os.path.dirname(__file__),
        "../../databases/schema.sql"
    )

    conn = sqlite3.connect(db_path)

    with open(schema_path, 'r') as f:
        schema_sql = f.read()
        conn.executescript(schema_sql)

    # Load seed data
    seed_path = os.path.join(
        os.path.dirname(__file__),
        "../../databases/seed.sql"
    )

    if os.path.exists(seed_path):
        with open(seed_path, 'r') as f:
            seed_sql = f.read()
            conn.executescript(seed_sql)

    conn.commit()
    conn.close()

    yield db_path


@pytest.fixture
def client(test_db, monkeypatch):
    """Create test client with test database"""

    # Monkeypatch database path
    monkeypatch.setenv("HEARTBEAT_BLOB_DB_PATH", test_db)

    # Reset singleton
    from src.database.connection import reset_blob_database, get_blob_database
    reset_blob_database()
    get_blob_database(test_db)

    # Create test client
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def valid_blob_request():
    """Valid blob registration request"""
    return {
        "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
        "blob_path": "/files_blob/550e8400-e29b-41d4-a716-446655440000-invoice.pdf",
        "file_size_bytes": 2048576,
        "file_hash": "a" * 64,  # 64-char hex string
        "content_type": "application/pdf",
        "source": "execujet-bulk-1"
    }


@pytest.fixture
def auth_headers():
    """Valid authorization headers"""
    return {"Authorization": "Bearer test-token-123"}


# Test Cases

class TestBlobRegistration:
    """Test blob registration endpoint"""

    def test_successful_registration(self, client, valid_blob_request, auth_headers):
        """Test successful blob registration returns 201 Created"""

        response = client.post(
            "/api/v1/heartbeat/blob/register",
            json=valid_blob_request,
            headers=auth_headers
        )

        assert response.status_code == 201
        data = response.json()

        assert data["status"] == "created"
        assert data["blob_uuid"] == valid_blob_request["blob_uuid"]
        assert "message" in data

    def test_duplicate_blob_uuid(self, client, valid_blob_request, auth_headers):
        """Test duplicate blob_uuid returns 409 Conflict"""

        # First registration (should succeed)
        response1 = client.post(
            "/api/v1/heartbeat/blob/register",
            json=valid_blob_request,
            headers=auth_headers
        )
        assert response1.status_code == 201

        # Second registration with same blob_uuid (should fail with 409)
        response2 = client.post(
            "/api/v1/heartbeat/blob/register",
            json=valid_blob_request,
            headers=auth_headers
        )

        assert response2.status_code == 409
        data = response2.json()

        assert "duplicate" in data["detail"]["message"].lower()

    def test_duplicate_blob_path_different_uuid(self, client, valid_blob_request, auth_headers):
        """Test same blob_path with different UUID succeeds (canonical schema allows it).

        In canonical schema v1.4.0, blob_path is NOT UNIQUE — only
        file_display_id and blob_uuid have UNIQUE constraints.
        Different files can share a path (e.g. re-uploads).
        """
        # First registration
        response1 = client.post(
            "/api/v1/heartbeat/blob/register",
            json=valid_blob_request,
            headers=auth_headers
        )
        assert response1.status_code == 201

        # Second registration with different UUID but same path — should succeed
        duplicate_request = valid_blob_request.copy()
        duplicate_request["blob_uuid"] = "661f9500-f39c-52e5-b827-557766551111"

        response2 = client.post(
            "/api/v1/heartbeat/blob/register",
            json=duplicate_request,
            headers=auth_headers
        )

        assert response2.status_code == 201

    def test_missing_authorization_header(self, client, valid_blob_request):
        """Test missing authorization header returns 401 Unauthorized"""

        response = client.post(
            "/api/v1/heartbeat/blob/register",
            json=valid_blob_request
            # No headers
        )

        assert response.status_code == 401

    def test_invalid_authorization_format(self, client, valid_blob_request):
        """Test invalid authorization format returns 401"""

        response = client.post(
            "/api/v1/heartbeat/blob/register",
            json=valid_blob_request,
            headers={"Authorization": "InvalidFormat"}
        )

        assert response.status_code == 401

    def test_empty_bearer_token(self, client, valid_blob_request):
        """Test empty Bearer token returns 401"""

        response = client.post(
            "/api/v1/heartbeat/blob/register",
            json=valid_blob_request,
            headers={"Authorization": "Bearer "}
        )

        assert response.status_code == 401

    def test_invalid_blob_uuid_format(self, client, valid_blob_request, auth_headers):
        """Test invalid blob_uuid format returns 400 Bad Request"""

        invalid_request = valid_blob_request.copy()
        invalid_request["blob_uuid"] = "invalid-uuid"  # Not 36 characters

        response = client.post(
            "/api/v1/heartbeat/blob/register",
            json=invalid_request,
            headers=auth_headers
        )

        assert response.status_code == 422  # Pydantic validation error

    def test_invalid_blob_path_format(self, client, valid_blob_request, auth_headers):
        """Test invalid blob_path format returns 400"""

        invalid_request = valid_blob_request.copy()
        invalid_request["blob_path"] = "/invalid/path.pdf"  # Doesn't start with /files_blob/

        response = client.post(
            "/api/v1/heartbeat/blob/register",
            json=invalid_request,
            headers=auth_headers
        )

        assert response.status_code == 422  # Pydantic validation error

    def test_invalid_file_hash_format(self, client, valid_blob_request, auth_headers):
        """Test invalid file_hash format returns 400"""

        invalid_request = valid_blob_request.copy()
        invalid_request["file_hash"] = "invalid-hash"  # Not 64 hex characters

        response = client.post(
            "/api/v1/heartbeat/blob/register",
            json=invalid_request,
            headers=auth_headers
        )

        assert response.status_code == 422  # Pydantic validation error

    def test_negative_file_size(self, client, valid_blob_request, auth_headers):
        """Test negative file_size_bytes returns 400"""

        invalid_request = valid_blob_request.copy()
        invalid_request["file_size_bytes"] = -1

        response = client.post(
            "/api/v1/heartbeat/blob/register",
            json=invalid_request,
            headers=auth_headers
        )

        assert response.status_code == 422  # Pydantic validation error

    def test_missing_required_field(self, client, auth_headers):
        """Test missing required field returns 422"""

        incomplete_request = {
            "blob_uuid": "550e8400-e29b-41d4-a716-446655440000"
            # Missing other required fields
        }

        response = client.post(
            "/api/v1/heartbeat/blob/register",
            json=incomplete_request,
            headers=auth_headers
        )

        assert response.status_code == 422

    def test_retention_calculation(self, client, valid_blob_request, auth_headers, test_db):
        """Test 7-year retention is calculated correctly"""

        response = client.post(
            "/api/v1/heartbeat/blob/register",
            json=valid_blob_request,
            headers=auth_headers
        )

        assert response.status_code == 201

        # Query database to verify retention_until
        db = BlobDatabase(test_db)
        blob = db.get_blob(valid_blob_request["blob_uuid"])

        assert blob is not None

        # Verify retention is approximately 7 years from now
        import time
        now_unix = int(time.time())
        expected_retention_unix = now_unix + (365 * 7 * 86400)

        # Allow 1 hour tolerance (timezone differences in test env)
        diff = abs(blob["retention_until_unix"] - expected_retention_unix)
        assert diff < 3700  # Within ~1 hour

    def test_database_fields_populated(self, client, valid_blob_request, auth_headers, test_db):
        """Test all database fields are populated correctly"""

        response = client.post(
            "/api/v1/heartbeat/blob/register",
            json=valid_blob_request,
            headers=auth_headers
        )

        assert response.status_code == 201

        # Query database
        db = BlobDatabase(test_db)
        blob = db.get_blob(valid_blob_request["blob_uuid"])

        # Verify all fields
        assert blob["blob_uuid"] == valid_blob_request["blob_uuid"]
        assert blob["blob_path"] == valid_blob_request["blob_path"]
        assert blob["file_size_bytes"] == valid_blob_request["file_size_bytes"]
        assert blob["file_hash"] == valid_blob_request["file_hash"].lower()
        assert blob["content_type"] == valid_blob_request["content_type"]
        assert blob["source"] == valid_blob_request["source"]
        assert blob["status"] == "uploaded"
        # Canonical schema v1.4.0: file_display_id and batch_display_id are auto-generated
        assert blob["file_display_id"] is not None
        assert blob["batch_display_id"] is not None
        assert blob["pending_sync"] == 0  # Server-confirmed

        # Verify timestamps are set
        assert blob["uploaded_at_unix"] is not None
        assert blob["uploaded_at_iso"] is not None
        assert blob["retention_until_unix"] is not None
        assert blob["retention_until_iso"] is not None
        assert blob["created_at"] is not None
        assert blob["updated_at"] is not None


class TestGetBlobEndpoint:
    """Test GET blob endpoint"""

    def test_get_existing_blob(self, client, valid_blob_request, auth_headers):
        """Test getting existing blob returns 200"""

        # Register blob first
        client.post(
            "/api/v1/heartbeat/blob/register",
            json=valid_blob_request,
            headers=auth_headers
        )

        # Get blob
        response = client.get(
            f"/api/v1/heartbeat/blob/{valid_blob_request['blob_uuid']}",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()

        assert data["blob_uuid"] == valid_blob_request["blob_uuid"]
        assert data["blob_path"] == valid_blob_request["blob_path"]

    def test_get_nonexistent_blob(self, client, auth_headers):
        """Test getting nonexistent blob returns 404"""

        response = client.get(
            "/api/v1/heartbeat/blob/00000000-0000-0000-0000-000000000000",
            headers=auth_headers
        )

        assert response.status_code == 404

    def test_get_blob_without_auth(self, client):
        """Test getting blob without auth returns 401"""

        response = client.get(
            "/api/v1/heartbeat/blob/550e8400-e29b-41d4-a716-446655440000"
            # No headers
        )

        assert response.status_code == 401


class TestHealthCheckEndpoint:
    """Test health check endpoint"""

    def test_health_check_healthy(self, client):
        """Test root /health returns healthy status"""

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()

        assert data["status"] in ("healthy", "degraded")
        assert data["service"] == "heartbeat"
        assert data["database"] == "connected"
        assert "timestamp" in data


class TestConcurrentRegistrations:
    """Test concurrent blob registrations"""

    def test_concurrent_registrations(self, client, auth_headers):
        """Load test: 100 concurrent registrations"""

        import concurrent.futures

        def register_blob(index):
            """Register a single blob"""
            request = {
                "blob_uuid": f"550e8400-e29b-41d4-a716-44665544{index:04d}",
                "blob_path": f"/files_blob/550e8400-e29b-41d4-a716-44665544{index:04d}-file{index}.pdf",
                "file_size_bytes": 1024 * (index + 1),
                "file_hash": f"{index:064x}",
                "content_type": "application/pdf",
                "source": "execujet-bulk-1"
            }

            response = client.post(
                "/api/v1/heartbeat/blob/register",
                json=request,
                headers=auth_headers
            )

            return response.status_code

        # Register 100 blobs concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(register_blob, i) for i in range(100)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # All should return 201
        assert all(status == 201 for status in results)
        assert len(results) == 100


class TestDatabaseConnection:
    """Test database connection module"""

    def test_singleton_pattern(self, test_db):
        """Test database singleton pattern"""

        db1 = get_blob_database(test_db)
        db2 = get_blob_database()

        assert db1 is db2  # Same instance

    def test_blob_exists(self, test_db):
        """Test blob_exists method"""

        db = BlobDatabase(test_db)

        # Non-existent blob
        assert not db.blob_exists("00000000-0000-0000-0000-000000000000")

        # Insert blob
        blob_uuid = "550e8400-e29b-41d4-a716-446655440000"
        db.register_blob(
            blob_uuid=blob_uuid,
            blob_path="/files_blob/test.pdf",
            file_size_bytes=1024,
            file_hash="a" * 64,
            content_type="application/pdf",
            source="test",
            uploaded_at_unix=int(datetime.utcnow().timestamp()),
            uploaded_at_iso=datetime.utcnow().isoformat(),
            retention_until_unix=int((datetime.utcnow() + timedelta(days=365*7)).timestamp()),
            retention_until_iso=(datetime.utcnow() + timedelta(days=365*7)).isoformat()
        )

        # Should exist now
        assert db.blob_exists(blob_uuid)


class TestRootEndpoint:
    """Test root endpoint"""

    def test_root_endpoint(self, client):
        """Test root endpoint returns service info"""

        response = client.get("/")

        assert response.status_code == 200
        data = response.json()

        assert data["service"] == "heartbeat"
        assert "version" in data
        assert "endpoints" in data


# Run tests with coverage
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=heartbeat", "--cov-report=term-missing"])
