"""SHA-256 primitives per HASHING_CONTRACT.md §1 + §4.

Algorithm parameters are bit-locked: changing them is a major-version bump
that forces re-hashing the entire blob store.
"""

from __future__ import annotations

import hashlib
import os
from typing import BinaryIO, Union

from helium_hash.errors import EmptyBatchError, InvalidHashFormat

_HEX64_CHARS = frozenset("0123456789abcdef")
_DEFAULT_CHUNK = 65536  # 64 KiB — matches legacy Float upload_manager.py:1282

FileSource = Union[str, "os.PathLike[str]", bytes, bytearray]


def _is_hex64_lower(s: str) -> bool:
    if len(s) != 64:
        return False
    return all(c in _HEX64_CHARS for c in s)


def sha256_file(source: FileSource) -> str:
    """SHA-256 of body bytes. Returns 64-char lowercase hex.

    Filename is not part of the digest — rename-safe.
    Paths read in 64 KiB chunks; bytes-like inputs hash directly.
    """
    h = hashlib.sha256()
    if isinstance(source, (bytes, bytearray)):
        h.update(source)
    else:
        with open(source, "rb") as f:
            while chunk := f.read(_DEFAULT_CHUNK):
                h.update(chunk)
    return h.hexdigest()


def sha256_stream(stream: BinaryIO, chunk_size: int = _DEFAULT_CHUNK) -> str:
    """Hash an open binary stream. Caller owns the stream lifecycle.

    Use when a file handle already exists (e.g. multipart parsers) and you
    don't want to re-open the underlying file.
    """
    h = hashlib.sha256()
    while chunk := stream.read(chunk_size):
        h.update(chunk)
    return h.hexdigest()


def sha256_batch(file_hashes: list[str]) -> str:
    """Sort lex, concat as ASCII bytes (no separator), SHA-256 the result.

    Raises EmptyBatchError on empty input, InvalidHashFormat on any non-conforming entry.
    """
    if not file_hashes:
        raise EmptyBatchError("sha256_batch requires at least one file hash")
    for fh in file_hashes:
        if not isinstance(fh, str) or not _is_hex64_lower(fh):
            raise InvalidHashFormat(
                f"expected 64-char lowercase hex, got {fh!r}"
            )
    sorted_hashes = sorted(file_hashes)
    concat_bytes = "".join(sorted_hashes).encode("ascii")
    return hashlib.sha256(concat_bytes).hexdigest()


def sha256_hlx(canonical_bytes: bytes) -> str:
    """SHA-256 of canonical-serialised HLX bytes.

    NOTE: HLX canonical-form definition is OPEN — owned by the Reader team.
    Until HLX_CANONICAL_FORM.md is published, callers MUST NOT compute or
    compare HLX hashes for semantically-meaningful equivalence — risk of
    divergent canonicalization producing different hashes for the same HLX.
    """
    return hashlib.sha256(canonical_bytes).hexdigest()
