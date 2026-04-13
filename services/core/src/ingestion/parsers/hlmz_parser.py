"""HLMZ parser — gzip-compressed HLM. Decompress and delegate to HLMParser."""

from __future__ import annotations

import gzip

from src.errors import ValidationError
from src.ingestion.models import FileType, ParseResult
from src.ingestion.parsers.base import BaseParser
from src.ingestion.parsers.hlm_parser import HLMParser


class HLMZParser(BaseParser):
    """Parse .hlmz files — gzip-compressed .hlm JSON."""

    def __init__(self) -> None:
        self._hlm_parser = HLMParser()

    async def parse(self, content: bytes, filename: str) -> ParseResult:
        try:
            decompressed = gzip.decompress(content)
        except (gzip.BadGzipFile, OSError) as e:
            raise ValidationError(
                f"Invalid gzip in HLMZ file '{filename}': {e}",
                details=[{"field": "content", "message": str(e)}],
            )

        # Strip .hlmz → .hlm for inner filename
        inner_name = filename.rsplit(".", 1)[0] + ".hlm" if "." in filename else filename

        result = await self._hlm_parser.parse(decompressed, inner_name)
        # Override file_type to hlmz (is_hlm already True from HLMParser)
        result.file_type = FileType.HLMZ.value
        result.metadata.parser_type = "hlmz"
        result.metadata.file_size_bytes = len(content)
        return result
