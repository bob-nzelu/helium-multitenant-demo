"""
Search Pydantic Models (WS4)

Per WS4 API_CONTRACTS.md Endpoint 9: POST /api/v1/search.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from src.errors import CoreError, CoreErrorCode


class SearchFilters(BaseModel):
    """Optional filters for search (invoice-specific)."""

    date_from: str | None = None
    date_to: str | None = None
    status: list[str] | None = None


class SearchRequest(BaseModel):
    """POST /search request body."""

    query: str
    entity_types: list[str] | None = Field(
        default=None,
        description="Entity types to search. Default: all three.",
    )
    filters: SearchFilters | None = None
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def apply_defaults_and_validate(self) -> SearchRequest:
        stripped = self.query.strip()

        if len(stripped) < 2:
            raise CoreError(
                error_code=CoreErrorCode.SEARCH_QUERY_TOO_SHORT,
                message="Search query must be at least 2 characters",
            )

        if len(stripped) > 500:
            raise CoreError(
                error_code=CoreErrorCode.SEARCH_QUERY_TOO_LONG,
                message="Search query exceeds 500 characters",
            )

        self.query = stripped

        if self.entity_types is None:
            self.entity_types = ["invoice", "customer", "inventory"]
        else:
            valid = {"invoice", "customer", "inventory"}
            for et in self.entity_types:
                if et not in valid:
                    raise CoreError(
                        error_code=CoreErrorCode.INVALID_ENTITY_TYPE,
                        message=f"Entity type '{et}' is not valid. Must be: invoice, customer, inventory",
                    )

        return self


class EntitySearchResults(BaseModel):
    """Per-entity search results with pagination."""

    total_count: int
    page: int
    per_page: int
    items: list[dict[str, Any]]


class SearchResponse(BaseModel):
    """POST /search response."""

    query: str
    results: dict[str, EntitySearchResults]
