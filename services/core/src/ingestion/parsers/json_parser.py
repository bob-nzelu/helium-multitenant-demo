"""JSON parser using stdlib."""

from __future__ import annotations

import json
import time

from src.errors import ValidationError
from src.ingestion.models import FileType, ParseMetadata, ParseResult
from src.ingestion.parsers.base import BaseParser


class JSONParser(BaseParser):
    """Parse JSON files — handles both objects and arrays."""

    async def parse(self, content: bytes, filename: str) -> ParseResult:
        start = time.monotonic()

        # Decode with BOM handling
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValidationError(
                f"Invalid JSON in '{filename}': {e}",
                details=[{"field": "content", "message": str(e)}],
            )

        # Normalize: single object → list
        if isinstance(data, dict):
            rows = [data]
        elif isinstance(data, list):
            rows = data
        else:
            raise ValidationError(
                f"JSON root must be object or array, got {type(data).__name__}",
            )

        elapsed = (time.monotonic() - start) * 1000
        return ParseResult(
            file_type=FileType.JSON.value,
            raw_data=rows,
            metadata=ParseMetadata(
                parser_type="json",
                original_filename=filename,
                file_size_bytes=len(content),
                row_count=len(rows),
                duration_ms=elapsed,
            ),
        )
