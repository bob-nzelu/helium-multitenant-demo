"""
Tests for Blob Status API (/api/v1/heartbeat/blob/{uuid}/status)

Tests GET and POST status endpoints.
"""

import pytest


class TestGetBlobStatus:
    """Tests for GET /api/v1/heartbeat/blob/{uuid}/status."""

    def _register_blob(self, client, blob_uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"):
        """Helper: register a blob so we can query its status."""
        client.post("/api/blobs/register", json={
            "blob_uuid": blob_uuid,
            "filename": "test.pdf",
            "file_size_bytes": 1024,
            "file_hash": "a" * 64,
            "api_key": "test-source",
        })

    def test_get_status_uploaded(self, client, mock_storage):
        """Newly registered blob has status=uploaded."""
        uuid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        self._register_blob(client, uuid)

        response = client.get(f"/api/v1/heartbeat/blob/{uuid}/status")
        assert response.status_code == 200
        data = response.json()
        assert data["blob_uuid"] == uuid
        assert data["status"] == "uploaded"
        assert data["processing_stage"] is None

    def test_get_status_not_found(self, client, mock_storage):
        """Non-existent blob returns 404."""
        response = client.get(
            "/api/v1/heartbeat/blob/99999999-9999-9999-9999-999999999999/status"
        )
        assert response.status_code == 404

    def test_get_status_has_timestamps(self, client, mock_storage):
        """Status response includes uploaded_at_iso."""
        uuid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        self._register_blob(client, uuid)

        response = client.get(f"/api/v1/heartbeat/blob/{uuid}/status")
        data = response.json()
        assert data["uploaded_at_iso"] is not None


class TestUpdateBlobStatus:
    """Tests for POST /api/v1/heartbeat/blob/{uuid}/status."""

    def _register_blob(self, client, blob_uuid="cccccccc-cccc-cccc-cccc-cccccccccccc"):
        """Helper: register a blob."""
        client.post("/api/blobs/register", json={
            "blob_uuid": blob_uuid,
            "filename": "test.pdf",
            "file_size_bytes": 1024,
            "file_hash": "c" * 64,
            "api_key": "test-source",
        })

    def test_update_to_processing(self, client, mock_storage):
        """Update status to processing with stage."""
        uuid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        self._register_blob(client, uuid)

        response = client.post(f"/api/v1/heartbeat/blob/{uuid}/status", json={
            "status": "processing",
            "processing_stage": "extracting",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["blob_uuid"] == uuid
        assert data["status"] == "processing"
        assert data["updated_at"] is not None

    def test_update_to_finalized(self, client, mock_storage):
        """Update status to finalized."""
        uuid = "dddddddd-dddd-dddd-dddd-dddddddddddd"
        self._register_blob(client, uuid)

        response = client.post(f"/api/v1/heartbeat/blob/{uuid}/status", json={
            "status": "finalized",
        })
        assert response.status_code == 200
        assert response.json()["status"] == "finalized"

    def test_update_reflects_in_get(self, client, mock_storage):
        """Status update is visible in subsequent GET."""
        uuid = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
        self._register_blob(client, uuid)

        # Update
        client.post(f"/api/v1/heartbeat/blob/{uuid}/status", json={
            "status": "processing",
            "processing_stage": "validating",
        })

        # Verify
        resp = client.get(f"/api/v1/heartbeat/blob/{uuid}/status")
        data = resp.json()
        assert data["status"] == "processing"
        assert data["processing_stage"] == "validating"

    def test_update_not_found(self, client, mock_storage):
        """Updating non-existent blob returns 404."""
        response = client.post(
            "/api/v1/heartbeat/blob/99999999-9999-9999-9999-999999999999/status",
            json={"status": "processing"},
        )
        assert response.status_code == 404

    def test_update_invalid_status(self, client, mock_storage):
        """Invalid status value returns 400."""
        uuid = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        self._register_blob(client, uuid)

        response = client.post(f"/api/v1/heartbeat/blob/{uuid}/status", json={
            "status": "invalid_status_value",
        })
        assert response.status_code == 400

    def test_lifecycle_uploaded_to_finalized(self, client, mock_storage):
        """Full lifecycle: uploaded → processing → preview_pending → finalized."""
        uuid = "11111111-2222-3333-4444-555555555555"
        self._register_blob(client, uuid)

        for s in ["processing", "preview_pending", "finalized"]:
            resp = client.post(f"/api/v1/heartbeat/blob/{uuid}/status", json={
                "status": s,
            })
            assert resp.status_code == 200
            assert resp.json()["status"] == s
