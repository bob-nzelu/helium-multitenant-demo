"""Pydantic-compatible type aliases for hash strings. See HASHING_CONTRACT.md §2.1.

This module imports pydantic lazily so callers without pydantic installed
can still use the algorithm primitives. Install with `helium-hash[pydantic]`
to get the Sha256Hex Annotated type.
"""

from __future__ import annotations

from typing import Annotated

try:
    from pydantic import StringConstraints
except ImportError as exc:  # pragma: no cover - exercised only without extra
    raise ImportError(
        "Sha256Hex requires pydantic. Install with: pip install helium-hash[pydantic]"
    ) from exc


Sha256Hex = Annotated[
    str,
    StringConstraints(
        pattern=r"^[0-9a-f]{64}$",
        min_length=64,
        max_length=64,
    ),
]
"""A 64-character lowercase hex SHA-256 digest.

Use in Pydantic models that pass hashes (e.g. ArtifactManifestRequest)
so uppercase / wrong-length / non-hex strings fail validation at the API
boundary rather than in handler code.
"""

__all__ = ["Sha256Hex"]
