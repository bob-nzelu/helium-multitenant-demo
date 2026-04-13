"""
Tests for Deduplication API (/api/dedup/*)

Tests GET /api/dedup/check and POST /api/dedup/record endpoints.
"""

import pytest


class TestDedupCheck:
    """Tests for GET /api/dedup/check."""

    def test_check_no_duplicate(self, client, mock_storage):
        """New hash returns is_duplicate=False."""
        response = client.get(f"/api/dedup/check?file_hash={'a' * 64}")
        assert response.status_code == 200
        data = response.json()
        assert data["is_duplicate"] is False
        assert data["file_hash"] == "a" * 64
        assert data["original_queue_id"] is None

    def test_check_finds_duplicate(self, client, mock_storage):
        """After recording, check returns is_duplicate=True."""
        file_hash = "d" * 64

        # Record the hash first
        client.post("/api/dedup/record", json={
            "file_hash": file_hash,
            "queue_id": "queue-123",
        })

        # Now check — should find duplicate
        response = client.get(f"/api/dedup/check?file_hash={file_hash}")
        assert response.status_code == 200
        data = response.json()
        assert data["is_duplicate"] is True
        assert data["original_queue_id"] == "queue-123"

    def test_check_invalid_hash_length(self, client, mock_storage):
        """Hash shorter than 64 chars returns 422."""
        response = client.get("/api/dedup/check?file_hash=tooshort")
        assert response.status_code == 422

    def test_check_missing_hash(self, client, mock_storage):
        """Missing file_hash parameter returns 422."""
        response = client.get("/api/dedup/check")
        assert response.status_code == 422


class TestDedupRecord:
    """Tests for POST /api/dedup/record."""

    def test_record_success(self, client, mock_storage):
        """Record returns file_hash, queue_id, status=recorded."""
        file_hash = "e" * 64
        response = client.post("/api/dedup/record", json={
            "file_hash": file_hash,
            "queue_id": "queue-456",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["file_hash"] == file_hash
        assert data["queue_id"] == "queue-456"
        assert data["status"] == "recorded"

    def test_record_idempotent(self, client, mock_storage):
        """Recording same hash twice is idempotent."""
        file_hash = "f" * 64
        payload = {"file_hash": file_hash, "queue_id": "queue-789"}

        resp1 = client.post("/api/dedup/record", json=payload)
        assert resp1.status_code == 201

        resp2 = client.post("/api/dedup/record", json=payload)
        assert resp2.status_code == 201
        assert resp2.json()["status"] == "recorded"

    def test_record_missing_fields(self, client, mock_storage):
        """Missing required fields return 422."""
        response = client.post("/api/dedup/record", json={
            "file_hash": "a" * 64,
        })
        assert response.status_code == 422

    def test_record_then_check_roundtrip(self, client, mock_storage):
        """Full roundtrip: record → check → is_duplicate=True."""
        file_hash = "1" * 64
        queue_id = "roundtrip-queue"

        # Record
        client.post("/api/dedup/record", json={
            "file_hash": file_hash,
            "queue_id": queue_id,
        })

        # Check
        resp = client.get(f"/api/dedup/check?file_hash={file_hash}")
        data = resp.json()
        assert data["is_duplicate"] is True
        assert data["original_queue_id"] == queue_id
