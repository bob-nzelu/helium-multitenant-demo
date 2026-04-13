"""CSV parser using stdlib csv module."""

from __future__ import annotations

import csv
import io
import time

from src.ingestion.models import FileType, ParseMetadata, ParseResult, RedFlag
from src.ingestion.parsers.base import BaseParser


class CSVParser(BaseParser):
    """Parse CSV/TSV files with auto-delimiter detection."""

    async def parse(self, content: bytes, filename: str) -> ParseResult:
        start = time.monotonic()
        red_flags: list[RedFlag] = []

        # Decode with BOM handling
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
            red_flags.append(RedFlag(
                field_name="encoding",
                message="File is not UTF-8, decoded as Latin-1",
                severity="warning",
            ))

        # Filter out HDX tag rows (lines starting with #)
        lines = text.splitlines(keepends=True)
        filtered = [ln for ln in lines if not ln.lstrip().startswith("#")]
        clean_text = "".join(filtered)

        if not clean_text.strip():
            return ParseResult(
                file_type=FileType.CSV.value,
                raw_data=[],
                metadata=ParseMetadata(
                    parser_type="csv",
                    original_filename=filename,
                    file_size_bytes=len(content),
                    row_count=0,
                    duration_ms=(time.monotonic() - start) * 1000,
                ),
                red_flags=[RedFlag(
                    field_name="content",
                    message="CSV file is empty after filtering",
                    severity="warning",
                )],
            )

        # Auto-detect delimiter using csv.Sniffer
        try:
            sample = clean_text[:4096]
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel  # fallback to comma

        reader = csv.DictReader(io.StringIO(clean_text), dialect=dialect)
        rows: list[dict] = []
        for row in reader:
            # Replace empty strings with None
            cleaned = {k: (v if v != "" else None) for k, v in row.items()}
            rows.append(cleaned)

        elapsed = (time.monotonic() - start) * 1000
        return ParseResult(
            file_type=FileType.CSV.value,
            raw_data=rows,
            metadata=ParseMetadata(
                parser_type="csv",
                original_filename=filename,
                file_size_bytes=len(content),
                row_count=len(rows),
                has_header=True,
                duration_ms=elapsed,
            ),
            red_flags=red_flags,
        )
