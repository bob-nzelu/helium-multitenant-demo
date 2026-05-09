"""
Unit tests for ``services/relay/src/clients/_s2s_hmac.py``.

Pin the wire format (HMAC_S2S_MIGRATION_SPEC.md §2.2) so HB-side and
Relay-side stay byte-identical:

    signing_input = f"{METHOD}\\n{path}\\n{timestamp}\\n{nonce}\\n{body_sha256}".encode("utf-8")

Every assertion below derives from the spec or from the helper's
public contract (RELAY_NEXT_STEPS_NOTE §1.4) — drift here means HB
will reject our requests with 403 HMAC_SIGNATURE_INVALID.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any
from unittest.mock import patch

import pytest

from src.clients._s2s_hmac import build_s2s_hmac_headers


# ── Fixtures ──────────────────────────────────────────────────────────────


SIGNING_KEY = "0123456789abcdef" * 4  # 64-hex chars (32 bytes)
API_KEY = "rl_test_relay001"


def _decode_input(method: str, path: str, ts: str, nonce: str, body: bytes) -> bytes:
    body_sha256 = hashlib.sha256(body).hexdigest()
    return f"{method.upper()}\n{path}\n{ts}\n{nonce}\n{body_sha256}".encode("utf-8")


def _expected_signature(method: str, path: str, ts: str, nonce: str, body: bytes) -> str:
    inp = _decode_input(method, path, ts, nonce, body)
    return hmac.new(
        SIGNING_KEY.encode("utf-8"), inp, hashlib.sha256
    ).hexdigest()


# ── Header shape ──────────────────────────────────────────────────────────


def test_returns_four_canonical_headers() -> None:
    headers = build_s2s_hmac_headers(
        method="POST",
        path="/api/dedup/check",
        body_bytes=b'{"file_hash":"abc"}',
        api_key=API_KEY,
        signing_key=SIGNING_KEY,
    )

    assert set(headers.keys()) == {
        "X-API-Key",
        "X-Timestamp",
        "X-Nonce",
        "X-Signature",
    }
    assert headers["X-API-Key"] == API_KEY


def test_timestamp_is_unix_epoch_seconds_ascii_integer() -> None:
    headers = build_s2s_hmac_headers(
        method="POST",
        path="/x",
        body_bytes=b"",
        api_key=API_KEY,
        signing_key=SIGNING_KEY,
    )
    # int() must succeed and value must be plausible (well past 2020-01-01)
    ts = int(headers["X-Timestamp"])
    assert ts > 1577836800  # 2020-01-01T00:00:00Z


def test_nonce_is_uuid4_with_dashes() -> None:
    headers = build_s2s_hmac_headers(
        method="POST",
        path="/x",
        body_bytes=b"",
        api_key=API_KEY,
        signing_key=SIGNING_KEY,
    )
    # uuid.uuid4() format: 8-4-4-4-12 hex chars
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        headers["X-Nonce"],
    ), f"unexpected nonce shape: {headers['X-Nonce']!r}"


def test_signature_is_lowercase_hex_64_chars() -> None:
    headers = build_s2s_hmac_headers(
        method="POST",
        path="/x",
        body_bytes=b"",
        api_key=API_KEY,
        signing_key=SIGNING_KEY,
    )
    sig = headers["X-Signature"]
    assert re.fullmatch(r"[0-9a-f]{64}", sig), (
        f"signature must be 64-char lowercase hex with no prefix; got {sig!r}"
    )
    # No upper-case hex
    assert sig == sig.lower()
    # No "0x" prefix and no "sha256=" prefix
    assert not sig.startswith("0x")
    assert not sig.startswith("sha256=")


# ── Signing input format ──────────────────────────────────────────────────


def test_signature_matches_pinned_signing_input_format() -> None:
    """Spec §2.2: signing_input = f"{METHOD}\\n{path}\\n{ts}\\n{nonce}\\n{body_sha256}"."""
    body = b'{"file_hash":"deadbeef"}'

    # Patch time + uuid so we know the exact ts/nonce written into the
    # signing input (and the resulting signature is deterministic).
    with (
        patch("src.clients._s2s_hmac.time.time", return_value=1715255100.5),
        patch(
            "src.clients._s2s_hmac.uuid.uuid4",
            return_value=_FixedUuid("550e8400-e29b-41d4-a716-446655440000"),
        ),
    ):
        headers = build_s2s_hmac_headers(
            method="POST",
            path="/api/dedup/check",
            body_bytes=body,
            api_key=API_KEY,
            signing_key=SIGNING_KEY,
        )

    assert headers["X-Timestamp"] == "1715255100"  # int(time.time())
    assert headers["X-Nonce"] == "550e8400-e29b-41d4-a716-446655440000"
    assert headers["X-Signature"] == _expected_signature(
        "POST",
        "/api/dedup/check",
        "1715255100",
        "550e8400-e29b-41d4-a716-446655440000",
        body,
    )


def test_method_upper_cased_in_signing_input() -> None:
    """Lowercase method input must be upper-cased in the signing string."""
    with (
        patch("src.clients._s2s_hmac.time.time", return_value=1715255100.0),
        patch(
            "src.clients._s2s_hmac.uuid.uuid4",
            return_value=_FixedUuid("00000000-0000-0000-0000-000000000000"),
        ),
    ):
        headers_lower = build_s2s_hmac_headers(
            method="post",
            path="/x",
            body_bytes=b"",
            api_key=API_KEY,
            signing_key=SIGNING_KEY,
        )
        headers_upper = build_s2s_hmac_headers(
            method="POST",
            path="/x",
            body_bytes=b"",
            api_key=API_KEY,
            signing_key=SIGNING_KEY,
        )

    assert headers_lower["X-Signature"] == headers_upper["X-Signature"]


# ── Body discipline ──────────────────────────────────────────────────────


def test_empty_body_uses_sha256_of_empty_bytes_not_empty_string() -> None:
    """Spec §2.2: empty body → ``hashlib.sha256(b"").hexdigest()``,
    NOT the SHA of the literal string ``""``. This is the canonical
    edge case noted in RELAY_NEXT_STEPS_NOTE §4.3."""
    expected_empty = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert hashlib.sha256(b"").hexdigest() == expected_empty

    with (
        patch("src.clients._s2s_hmac.time.time", return_value=1715255100.0),
        patch(
            "src.clients._s2s_hmac.uuid.uuid4",
            return_value=_FixedUuid("00000000-0000-0000-0000-000000000000"),
        ),
    ):
        headers = build_s2s_hmac_headers(
            method="POST",
            path="/api/v1/heartbeat/config",
            body_bytes=b"",
            api_key=API_KEY,
            signing_key=SIGNING_KEY,
        )

    # The signing input baked into the signature must include the empty-
    # body sha256; recompute and compare.
    expected_sig = hmac.new(
        SIGNING_KEY.encode("utf-8"),
        (
            "POST\n/api/v1/heartbeat/config\n"
            "1715255100\n"
            "00000000-0000-0000-0000-000000000000\n"
            f"{expected_empty}"
        ).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert headers["X-Signature"] == expected_sig


def test_byte_identical_bodies_produce_byte_identical_body_sha() -> None:
    """The caller passes EXACT wire bytes; same bytes = same signature
    (modulo timestamp + nonce). Verifies body-bytes discipline."""
    body = b'{"a":1,"b":[2,3]}'

    with (
        patch("src.clients._s2s_hmac.time.time", return_value=1715255100.0),
        patch(
            "src.clients._s2s_hmac.uuid.uuid4",
            return_value=_FixedUuid("11111111-1111-1111-1111-111111111111"),
        ),
    ):
        h1 = build_s2s_hmac_headers(
            method="POST",
            path="/x",
            body_bytes=body,
            api_key=API_KEY,
            signing_key=SIGNING_KEY,
        )
        h2 = build_s2s_hmac_headers(
            method="POST",
            path="/x",
            body_bytes=body,
            api_key=API_KEY,
            signing_key=SIGNING_KEY,
        )

    assert h1["X-Signature"] == h2["X-Signature"]


def test_different_path_changes_signature() -> None:
    body = b""
    with (
        patch("src.clients._s2s_hmac.time.time", return_value=1715255100.0),
        patch(
            "src.clients._s2s_hmac.uuid.uuid4",
            return_value=_FixedUuid("11111111-1111-1111-1111-111111111111"),
        ),
    ):
        h_a = build_s2s_hmac_headers(
            method="POST",
            path="/api/dedup/check",
            body_bytes=body,
            api_key=API_KEY,
            signing_key=SIGNING_KEY,
        )
        h_b = build_s2s_hmac_headers(
            method="POST",
            path="/api/limits/daily/check",
            body_bytes=body,
            api_key=API_KEY,
            signing_key=SIGNING_KEY,
        )
    assert h_a["X-Signature"] != h_b["X-Signature"]


def test_different_signing_key_changes_signature() -> None:
    body = b""
    with (
        patch("src.clients._s2s_hmac.time.time", return_value=1715255100.0),
        patch(
            "src.clients._s2s_hmac.uuid.uuid4",
            return_value=_FixedUuid("11111111-1111-1111-1111-111111111111"),
        ),
    ):
        h_a = build_s2s_hmac_headers(
            method="POST",
            path="/x",
            body_bytes=body,
            api_key=API_KEY,
            signing_key=SIGNING_KEY,
        )
        h_b = build_s2s_hmac_headers(
            method="POST",
            path="/x",
            body_bytes=body,
            api_key=API_KEY,
            signing_key="different" * 8,  # 64 chars
        )
    assert h_a["X-Signature"] != h_b["X-Signature"]


# ── Misconfiguration ─────────────────────────────────────────────────────


def test_empty_signing_key_raises_value_error() -> None:
    with pytest.raises(ValueError, match="RELAY_S2S_SIGNING_KEY"):
        build_s2s_hmac_headers(
            method="POST",
            path="/x",
            body_bytes=b"",
            api_key=API_KEY,
            signing_key="",
        )


def test_empty_api_key_raises_value_error() -> None:
    with pytest.raises(ValueError, match="api_key is empty"):
        build_s2s_hmac_headers(
            method="POST",
            path="/x",
            body_bytes=b"",
            api_key="",
            signing_key=SIGNING_KEY,
        )


# ── Helpers ──────────────────────────────────────────────────────────────


class _FixedUuid:
    """Stand-in for ``uuid.uuid4()`` whose ``str()`` returns a fixed value.

    The helper does ``str(uuid.uuid4())``, so this only needs to override
    ``__str__``.
    """

    def __init__(self, value: str) -> None:
        self._value = value

    def __str__(self) -> str:  # noqa: D401
        return self._value
