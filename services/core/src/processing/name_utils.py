"""
Name normalization and fuzzy matching utilities.

Per DEC-WS2-007: Deterministic normalization (uppercase + suffix strip + whitespace collapse).
Per DEC-WS2-012: python-Levenshtein C extension with pure-Python fallback.
Per DEC-WS2-001: Fuzzy match threshold 0.85.
"""

from __future__ import annotations

import re

# Legal suffixes to strip during normalization
_LEGAL_SUFFIXES = re.compile(
    r"\s+(LTD|LIMITED|PLC|INC|INCORPORATED|LLC|CORP|CORPORATION"
    r"|ENTERPRISES?|INTL?|INTERNATIONAL"
    r"|NIG|NIGERIA|NIJA)\.?\s*$",
    re.IGNORECASE,
)

# Try python-Levenshtein first (C extension, fast), fall back to difflib
try:
    from Levenshtein import ratio as _levenshtein_ratio

    _HAS_C_LEVENSHTEIN = True
except ImportError:
    from difflib import SequenceMatcher

    _HAS_C_LEVENSHTEIN = False

    def _levenshtein_ratio(s1: str, s2: str) -> float:
        """Pure-Python fallback using difflib.SequenceMatcher."""
        return SequenceMatcher(None, s1, s2).ratio()


def normalize_name(name: str) -> str:
    """
    Normalize a company or product name for fuzzy matching.

    Steps:
        1. Uppercase
        2. Replace & with AND
        3. Strip legal suffixes (LTD, PLC, etc.)
        4. Remove punctuation except hyphens
        5. Collapse whitespace
        6. Strip

    Args:
        name: Raw name string.

    Returns:
        Normalized name string.
    """
    if not name:
        return ""
    result = name.upper()
    result = result.replace("&", "AND")
    # Strip trailing suffixes repeatedly (e.g., "Nigeria Limited" → strip "Limited" → strip "Nigeria")
    prev = None
    while prev != result:
        prev = result
        result = _LEGAL_SUFFIXES.sub("", result)
    result = re.sub(r"[^\w\s\-]", "", result)
    result = re.sub(r"\s+", " ", result).strip()
    return result


def levenshtein_ratio(s1: str, s2: str) -> float:
    """
    Calculate similarity ratio between two strings.

    Uses python-Levenshtein C extension if available, otherwise difflib.

    Args:
        s1: First string (should be normalized).
        s2: Second string (should be normalized).

    Returns:
        Float between 0.0 (completely different) and 1.0 (identical).
    """
    if not s1 or not s2:
        return 0.0
    if s1 == s2:
        return 1.0
    return _levenshtein_ratio(s1, s2)


def has_c_levenshtein() -> bool:
    """Return True if the fast C Levenshtein library is available."""
    return _HAS_C_LEVENSHTEIN
