"""
Blob Registration API Endpoint

POST /api/v1/heartbeat/blob/register

Receives blob registration requests from Relay services after successful blob storage write.
Creates file_entries record for tracking, retention, and reconciliation.

Response Codes:
- 201 Created: Blob registered successfully
- 409 Conflict: Duplicate blob_uuid or blob_path (UNIQUE constraint violation)
- 401 Unauthorized: Missing or invalid authorization token
- 400 Bad Request: Invalid request body
- 500 Internal Server Error: Database error
"""

import logging
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, status
from pydantic import BaseModel, Field, validator

from ..database import get_blob_database


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/heartbeat/blob", tags=["blob"])


# Request/Response Models

class BlobRegisterRequest(BaseModel):
    """Request body for blob registration"""

    blob_uuid: str = Field(
        ...,
        description="Unique blob identifier (UUIDv4)",
        min_length=36,
        max_length=36
    )
    blob_path: str = Field(
        ...,
        description="Storage object path (/files_blob/{uuid}-{filename})",
        min_length=1,
        max_length=500
    )
    file_size_bytes: int = Field(
        ...,
        description="File size in bytes",
        gt=0
    )
    file_hash: str = Field(
        ...,
        description="SHA256 hash of file",
        min_length=64,
        max_length=64
    )
    content_type: str = Field(
        ...,
        description="MIME type (e.g., application/pdf)",
        min_length=1,
        max_length=100
    )
    source: str = Field(
        ...,
        description="Source relay instance ID (e.g., execujet-bulk-1)",
        min_length=1,
        max_length=100
    )

    @validator('blob_uuid')
    def validate_blob_uuid(cls, v):
        """Validate UUID format"""
        if not v or len(v) != 36:
            raise ValueError("blob_uuid must be valid UUIDv4 (36 characters)")
        return v

    @validator('blob_path')
    def validate_blob_path(cls, v):
        """Validate blob path format"""
        if not v.startswith("/files_blob/"):
            raise ValueError("blob_path must start with /files_blob/")
        return v

    @validator('file_hash')
    def validate_file_hash(cls, v):
        """Validate SHA256 hash format"""
        if not v or len(v) != 64:
            raise ValueError("file_hash must be SHA256 (64 hex characters)")
        # Ensure it's valid hex
        try:
            int(v, 16)
        except ValueError:
            raise ValueError("file_hash must be valid hexadecimal")
        return v.lower()


class BlobRegisterResponse(BaseModel):
    """Response for successful blob registration"""

    status: str = Field(..., description="Response status")
    blob_uuid: str = Field(..., description="Registered blob UUID")
    message: str = Field(..., description="Human-readable message")


class ErrorResponse(BaseModel):
    """Error response format"""

    status: str = Field(..., description="Error status")
    blob_uuid: Optional[str] = Field(None, description="Blob UUID if applicable")
    message: str = Field(..., description="Error message")
    error_code: Optional[str] = Field(None, description="Error code for debugging")


# Authentication

