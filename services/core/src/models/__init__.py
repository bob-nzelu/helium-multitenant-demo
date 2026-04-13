"""
Core Service Pydantic Models

Re-exports from base for backwards compatibility with existing imports.
"""

from src.models.base import ErrorResponse, HealthResponse, PaginatedResponse

__all__ = ["ErrorResponse", "HealthResponse", "PaginatedResponse"]
