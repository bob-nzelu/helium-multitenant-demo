"""
File Validation

Cherry-picked from old_src/bulk/validation.py (lines 167-279).
Validates file count, extensions, and sizes.

Separated from auth (core/auth.py) for single-responsibility.
All validations are synchronous and fail-fast.
"""

import logging
from pathlib import Path
from typing import List, Tuple

from ..config import RelayConfig
from ..errors import (
    FileSizeExceededError,
    InvalidFileExtensionError,
    NoFilesProvidedError,
    TooManyFilesError,
    TotalSizeExceededError,
)

logger = logging.getLogger(__name__)


def validate_file_count(count: int, max_files: int) -> None:
    """
    Validate file count is within limits.

    Args:
        count: Number of files in request.
        max_files: Maximum allowed files per request.

    Raises:
        NoFilesProvidedError: If count is 0.
        TooManyFilesError: If count exceeds limit.
    """
    if count == 0:
        raise NoFilesProvidedError()

    if count > max_files:
        raise TooManyFilesError(count=count, limit=max_files)

    logger.debug(f"File count OK: {count}/{max_files}")


def validate_file_extensions(
    files: List[Tuple[str, bytes]],
    allowed_extensions: Tuple[str, ...],
) -> None:
    """
    Validate all file extensions are in the allowed list.

    Checks every file and raises on the FIRST invalid one (fail-fast).

    Args:
        files: List of (filename, file_data) tuples.
        allowed_extensions: Tuple of allowed extensions (e.g., (".pdf", ".xml")).

    Raises:
        InvalidFileExtensionError: If any file has a disallowed extension.
    """
    allowed_lower = tuple(ext.lower() for ext in allowed_extensions)

    for filename, _ in files:
        ext = Path(filename).suffix.lower()
        if ext not in allowed_lower:
            raise InvalidFileExtensionError(
                filename=filename,
                allowed=list(allowed_lower),
            )

    logger.debug(
        f"File extensions OK: {[Path(f).suffix for f, _ in files]}"
    )


def validate_file_sizes(
    files: List[Tuple[str, bytes]],
    max_file_size_mb: float,
    max_total_size_mb: float,
) -> None:
    """
    Validate individual and total file sizes.

    Checks every file individually, then total. Fail-fast on first violation.

    Args:
        files: List of (filename, file_data) tuples.
        max_file_size_mb: Maximum size per file in MB.
        max_total_size_mb: Maximum total upload size in MB.

    Raises:
        FileSizeExceededError: If any individual file is too large.
        TotalSizeExceededError: If total exceeds limit.
    """
    total_bytes = 0

    for filename, file_data in files:
        size_bytes = len(file_data)
        size_mb = size_bytes / (1024 * 1024)
        total_bytes += size_bytes

        if size_mb > max_file_size_mb:
            raise FileSizeExceededError(
                filename=filename,
                size_mb=size_mb,
                limit_mb=max_file_size_mb,
            )

    total_mb = total_bytes / (1024 * 1024)
    if total_mb > max_total_size_mb:
        raise TotalSizeExceededError(
            total_mb=total_mb,
            limit_mb=max_total_size_mb,
        )

    logger.debug(f"File sizes OK: total={total_mb:.2f} MB")


def validate_files(
    files: List[Tuple[str, bytes]],
    config: RelayConfig,
) -> None:
    """
    Run all file validations in sequence.

    Convenience function that calls count → extensions → sizes.

    Args:
        files: List of (filename, file_data) tuples.
        config: RelayConfig with file limit settings.

    Raises:
        ValidationFailedError (or subclass) on any failure.
    """
    validate_file_count(len(files), config.max_files)
    validate_file_extensions(files, config.allowed_extensions)
    validate_file_sizes(files, config.max_file_size_mb, config.max_total_size_mb)

    logger.info(f"All file validations passed — {len(files)} files")
