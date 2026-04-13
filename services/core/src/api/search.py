"""
Cross-Entity Search Endpoint (WS4)

POST /api/v1/search - Full-text search across invoices, customers, inventory.
Per MENTAL_MODEL #6 + Q9 APPROVED: tsvector/tsquery with GIN indexes.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request

from src.data import search_repository
from src.models.search import EntitySearchResults, SearchRequest, SearchResponse

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1", tags=["search"])


@router.post("/search")
async def cross_entity_search(
    request: Request,
    body: SearchRequest,
):
    """Search across entity types using PostgreSQL FTS."""

    pool = request.app.state.pool

    date_from = body.filters.date_from if body.filters else None
    date_to = body.filters.date_to if body.filters else None
    status = body.filters.status if body.filters else None

    all_results = await search_repository.search_all(
        pool,
        body.query,
        body.entity_types,
        page=body.page,
        per_page=body.per_page,
        date_from=date_from,
        date_to=date_to,
        status=status,
    )

    results = {}

    type_to_key = {
        "invoice": "invoices",
        "customer": "customers",
        "inventory": "inventories",
    }

    for entity_type, (items, total_count) in all_results.items():
        key = type_to_key.get(entity_type, entity_type)
        results[key] = EntitySearchResults(
            total_count=total_count,
            page=body.page,
            per_page=body.per_page,
            items=items,
        )

    logger.info(
        "cross_entity_search",
        query=body.query,
        entity_types=body.entity_types,
        result_counts={k: v.total_count for k, v in results.items()},
    )

    return SearchResponse(
        query=body.query,
        results=results,
    )
