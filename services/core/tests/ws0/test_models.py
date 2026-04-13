"""Tests for base Pydantic models — validation, serialization."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from src.models import ErrorResponse, HealthResponse, PaginatedResponse


class TestErrorResponse:
    """Test ErrorResponse model."""

    def test_minimal(self):
        r = ErrorResponse(error="INV_001", message="bad input")
        assert r.error == "INV_001"
        assert r.message == "bad input"
        assert r.details is None

    def test_with_details(self):
        r = ErrorResponse(
            error="INV_001",
            message="bad",
            details=[{"field": "name", "error": "required"}],
        )
        assert len(r.details) == 1
        assert r.details[0]["field"] == "name"

    def test_serialization(self):
        r = ErrorResponse(error="INV_002", message="not found")
        d = r.model_dump()
        assert d == {"error": "INV_002", "message": "not found", "details": None}

    def test_serialization_exclude_none(self):
        r = ErrorResponse(error="INV_002", message="not found")
        d = r.model_dump(exclude_none=True)
        assert "details" not in d

    def test_json_roundtrip(self):
        r = ErrorResponse(error="INV_013", message="oops")
        j = r.model_dump_json()
        r2 = ErrorResponse.model_validate_json(j)
        assert r2.error == r.error
        assert r2.message == r.message


class TestHealthResponse:
    """Test HealthResponse model."""

    def test_healthy(self):
        r = HealthResponse(
            status="healthy",
            version="0.1.0",
            uptime_seconds=123.45,
            database="connected",
            scheduler="running",
        )
        assert r.status == "healthy"
        assert r.version == "0.1.0"
        assert r.uptime_seconds == 123.45
        assert r.database == "connected"
        assert r.scheduler == "running"

    def test_degraded(self):
        r = HealthResponse(
            status="degraded",
            version="0.1.0",
            uptime_seconds=0.0,
            database="disconnected",
            scheduler="running",
        )
        assert r.status == "degraded"

    def test_serialization(self):
        r = HealthResponse(
            status="healthy",
            version="0.1.0",
            uptime_seconds=60.0,
            database="connected",
            scheduler="running",
        )
        d = r.model_dump()
        assert set(d.keys()) == {"status", "version", "uptime_seconds", "database", "scheduler"}

    def test_missing_required_field(self):
        with pytest.raises(PydanticValidationError):
            HealthResponse(status="healthy", version="0.1.0")  # missing fields


class TestPaginatedResponse:
    """Test generic PaginatedResponse model."""

    def test_basic(self):
        r = PaginatedResponse[str](
            items=["a", "b"],
            total=10,
            page=1,
            per_page=2,
            pages=5,
        )
        assert r.items == ["a", "b"]
        assert r.total == 10
        assert r.page == 1
        assert r.per_page == 2
        assert r.pages == 5

    def test_empty_page(self):
        r = PaginatedResponse[int](
            items=[],
            total=0,
            page=1,
            per_page=25,
            pages=0,
        )
        assert r.items == []
        assert r.total == 0

    def test_serialization(self):
        r = PaginatedResponse[str](
            items=["x"],
            total=1,
            page=1,
            per_page=10,
            pages=1,
        )
        d = r.model_dump()
        assert d["items"] == ["x"]
        assert d["total"] == 1

    def test_with_dict_items(self):
        r = PaginatedResponse[dict](
            items=[{"id": "abc", "name": "Test"}],
            total=1,
            page=1,
            per_page=25,
            pages=1,
        )
        assert r.items[0]["id"] == "abc"