def validate_authorization(authorization: Optional[str] = Header(None)) -> bool:
    """
    Validate Authorization header.

    For Phase 2: Simple Bearer token validation.
    Future: Implement proper JWT validation or API key management.

    Args:
        authorization: Authorization header value

    Returns:
        True if valid

    Raises:
        HTTPException: 401 if invalid or missing
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header"
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization must be Bearer token"
        )

    token = authorization.replace("Bearer ", "").strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Empty authorization token"
        )

    # Phase 2: Simple validation (non-empty token)
    # Future: Validate against token database or JWT signature
    # For now, accept any non-empty Bearer token

    return True


# API Endpoints

@router.post(
    "/register",
    response_model=BlobRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {
            "description": "Blob registered successfully",
            "model": BlobRegisterResponse
        },
        409: {
            "description": "Duplicate blob (UNIQUE constraint violation)",
            "model": ErrorResponse
        },
        401: {
            "description": "Unauthorized (missing or invalid token)",
            "model": ErrorResponse
        },
        400: {
            "description": "Bad request (invalid request body)",
            "model": ErrorResponse
        },
        500: {
            "description": "Internal server error (database error)",
            "model": ErrorResponse
        }
    }
)
async def register_blob(
    request: BlobRegisterRequest,
    authorization: Optional[str] = Header(None)
):
    """
    Register blob with HeartBeat after successful storage write.

    Called by Relay services immediately after writing file to blob storage.
    Creates file_entries record for:
    - Compliance tracking (7-year retention)
    - Reconciliation (periodic sync between storage and database)
    - Lifecycle management (soft delete, hard delete)

    **Authentication:**
    Requires `Authorization: Bearer <token>` header.

    **Idempotent:**
    Returns 409 Conflict if blob_uuid or blob_path already exists.
    Safe to retry on network failures.

    **Retention Policy:**
    - Original files: 7-year retention from upload
    - retention_until calculated automatically

    **Example Request:**
    ```json
    {
        "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
        "blob_path": "/files_blob/550e8400-e29b-41d4-a716-446655440000-invoice.pdf",
        "file_size_bytes": 2048576,
        "file_hash": "abc123...",
        "content_type": "application/pdf",
        "source": "execujet-bulk-1"
    }
    ```

    **Example Response (201):**
    ```json
    {
        "status": "created",
        "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
        "message": "Blob registered successfully"
    }
    ```

    **Example Response (409):**
    ```json
    {
        "status": "conflict",
        "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
        "message": "Blob already registered (duplicate blob_uuid)"
    }
    ```
    """

    # Validate authorization
    validate_authorization(authorization)

    # Calculate timestamps
    now = datetime.utcnow()
    uploaded_at_unix = int(now.timestamp())
    uploaded_at_iso = now.isoformat()

    # Calculate retention (7 years for original files - compliance requirement)
    retention_until = now + timedelta(days=365 * 7)
    retention_until_unix = int(retention_until.timestamp())
    retention_until_iso = retention_until.isoformat()

    # Get database instance
    db = get_blob_database()

    try:
        # Insert file_entries record (canonical schema v1.4.0)
        # Extract filename from blob_path for original_filename
        original_filename = request.blob_path.split("/")[-1]

        row_id = db.register_blob(
            blob_uuid=request.blob_uuid,
            blob_path=request.blob_path,
            file_size_bytes=request.file_size_bytes,
            file_hash=request.file_hash,
            content_type=request.content_type,
            source=request.source,
            uploaded_at_unix=uploaded_at_unix,
            uploaded_at_iso=uploaded_at_iso,
            retention_until_unix=retention_until_unix,
            retention_until_iso=retention_until_iso,
            original_filename=original_filename,
        )

        logger.info(
            f"Blob registered successfully: uuid={request.blob_uuid}, "
            f"path={request.blob_path}, source={request.source}, row_id={row_id}"
        )

        return BlobRegisterResponse(
            status="created",
            blob_uuid=request.blob_uuid,
            message="Blob registered successfully"
        )

    except sqlite3.IntegrityError as e:
        error_msg = str(e).lower()

        # Determine which UNIQUE constraint failed
        if "blob_uuid" in error_msg:
            conflict_field = "blob_uuid"
        elif "blob_path" in error_msg:
            conflict_field = "blob_path"
        else:
            conflict_field = "unknown field"

        logger.warning(
            f"Duplicate blob registration attempt: {conflict_field}={request.blob_uuid}, "
            f"error={error_msg}"
        )

        # Return 409 Conflict (idempotent - safe to retry)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "status": "conflict",
                "blob_uuid": request.blob_uuid,
                "message": f"Blob already registered (duplicate {conflict_field})",
                "error_code": "DUPLICATE_BLOB"
            }
        )

    except sqlite3.OperationalError as e:
        # Database locked or operational issue
        logger.error(
            f"Database operational error during blob registration: "
            f"uuid={request.blob_uuid}, error={str(e)}"
        )

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "error",
                "message": "Database temporarily unavailable, please retry",
                "error_code": "DATABASE_LOCKED"
            }
        )

    except Exception as e:
        # Unexpected database error
        logger.error(
            f"Unexpected error during blob registration: "
            f"uuid={request.blob_uuid}, error={str(e)}",
            exc_info=True
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "message": "Internal server error during blob registration",
                "error_code": "REGISTRATION_FAILED"
            }
        )


@router.get(
    "/{blob_uuid}",
    response_model=dict,
    responses={
        200: {"description": "Blob found"},
        404: {"description": "Blob not found"},
        401: {"description": "Unauthorized"}
    }
)
async def get_blob(
    blob_uuid: str,
    authorization: Optional[str] = Header(None)
):
    """
    Get blob information by UUID.

    Returns blob metadata including:
    - Status (uploaded, processing, finalized)
    - File size and hash
    - Retention information
    - Source relay instance

    **Authentication:**
    Requires `Authorization: Bearer <token>` header.
    """

    # Validate authorization
    validate_authorization(authorization)

    db = get_blob_database()

    blob = db.get_blob(blob_uuid)

    if not blob:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "status": "not_found",
                "blob_uuid": blob_uuid,
                "message": "Blob not found"
            }
        )

    return blob


@router.get(
    "/health",
    response_model=dict,
    responses={
        200: {"description": "Service healthy"}
    }
)
async def health_check():
    """
    Health check endpoint for HeartBeat blob service.

    Returns service status and database connectivity.
    """
    db = get_blob_database()

    # Test database connection (canonical table: file_entries)
    try:
        query = "SELECT COUNT(*) as count FROM file_entries"
        result = db.execute_query(query)
        blob_count = result[0]["count"] if result else 0

        return {
            "status": "healthy",
            "service": "heartbeat-blob",
            "database": "connected",
            "file_entries_count": blob_count,
            "timestamp": datetime.utcnow().isoformat()
        }

    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")

        return {
            "status": "unhealthy",
            "service": "heartbeat-blob",
            "database": "disconnected",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }
