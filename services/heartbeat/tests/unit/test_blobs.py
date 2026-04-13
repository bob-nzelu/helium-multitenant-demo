"""
Tests for Blob Write and Register API (/api/blobs/*)

Tests POST /api/blobs/write and POST /api/blobs/register endpoints.
"""

import hashlib
import io
import pytest


class TestBlobWrite:
    """Tests for POST /api/blobs/write."""

    def test_write_blob_success(self, client, mock_storage):
        """Write blob returns metadata with hash and path."""
        file_content = b"test file content"
        response = client.post(
            "/api/blobs/write",
            data={
                "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
                "filename": "invoice.pdf",
            },
            files={"file": ("invoice.pdf", io.BytesIO(file_content), "application/pdf")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["blob_uuid"] == "550e8400-e29b-41d4-a716-446655440000"
        assert data["blob_path"] == "/files_blob/550e8400-e29b-41d4-a716-446655440000-invoice.pdf"
        assert data["file_size_bytes"] == len(file_content)
        assert data["file_hash"] == hashlib.sha256(file_content).hexdigest()
        assert data["status"] == "uploaded"

    def test_write_blob_stores_in_storage(self, client, mock_storage):
        """Write blob actually stores data in filesystem storage."""
        file_content = b"stored content"
        client.post(
            "/api/blobs/write",
            data={
                "blob_uuid": "11111111-1111-1111-1111-111111111111",
                "filename": "test.pdf",
            },
            files={"file": ("test.pdf", io.BytesIO(file_content), "application/pdf")},
        )
        assert "files_blob/11111111-1111-1111-1111-111111111111-test.pdf" in mock_storage._storage

    def test_write_blob_hash_matches(self, client, mock_storage):
        """File hash in response matches actual SHA256 of content."""
        file_content = b"hash verification content"
        expected_hash = hashlib.sha256(file_content).hexdigest()

        response = client.post(
            "/api/blobs/write",
            data={
                "blob_uuid": "22222222-2222-2222-2222-222222222222",
                "filename": "doc.pdf",
            },
            files={"file": ("doc.pdf", io.BytesIO(file_content), "application/pdf")},
        )
        assert response.json()["file_hash"] == expected_hash


class TestBlobRegisterInternal:
    """Tests for POST /api/blobs/register."""

    def test_register_blob_success(self, client, mock_storage):
        """Register returns blob_uuid, status, tracking_id."""
        response = client.post(
            "/api/blobs/register",
            json={
                "blob_uuid": "33333333-3333-3333-3333-333333333333",
                "filename": "invoice.pdf",
                "file_size_bytes": 1024,
                "file_hash": "a" * 64,
                "api_key": "test-key-1",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["blob_uuid"] == "33333333-3333-3333-3333-333333333333"
        assert data["status"] == "registered"
        assert data["tracking_id"].startswith("track_")

    def test_register_blob_idempotent(self, client, mock_storage):
        """Registering same blob_uuid twice returns already_registered."""
        payload = {
            "blob_uuid": "44444444-4444-4444-4444-444444444444",
            "filename": "invoice.pdf",
            "file_size_bytes": 2048,
            "file_hash": "b" * 64,
            "api_key": "test-key-1",
        }
        # First registration
        resp1 = client.post("/api/blobs/register", json=payload)
        assert resp1.status_code == 201
        assert resp1.json()["status"] == "registered"

        # Second registration (idempotent)
        resp2 = client.post("/api/blobs/register", json=payload)
        assert resp2.status_code == 201
        assert resp2.json()["status"] == "already_registered"

    def test_register_blob_increments_daily_usage(self, client, mock_storage):
        """Registration increments daily usage counter."""
        payload = {
            "blob_uuid": "55555555-5555-5555-5555-555555555555",
            "filename": "doc.pdf",
            "file_size_bytes": 4096,
            "file_hash": "c" * 64,
            "api_key": "company-a",
        }
        client.post("/api/blobs/register", json=payload)

        # Check daily limit reflects the upload
        resp = client.get("/api/limits/daily?company_id=company-a")
        assert resp.status_code == 200
        assert resp.json()["files_today"] == 1

    def test_register_blob_missing_fields(self, client, mock_storage):
        """Missing required fields return 422."""
        response = client.post(
            "/api/blobs/register",
            json={"blob_uuid": "66666666-6666-6666-6666-666666666666"},
        )
        assert response.status_code == 422
