"""Parser registry — maps file types to parser instances."""

from __future__ import annotations

from src.errors import ValidationError
from src.ingestion.models import FileType
from src.ingestion.parsers.base import BaseParser


class ParserRegistry:
    """Route file types to the appropriate parser instance."""

    def __init__(self) -> None:
        self._parsers: dict[str, BaseParser] = {}

    def register(self, file_type: FileType, parser: BaseParser) -> None:
        self._parsers[file_type.value] = parser

    def get(self, file_type: str | FileType) -> BaseParser:
        key = file_type.value if isinstance(file_type, FileType) else file_type
        parser = self._parsers.get(key)
        if parser is None:
            raise ValidationError(f"No parser registered for file type: {key}")
        return parser


def create_default_registry() -> ParserRegistry:
    """Create a registry with all built-in parsers."""
    from src.ingestion.parsers.csv_parser import CSVParser
    from src.ingestion.parsers.excel import ExcelParser
    from src.ingestion.parsers.hlm_parser import HLMParser
    from src.ingestion.parsers.hlmz_parser import HLMZParser
    from src.ingestion.parsers.json_parser import JSONParser
    from src.ingestion.parsers.pdf_parser import PDFParser
    from src.ingestion.parsers.xml_parser import XMLParser

    registry = ParserRegistry()
    registry.register(FileType.EXCEL, ExcelParser())
    registry.register(FileType.CSV, CSVParser())
    registry.register(FileType.JSON, JSONParser())
    registry.register(FileType.XML, XMLParser())
    registry.register(FileType.PDF, PDFParser())
    registry.register(FileType.HLM, HLMParser())
    registry.register(FileType.HLMZ, HLMZParser())
    return registry
