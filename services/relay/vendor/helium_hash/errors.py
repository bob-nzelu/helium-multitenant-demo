"""Exception hierarchy for helium_hash. See HASHING_CONTRACT.md §2.2."""

from __future__ import annotations


class HashError(Exception):
    """Base for all helium_hash errors."""


class InvalidHashFormat(HashError, ValueError):
    """Hash string doesn't match expected lowercase-hex-64 shape."""


class EmptyBatchError(HashError, ValueError):
    """sha256_batch called with no inputs."""
