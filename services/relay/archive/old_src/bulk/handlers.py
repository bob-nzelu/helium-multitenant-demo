"""
FastAPI HTTP Handlers for Bulk Upload

Implements HTTP endpoints:
- POST /api/ingest - Upload 1-3 files for preview
- POST /api/finalize - Finalize with edits
- GET /api/status/{queue_id} - Poll for status
- GET /health - Health check
- GET /metrics - Prometheus metrics

Decision from RELAY_DECISIONS.md:
- HMAC authentication required for all /api/* endpoints
- JSON structured error responses
- Graceful degradation when dependencies unavailable
"""

import logging
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime

from fastapi import (
    FastAPI,
    File,
    UploadFile,
    Form,
    Header,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .service import RelayBulkService
from .validation import BulkValidationPipeline
from ..services.errors import (
    ValidationFailedError,
    AuthenticationFailedError,
    RateLimitExceededError,
    MalwareDetectedError,
    RelayError,
)


logger = logging.getLogger(__name__)


# ============================================================================
# Pydantic Models
# ============================================================================


class FinalizeRequest(BaseModel):
    """Request model for /api/finalize endpoint."""

    batch_id: str = Field(..., description="Batch identifier from ingest response")
    queue_ids: List[str] = Field(..., description="List of queue IDs to finalize")
    edits: Optional[Dict[str, Any]] = Field(None, description="Optional user edits per queue_id")


class ErrorResponse(BaseModel):
    """Standard error response format."""

    status: str = "error"
    error_code: str
    message: str
    details: Optional[List[Dict[str, Any]]] = None
    request_id: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    instance_id: str
    relay_type: str = "bulk"
    version: str = "1.0.0"
    services: Dict[str, str]
    message: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


# ============================================================================
# FastAPI App Factory
# ============================================================================


def create_bulk_app(
    bulk_service: RelayBulkService,
    validation_pipeline: BulkValidationPipeline,
    instance_id: str,
    config: Dict[str, Any],
) -> FastAPI:
    """
    Create FastAPI application for Relay Bulk Upload.

    Args:
        bulk_service: RelayBulkService instance
        validation_pipeline: BulkValidationPipeline instance
        instance_id: Unique instance identifier
        config: Service configuration

    Returns:
        FastAPI application
    """
    app = FastAPI(
        title="Helium Relay Bulk Upload Service",
        description="HTTP API for bulk file uploads from Float UI",
        version="1.0.0",
        docs_url="/docs",  # Swagger UI
        redoc_url="/redoc",  # ReDoc
    )

    # ========================================================================
    # Middleware
    # ========================================================================

    @app.middleware("http")
    async def add_trace_id(request: Request, call_next):
        """Add trace ID to all requests for tracking."""
        trace_id = request.headers.get("X-Trace-ID") or f"req_{uuid.uuid4()}"
        request.state.trace_id = trace_id

        response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id

        return response

    # ========================================================================
    # Error Handlers
    # ========================================================================

    @app.exception_handler(ValidationFailedError)
    async def validation_error_handler(request: Request, exc: ValidationFailedError):
        """Handle validation errors with 400 Bad Request."""
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(
                error_code=exc.error_code,
                message=exc.message,
                details=exc.details,
                request_id=request.state.trace_id,
            ).dict(),
        )

    @app.exception_handler(AuthenticationFailedError)
    async def auth_error_handler(request: Request, exc: AuthenticationFailedError):
        """Handle authentication errors with 401 Unauthorized."""
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=ErrorResponse(
                error_code=exc.error_code,
                message=exc.message,
                request_id=request.state.trace_id,
            ).dict(),
        )

    @app.exception_handler(RateLimitExceededError)
    async def rate_limit_error_handler(request: Request, exc: RateLimitExceededError):
        """Handle rate limit errors with 429 Too Many Requests."""
        response = JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=ErrorResponse(
                error_code=exc.error_code,
                message=exc.message,
                request_id=request.state.trace_id,
            ).dict(),
        )

        if exc.retry_after:
            response.headers["Retry-After"] = exc.retry_after

        return response

    @app.exception_handler(MalwareDetectedError)
    async def malware_error_handler(request: Request, exc: MalwareDetectedError):
        """Handle malware detection errors with 400 Bad Request."""
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(
                error_code=exc.error_code,
                message=exc.message,
                details=exc.details,
                request_id=request.state.trace_id,
            ).dict(),
        )

    @app.exception_handler(RelayError)
    async def relay_error_handler(request: Request, exc: RelayError):
        """Handle generic relay errors with 500 Internal Server Error."""
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error_code=exc.error_code,
                message=exc.message,
                request_id=request.state.trace_id,
            ).dict(),
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception):
        """Handle unexpected errors with 500 Internal Server Error."""
        logger.error(
            f"Unexpected error: {exc}",
            extra={"trace_id": request.state.trace_id},
            exc_info=True,
        )

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error_code="INTERNAL_ERROR",
                message="An unexpected error occurred. Please try again.",
                request_id=request.state.trace_id,
            ).dict(),
        )

    # ========================================================================
    # Endpoints
    # ========================================================================

    @app.post("/api/ingest")
    async def ingest_files(
        request: Request,
        files: List[UploadFile] = File(...),
        company_id: str = Form(...),
        user_id: Optional[str] = Form(None),
        x_api_key: str = Header(..., alias="X-API-Key"),
        x_timestamp: str = Header(..., alias="X-Timestamp"),
        x_signature: str = Header(..., alias="X-Signature"),
    ):
        """
        Upload 1-3 invoice files for preview processing.

        Authentication: HMAC-SHA256 signature required

        Returns:
            Batch result with per-file results and preview data
        """
        trace_id = request.state.trace_id

        logger.info(
            f"Ingest request received - files={len(files)}, company_id={company_id}",
            extra={"trace_id": trace_id},
        )

        # Read file data
        file_tuples = []
        for file in files:
            file_data = await file.read()
            file_tuples.append((file.filename, file_data))

        # Read raw body for HMAC verification
        # Note: FastAPI consumes the body, so we reconstruct it
        # In production, use a middleware to capture raw body before FastAPI parsing
        body = await request.body()

        # Validate all (HMAC, count, extensions, sizes, daily limits, malware)
        await validation_pipeline.validate_all(
            api_key=x_api_key,
            timestamp=x_timestamp,
            signature=x_signature,
            body=body,
            files=file_tuples,
            company_id=company_id,
        )

        # Process batch
        result = await bulk_service.ingest_batch(
            files=file_tuples,
            company_id=company_id,
            user_id=user_id,
            api_key=x_api_key,
        )

        return result

    @app.post("/api/finalize")
    async def finalize_batch(
        request: Request,
        finalize_req: FinalizeRequest,
        x_api_key: str = Header(..., alias="X-API-Key"),
        x_timestamp: str = Header(..., alias="X-Timestamp"),
        x_signature: str = Header(..., alias="X-Signature"),
    ):
        """
        Finalize previewed invoices with optional user edits.

        Authentication: HMAC-SHA256 signature required

        Returns:
            Finalization result with per-queue results
        """
        trace_id = request.state.trace_id

        logger.info(
            f"Finalize request received - batch_id={finalize_req.batch_id}, queue_ids={finalize_req.queue_ids}",
            extra={"trace_id": trace_id},
        )

        # Read raw body for HMAC verification
        body = await request.body()

        # Validate HMAC only (no files to validate)
        # TODO: We need to get company_id from finalize request or lookup from batch_id
        # For now, skip company_id requirement for finalize (HMAC is sufficient)
        validation_pipeline.validate_hmac(
            api_key=x_api_key,
            timestamp=x_timestamp,
            signature=x_signature,
            body=body,
        )

        # Finalize batch
        result = await bulk_service.finalize_batch(
            batch_id=finalize_req.batch_id,
            queue_ids=finalize_req.queue_ids,
            edits=finalize_req.edits,
            user_id=None,  # TODO: Extract from JWT or session
        )

        return result

    @app.get("/api/status/{queue_id}")
    async def get_status(
        request: Request,
        queue_id: str,
    ):
        """
        Get processing status for a queue entry.

        This endpoint allows polling for preview data when Core processing
        takes longer than the initial timeout (5 minutes).

        Returns:
            Status dict with processing state and preview data (if ready)
        """
        trace_id = request.state.trace_id

        logger.info(
            f"Status request received - queue_id={queue_id}",
            extra={"trace_id": trace_id},
        )

        try:
            # Call Core API to get status
            status_response = await bulk_service.core_client.get_status(queue_id)

            return {
                "status": status_response.get("status", "unknown"),
                "queue_id": queue_id,
                "preview_available": status_response.get("preview_available", False),
                "preview_data": status_response.get("preview_data"),
                "statistics": status_response.get("statistics"),
                "message": status_response.get("message"),
            }

        except Exception as e:
            logger.warning(
                f"Status check failed - queue_id={queue_id}: {e}",
                extra={"trace_id": trace_id},
            )

            return {
                "status": "unknown",
                "queue_id": queue_id,
                "message": f"Unable to retrieve status: {str(e)}",
                "retry_suggested": True,
            }

    @app.get("/health", response_model=HealthResponse)
    async def health_check(request: Request):
        """
        Health check endpoint.

        Returns service health and dependency status.
        """
        trace_id = request.state.trace_id

        # Check dependencies
        services = {
            "core_api": "healthy",  # TODO: Ping Core API
            "heartbeat": "healthy",  # TODO: Ping HeartBeat API
            "audit_service": "healthy",  # TODO: Ping Audit API
        }

        # Determine overall status
        unhealthy_services = [name for name, status in services.items() if status != "healthy"]

        if unhealthy_services:
            overall_status = "degraded"
            message = f"Some services unavailable: {', '.join(unhealthy_services)}"
        else:
            overall_status = "healthy"
            message = None

        return HealthResponse(
            status=overall_status,
            instance_id=instance_id,
            services=services,
            message=message,
        )

    @app.get("/metrics")
    async def metrics(request: Request):
        """
        Prometheus metrics endpoint.

        Returns metrics in Prometheus exposition format.

        Metrics exported:
        - helium_relay_files_ingested_total
        - helium_relay_batches_processed_total
        - helium_relay_processing_duration_seconds
        - helium_relay_errors_total
        - helium_relay_active_requests
        - helium_relay_health_status
        - helium_relay_file_size_bytes
        - helium_relay_duplicates_detected_total
        """
        from fastapi.responses import Response
        from .metrics import get_metrics_output, METRICS

        if not METRICS.is_available():
            return {
                "status": "unavailable",
                "message": "Prometheus client (prometheus_client) not installed",
                "install": "pip install prometheus-client",
            }

        content, content_type = get_metrics_output()

        return Response(
            content=content,
            media_type=content_type,
        )

    return app
