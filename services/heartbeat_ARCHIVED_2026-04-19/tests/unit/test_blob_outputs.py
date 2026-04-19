"""
Tests for Blob Outputs API (P2-F)

Tests cover:
    1. Register a processed output
    2. List outputs for a blob
    3. Get specific output by type
    4. 404 for non-existent blob/output
    5. Upsert behavior (register same type twice)
    6. Access counter increments
"""

import pytest


# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════

def _get_test_blob_uuid(client):
    """Get the first dev blob UUID from the seeded database."""
    return "dev00001-0000-0000-0000-000000000001"


def _register_output(client, blob_uuid, output_type="firs_invoices"):
    """Helper: register a processed output."""
    return client.post("/api/outputs/register", json={
        "blob_uuid": blob_uuid,
        "output_type": output_type,
        "object_path": f"/files_blob/{blob_uuid}/processed/{output_type}.json",
        "content_type": "application/json",
        "size_bytes": 4096,
        "file_hash": "b" * 64,
        "core_version": "1.0.0",
    })


# ══════════════════════════════════════════════════════════════════════════
# TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestBlobOutputsAPI:
    """Blob Outputs CRUD tests."""

    def test_register_output(self, client):
        """POST /api/outputs/register succeeds for existing blob."""
        uuid = _get_test_blob_uuid(client)
        resp = _register_output(client, uuid)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "registered"
        assert data["blob_uuid"] == uuid
        assert data["output_type"] == "firs_invoices"

    def test_register_output_nonexistent_blob(self, client):
        """POST /api/outputs/register returns 404 for missing blob."""
        resp = _register_output(client, "nonexistent-uuid-0000")
        assert resp.status_code == 404

    def test_list_outputs_empty(self, client):
        """GET /api/outputs/{uuid} returns empty list for blob with no outputs."""
        uuid = _get_test_blob_uuid(client)
        resp = client.get(f"/api/outputs/{uuid}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["blob_uuid"] == uuid
        assert data["count"] == 0
        assert data["outputs"] == []

    def test_list_outputs_after_register(self, client):
        """GET /api/outputs/{uuid} shows registered output."""
        uuid = _get_test_blob_uuid(client)
        _register_output(client, uuid, "firs_invoices")
        _register_output(client, uuid, "report")

        resp = client.get(f"/api/outputs/{uuid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        types = {o["output_type"] for o in data["outputs"]}
        assert types == {"firs_invoices", "report"}

    def test_get_specific_output(self, client):
        """GET /api/outputs/{uuid}/{type} returns the specific output."""
        uuid = _get_test_blob_uuid(client)
        _register_output(client, uuid, "customers")

        resp = client.get(f"/api/outputs/{uuid}/customers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["blob_uuid"] == uuid
        assert data["output_type"] == "customers"
        assert data["content_type"] == "application/json"

    def test_get_nonexistent_output_type(self, client):
        """GET /api/outputs/{uuid}/{type} returns 404 for missing type."""
        uuid = _get_test_blob_uuid(client)
        resp = client.get(f"/api/outputs/{uuid}/nonexistent_type")
        assert resp.status_code == 404

    def test_upsert_replaces_existing(self, client):
        """Registering same (blob_uuid, output_type) updates the entry."""
        uuid = _get_test_blob_uuid(client)

        # First registration
        _register_output(client, uuid, "firs_invoices")

        # Second registration with different size
        resp = client.post("/api/outputs/register", json={
            "blob_uuid": uuid,
            "output_type": "firs_invoices",
            "object_path": f"/files_blob/{uuid}/processed/firs_invoices_v2.json",
            "content_type": "application/json",
            "size_bytes": 8192,
        })
        assert resp.status_code == 200

        # Verify only 1 output, with updated path
        resp = client.get(f"/api/outputs/{uuid}")
        assert resp.json()["count"] == 1
        assert "v2" in resp.json()["outputs"][0]["object_path"]

    def test_access_counter_increments(self, client):
        """GET /api/outputs/{uuid}/{type} increments access counter."""
        uuid = _get_test_blob_uuid(client)
        _register_output(client, uuid, "report")

        # Access twice
        client.get(f"/api/outputs/{uuid}/report")
        resp = client.get(f"/api/outputs/{uuid}/report")

        assert resp.status_code == 200
        # After 2 accesses, counter should be 2
        assert resp.json()["accessed_count"] == 2
