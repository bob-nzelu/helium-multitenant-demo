"""
WS1 Ingestion Models

Dataclasses for internal pipeline data, Pydantic models for API request/response.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────────


class FileType(str, enum.Enum):
    """Supported file types for ingestion."""

    EXCEL = "excel"
    CSV = "csv"
    JSON = "json"
    XML = "xml"
    PDF = "pdf"
    HLM = "hlm"
    HLMZ = "hlmz"


# ── Internal dataclasses ───────────────────────────────────────────────────


@dataclass
class RedFlag:
    """Parse-time warning or error."""

    field_name: str
    message: str
    severity: str = "warning"  # "warning" | "error" | "info"


@dataclass
class ParseMetadata:
    """Metadata about the parse operation."""

    parser_type: str
    original_filename: str
    file_size_bytes: int = 0
    row_count: int = 0
    sheet_names: list[str] = field(default_factory=lambda: ["default"])
    encoding: str = "utf-8"
    has_header: bool = True
    duration_ms: float = 0.0


@dataclass
class ParseResult:
    """Output of any parser — the universal exchange format for WS1."""

    file_type: str  # FileType.value
    raw_data: dict | list  # Parsed content (rows as dicts for tabular, raw for others)
    metadata: ParseMetadata
    is_hlm: bool = False  # True → WS2 Transformer skips Transforma
    file_hash: str = ""  # SHA256 hex digest of raw bytes
    red_flags: list[RedFlag] = field(default_factory=list)


@dataclass
class BlobResponse:
    """Response from HeartBeat blob download."""

    content: bytes
    content_type: str
    filename: str
    size: int
    blob_hash: str = ""


@dataclass
class DedupResult:
    """Result of SHA256 deduplication check."""

    is_duplicate: bool
    file_hash: str
    existing_queue_id: str | None = None
    existing_filename: str | None = None


@dataclass
class QueueEntry:
    """In-memory representation of a core_queue row."""

    queue_id: str
    blob_uuid: str
    data_uuid: str
    original_filename: str
    company_id: str
    uploaded_by: str
    batch_id: str | None
    status: str
    priority: int
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None = None
    processing_started_at: datetime | None = None
    error_message: str | None = None
    retry_count: int = 0
    max_attempts: int = 3


# ── Pydantic request/response models ──────────────────────────────────────


class EnqueueRequest(BaseModel):
    """POST /api/v1/enqueue request body."""

    blob_uuid: str = Field(..., min_length=1, max_length=100)
    data_uuid: str = Field(..., min_length=1, max_length=100)
    original_filename: str = Field(..., min_length=1, max_length=500)
    company_id: str = Field(..., min_length=1, max_length=100)
    uploaded_by: str = Field("", max_length=100)
    batch_id: str | None = Field(None, max_length=100)
    priority: int = Field(3, ge=1, le=5)


class EnqueueResponse(BaseModel):
    """POST /api/v1/enqueue response."""

    queue_id: str
    status: str
    data_uuid: str
    created_at: str


class QueueStatusEntry(BaseModel):
    """Single entry in queue status response."""

    queue_id: str
    blob_uuid: str
    data_uuid: str
    status: str
    company_id: str
    original_filename: str | None
    created_at: str
    updated_at: str
    processed_at: str | None
    error_message: str | None


class QueueStatusResponse(BaseModel):
    """GET /api/v1/core_queue/status response."""

    entries: list[QueueStatusEntry]
    total: int
    limit: int
    offset: int
