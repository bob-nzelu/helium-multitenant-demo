"""
File Type Detection

Detects file type from magic bytes first, filename extension fallback.
"""

from __future__ import annotations

import io
import zipfile

from src.errors import ValidationError
from src.ingestion.models import FileType

# Magic byte signatures
_PDF_MAGIC = b"%PDF"
_ZIP_MAGIC = b"PK\x03\x04"
_GZIP_MAGIC = b"\x1f\x8b"
_XML_MAGIC = b"<?xml"
_BOM_UTF8 = b"\xef\xbb\xbf"

_EXTENSION_MAP: dict[str, FileType] = {
    ".xlsx": FileType.EXCEL,
    ".xls": FileType.EXCEL,
    ".csv": FileType.CSV,
    ".tsv": FileType.CSV,
    ".json": FileType.JSON,
    ".xml": FileType.XML,
    ".pdf": FileType.PDF,
    ".hlm": FileType.HLM,
    ".hlmz": FileType.HLMZ,
}


def detect_file_type(content: bytes, filename: str) -> FileType:
    """
    Detect file type from magic bytes first, filename extension fallback.

    Priority:
    1. Magic bytes (most reliable)
    2. Filename extension (fallback)
    3. Content heuristics (CSV detection)

    Raises:
        ValidationError: If file type cannot be determined.
    """
    if not content:
        raise ValidationError("Empty file content")

    # Strip BOM for analysis
    analysis = content.lstrip(_BOM_UTF8)

    # 1. Check .hlm/.hlmz by extension first (they use JSON/gzip magic internally)
    lower_name = filename.lower()
    if lower_name.endswith(".hlmz"):
        return FileType.HLMZ
    if lower_name.endswith(".hlm"):
        return FileType.HLM

    # 2. Magic byte detection
    if analysis[:4] == _PDF_MAGIC:
        return FileType.PDF

    if analysis[:4] == _ZIP_MAGIC:
        # ZIP could be XLSX — check for xl/ path inside
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                names = zf.namelist()
                if any(n.startswith("xl/") or n == "[Content_Types].xml" for n in names):
                    return FileType.EXCEL
        except zipfile.BadZipFile:
            pass

    if analysis[:2] == _GZIP_MAGIC:
        return FileType.HLMZ

    if analysis[:5] == _XML_MAGIC or (analysis[:1] == b"<" and b"</" in analysis[:500]):
        return FileType.XML

    # JSON detection: starts with { or [
    stripped = analysis.lstrip()
    if stripped[:1] in (b"{", b"["):
        return FileType.JSON

    # 3. Extension fallback
    for ext, ftype in _EXTENSION_MAP.items():
        if lower_name.endswith(ext):
            return ftype

    # 4. CSV heuristic: multiple lines with consistent delimiter
    if _looks_like_csv(analysis):
        return FileType.CSV

    raise ValidationError(
        f"Cannot determine file type for '{filename}'",
        details=[{"field": "content", "message": "Unsupported or unrecognizable file format"}],
    )


def _looks_like_csv(content: bytes) -> bool:
    """Heuristic: ≥3 lines with consistent delimiter count."""
    try:
        text = content[:4096].decode("utf-8", errors="replace")
    except Exception:
        return False

    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    if len(lines) < 2:
        return False

    for delim in (",", ";", "\t", "|"):
        counts = [ln.count(delim) for ln in lines[:5]]
        if counts[0] > 0 and len(set(counts)) == 1:
            return True

    return False
