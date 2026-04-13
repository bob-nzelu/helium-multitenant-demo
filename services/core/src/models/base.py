"""
Core Service Base Pydantic Models

Shared response models used across all workstreams.
"""

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorResponse(BaseModel):
    """Standard error response format."""

    error: str = Field(description="Error code (e.g. INV_001)")
    message: str = Field(description="Human-readable error message")
    details: list[dict[str, str]] | None = Field(
        default=None,
        description="Optional field-level error details",
    )


class HealthResponse(BaseModel):
    """GET /api/v1/health response."""

    status: str = Field(description="healthy, degraded, or unhealthy")
    version: str = Field(description="Service version (semver)")
    uptime_seconds: float = Field(description="Seconds since service started")
    database: str = Field(description="connected or disconnected")
    scheduler: str = Field(description="running or stopped")


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response wrapper."""

    items: list[T] = Field(description="Page of results")
    total: int = Field(description="Total matching records")
    page: int = Field(description="Current page number (1-based)")
    per_page: int = Field(description="Items per page")
    pages: int = Field(description="Total pages")
