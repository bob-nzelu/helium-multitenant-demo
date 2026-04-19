"""
Tests for Daily Limits API (/api/limits/*)

Tests GET /api/limits/daily endpoint.
"""

import pytest


class TestDailyLimit:
    """Tests for GET /api/limits/daily."""

    def test_check_limit_no_usage(self, client, mock_storage):
        """Company with no usage today shows 0 files, full remaining."""
        response = client.get("/api/limits/daily?company_id=new-company")
        assert response.status_code == 200
        data = response.json()
        assert data["company_id"] == "new-company"
        assert data["files_today"] == 0
        assert data["daily_limit"] == 1000
        assert data["limit_reached"] is False
        assert data["remaining"] == 1000

    def test_check_limit_after_uploads(self, client, mock_storage):
        """After registering blobs, files_today reflects uploads."""
        # Register 3 blobs for same company
        for i in range(3):
            uuid = f"{i}0000000-0000-0000-0000-000000000000"
            client.post("/api/blobs/register", json={
                "blob_uuid": uuid,
                "filename": f"file{i}.pdf",
                "file_size_bytes": 1024,
                "file_hash": f"{i}" * 64,
                "api_key": "upload-company",
            })

        response = client.get("/api/limits/daily?company_id=upload-company")
        data = response.json()
        assert data["files_today"] == 3
        assert data["remaining"] == 997

    def test_check_limit_with_file_count(self, client, mock_storage):
        """file_count parameter checks if adding N files would exceed limit."""
        response = client.get("/api/limits/daily?company_id=test-co&file_count=1001")
        data = response.json()
        assert data["limit_reached"] is True

    def test_check_limit_missing_company(self, client, mock_storage):
        """Missing company_id returns 422."""
        response = client.get("/api/limits/daily")
        assert response.status_code == 422

    def test_check_limit_different_companies_isolated(self, client, mock_storage):
        """Usage is tracked per-company, not globally."""
        # Register blob for company-a
        client.post("/api/blobs/register", json={
            "blob_uuid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "filename": "a.pdf",
            "file_size_bytes": 1024,
            "file_hash": "a" * 64,
            "api_key": "company-a",
        })

        # company-b should still have 0
        resp = client.get("/api/limits/daily?company_id=company-b")
        assert resp.json()["files_today"] == 0

        # company-a should have 1
        resp = client.get("/api/limits/daily?company_id=company-a")
        assert resp.json()["files_today"] == 1
