"""PDF parser — STUB. Returns raw text placeholder. IntelliCore integration pending."""

from __future__ import annotations

import time

from src.ingestion.models import FileType, ParseMetadata, ParseResult, RedFlag
from src.ingestion.parsers.base import BaseParser


class PDFParser(BaseParser):
    """
    Stub PDF parser.

    Returns raw bytes decoded as text. Real extraction (via Textract/IntelliCore)
    will be wired in a future workstream.
    """

    async def parse(self, content: bytes, filename: str) -> ParseResult:
        start = time.monotonic()

        # Best-effort text extraction from raw bytes
        try:
            text = content.decode("utf-8", errors="ignore")
        except Exception:
            text = ""

        elapsed = (time.monotonic() - start) * 1000
        return ParseResult(
            file_type=FileType.PDF.value,
            raw_data={"raw_text": text, "stub": True},
            metadata=ParseMetadata(
                parser_type="pdf_stub",
                original_filename=filename,
                file_size_bytes=len(content),
                row_count=0,
                duration_ms=elapsed,
            ),
            red_flags=[
                RedFlag(
                    field_name="parser",
                    message="PDF parsing is a stub — structured extraction via IntelliCore pending",
                    severity="info",
                )
            ],
        )
