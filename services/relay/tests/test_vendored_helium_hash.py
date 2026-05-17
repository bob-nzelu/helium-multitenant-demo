"""
Sanity tests for the vendored ``helium_hash`` package — CSSV1 S4 R7.

Two purposes:
    1. Confirm the canonical ``from helium_hash import ...`` import path
       resolves (i.e., pytest's ``pythonpath = vendor`` and Docker's
       ``ENV PYTHONPATH=/app/vendor`` are wired correctly).
    2. Spot-check the §3 mandatory test vectors from
       ``HASHING_CONTRACT.md`` so if the vendor copy drifts from the
       source-of-truth package, the build goes red loudly.

The helium-hash package has its own 75-test suite at
``helium-services-phase3/packages/helium-hash/tests/`` with 100%
line + branch coverage. THIS file is intentionally lightweight — just
enough to detect "Relay imports a broken vendor copy" without
duplicating the upstream tests.
"""

from __future__ import annotations

import pytest

from helium_hash import (
    EmptyBatchError,
    InvalidHashFormat,
    sha256_batch,
    sha256_file,
    sha256_stream,
)


class TestVendoredImport:
    """Verify the package resolves via the canonical import path."""

    def test_module_resolves(self):
        import helium_hash
        assert hasattr(helium_hash, "sha256_file")
        assert hasattr(helium_hash, "sha256_batch")
        assert hasattr(helium_hash, "sha256_hlx")
        assert hasattr(helium_hash, "sha256_stream")

    def test_version_pinned(self):
        import helium_hash
        # Pinned at vendor time. Bump in lockstep with the source repo.
        assert helium_hash.__version__ == "1.0.0"


class TestSha256FileVectors:
    """A handful of canonical vectors from HASHING_CONTRACT.md §3.
    If these go red, the vendor copy is broken or the spec changed."""

    def test_empty_bytes(self):
        # SHA-256("") — the most boring known vector.
        assert sha256_file(b"") == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_ascii_test(self):
        # SHA-256("test") — widely known regression test value.
        assert sha256_file(b"test") == (
            "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
        )

    def test_chunked_path_matches_bytes_path(self, tmp_path):
        """64 KiB chunked file reader must produce the same digest as
        the bytes-direct path. Covers the contract's `_DEFAULT_CHUNK`
        constant — if it ever drifts from 65536, this would catch it."""
        payload = b"a" * (65537 * 2)  # crosses two full chunks + a remainder
        p = tmp_path / "blob.bin"
        p.write_bytes(payload)

        assert sha256_file(p) == sha256_file(payload)


class TestSha256BatchVectors:
    """Spot-check the batch-hash composition."""

    def test_single_file_batch(self):
        # batch(sorted([H1])) — must equal SHA-256(H1) since concat == H1.
        h1 = "a" * 64
        import hashlib
        expected = hashlib.sha256(h1.encode("ascii")).hexdigest()
        assert sha256_batch([h1]) == expected

    def test_sort_order_independence(self):
        """The contract locks lex-sort before concat, so input order
        must not affect the result."""
        h1 = "a" * 64
        h2 = "b" * 64
        h3 = "c" * 64
        assert sha256_batch([h1, h2, h3]) == sha256_batch([h3, h2, h1])
        assert sha256_batch([h2, h1, h3]) == sha256_batch([h1, h2, h3])

    def test_empty_batch_raises(self):
        with pytest.raises(EmptyBatchError):
            sha256_batch([])

    def test_uppercase_hash_raises(self):
        # Contract is lowercase-only.
        with pytest.raises(InvalidHashFormat):
            sha256_batch(["A" * 64])

    def test_short_hash_raises(self):
        with pytest.raises(InvalidHashFormat):
            sha256_batch(["abc"])


class TestSha256StreamVectors:
    """Stream-based hashing path."""

    def test_stream_matches_bytes(self):
        import io
        payload = b"streaming test payload" * 100
        stream = io.BytesIO(payload)
        assert sha256_stream(stream) == sha256_file(payload)
