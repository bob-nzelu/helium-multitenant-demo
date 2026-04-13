"""Base parser abstract class."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.ingestion.models import ParseResult


class BaseParser(ABC):
    """All parsers implement this interface."""

    @abstractmethod
    async def parse(self, content: bytes, filename: str) -> ParseResult:
        """
        Parse raw file bytes into a ParseResult.

        Args:
            content: Raw file bytes.
            filename: Original filename (for metadata/logging).

        Returns:
            ParseResult with parsed data, metadata, and any red flags.
        """
        ...
