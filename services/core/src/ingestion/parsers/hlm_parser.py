"""HLM parser — pass-through for pre-structured .hlm files."""

from __future__ import annotations

import json
import time

from src.errors import ValidationError
from src.ingestion.models import FileType, ParseMetadata, ParseResult
from src.ingestion.parsers.base import BaseParser


class HLMParser(BaseParser):
    """
    Parse .hlm files — already HLM-shaped JSON.

    Sets is_hlm=True so WS2's Transformer skips Transforma entirely.
    """

    async def parse(self, content: bytes, filename: str) -> ParseResult:
        start = time.monotonic()

        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValidationError(
                f"Invalid JSON in HLM file '{filename}': {e}",
                details=[{"field": "content", "message": str(e)}],
            )

        if not isinstance(data, dict):
            raise ValidationError(
                f"HLM file must be a JSON object, got {type(data).__name__}",
            )

        # Validate HLM structure — must have hlm_version
        if "hlm_version" not in data:
            raise ValidationError(
                f"File '{filename}' claims to be .hlm but has no 'hlm_version' key",
            )

        elapsed = (time.monotonic() - start) * 1000
        return ParseResult(
            file_type=FileType.HLM.value,
            raw_data=data,
            metadata=ParseMetadata(
                parser_type="hlm",
                original_filename=filename,
                file_size_bytes=len(content),
                row_count=1,
                duration_ms=elapsed,
            ),
            is_hlm=True,
        )
