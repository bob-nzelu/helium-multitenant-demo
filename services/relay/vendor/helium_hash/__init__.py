"""Canonical SHA-256 hashing primitives for the Helium platform.

Spec: HeartBeat/Documentation/HASHING_CONTRACT.md
"""

from helium_hash.algorithms import (
    sha256_batch,
    sha256_file,
    sha256_hlx,
    sha256_stream,
)
from helium_hash.errors import (
    EmptyBatchError,
    HashError,
    InvalidHashFormat,
)

__all__ = [
    "sha256_file",
    "sha256_batch",
    "sha256_hlx",
    "sha256_stream",
    "HashError",
    "InvalidHashFormat",
    "EmptyBatchError",
]
__version__ = "1.0.0"
