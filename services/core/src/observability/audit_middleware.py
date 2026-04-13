"""
WS6: Entity CRUD Audit Middleware

Intercepts mutating HTTP requests (PUT, PATCH, DELETE) to WS4 entity
endpoints and logs audit events. This is necessary because WS4 source
files are compiled (.pyc only) and cannot be directly modified.

This middleware observes at the HTTP boundary — it captures the
entity_type, entity_id, action, and response status for all mutations.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger()

# Entity endpoint patterns
_ENTITY_PATTERNS = [
    (re.compile(r"^/api/v1/invoices/([^/]+)$"), "invoice"),
    (re.compile(r"^/api/v1/customers/([^/]+)$"), "customer"),
    (re.compile(r"^/api/v1/inventory/([^/]+)$"), "inventory"),
]

# Search endpoint
_SEARCH_PATTERN = re.compile(r"^/api/v1/search")

# Map HTTP methods to audit actions
_METHOD_ACTION_MAP = {
    "POST": "CREATE",
    "PUT": "UPDATE",
    "PATCH": "UPDATE",
    "DELETE": "DELETE",
}


class EntityAuditMiddleware(BaseHTTPMiddleware):
    """
    Logs audit events for WS4 entity mutations.

    Intercepts PUT/PATCH/DELETE on entity endpoints and POST on search.
    Fire-and-forget: audit failures never block the response.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path
        method = request.method

        # Only audit mutations and search
        if method not in ("PUT", "PATCH", "DELETE", "POST"):
            return await call_next(request)

        # Check entity patterns
        entity_type = None
        entity_id = None
        for pattern, etype in _ENTITY_PATTERNS:
            match = pattern.match(path)
            if match:
                entity_type = etype
                entity_id = match.group(1)
                break

        # Check search
        is_search = bool(_SEARCH_PATTERN.match(path)) and method in ("GET", "POST")

        if not entity_type and not is_search:
            return await call_next(request)

        response = await call_next(request)

        # Fire-and-forget audit
        audit_logger = getattr(request.app.state, "audit_logger", None)
        if audit_logger and entity_type:
            action = _METHOD_ACTION_MAP.get(method, method)
            event_type = f"{entity_type}.{action.lower()}d" if action != "DELETE" else f"{entity_type}.deleted"
            if action == "CREATE":
                event_type = f"{entity_type}.created"
            elif action == "UPDATE":
                event_type = f"{entity_type}.updated"

            try:
                # Extract company_id and user_id from headers or query
                company_id = (
                    request.headers.get("x-company-id", "")
                    or request.query_params.get("company_id", "")
                )
                actor_id = (
                    request.headers.get("x-user-id", "")
                    or request.query_params.get("user_id", "")
                )

                await audit_logger.log(
                    event_type=event_type,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    action=action,
                    actor_id=actor_id or None,
                    company_id=company_id,
                    metadata={
                        "http_method": method,
                        "path": path,
                        "status_code": response.status_code,
                    },
                )
            except Exception as e:
                logger.error("entity_audit_middleware_error", error=str(e))

        if audit_logger and is_search:
            try:
                await audit_logger.log(
                    event_type="search.executed",
                    entity_type="search",
                    action="PROCESS",
                    metadata={
                        "path": path,
                        "query_string": str(request.query_params),
                        "status_code": response.status_code,
                    },
                )
            except Exception as e:
                logger.error("search_audit_middleware_error", error=str(e))

        return response
