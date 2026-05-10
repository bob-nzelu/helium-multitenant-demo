"""
Tests for ``src.observability.startup_checks`` (CSSV1 R9.1 + R9.2).

R9.1 — ``validate_signing_key_shape`` must:
- accept exactly 64 lowercase-hex chars
- pass empty silently (degraded dev mode, WARNING-only)
- reject everything else with :class:`ConfigError`

R9.2 — ``check_clock_skew_against_heartbeat`` must:
- return the absolute skew when both sides reply with parseable Date
- raise :class:`ConfigError` when skew exceeds the bail threshold
- return ``None`` (warn-and-continue) on HB unreachable / no Date /
  unparseable Date
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
import respx
from httpx import Response

from src.errors import ConfigError
from src.observability.startup_checks import (
    check_clock_skew_against_heartbeat,
    validate_signing_key_shape,
)


HB_URL = "http://hb.test:9000"


# ── R9.1 — signing-key shape ─────────────────────────────────────────────


class TestValidateSigningKeyShape:

    def test_valid_64_lowercase_hex_passes(self):
        # secrets.token_hex(32) shape — what HB actually generates
        validate_signing_key_shape("0123456789abcdef" * 4)

    def test_empty_string_passes_with_warning(self, caplog):
        """Empty key is the degraded dev-mode signal — WARN, don't bail."""
        with caplog.at_level("WARNING", logger="src.observability.startup_checks"):
            validate_signing_key_shape("")
        assert any(
            "DEGRADED" in rec.message for rec in caplog.records
        ), "expected a WARNING about degraded dev mode"

    def test_uppercase_hex_rejected(self):
        with pytest.raises(ConfigError):
            validate_signing_key_shape("ABCDEF" + "0" * 58)

    def test_short_key_rejected(self):
        with pytest.raises(ConfigError):
            validate_signing_key_shape("abc123")

    def test_long_key_rejected(self):
        with pytest.raises(ConfigError):
            validate_signing_key_shape("0" * 65)

    def test_non_hex_chars_rejected(self):
        # 64 chars but contains 'g'
        with pytest.raises(ConfigError):
            validate_signing_key_shape("g" + "0" * 63)

    def test_leading_whitespace_rejected(self):
        with pytest.raises(ConfigError):
            validate_signing_key_shape(" " + "0" * 64)

    def test_prefix_rejected(self):
        """``hex:0123…`` shape (an operator might paste with a prefix)."""
        with pytest.raises(ConfigError):
            validate_signing_key_shape("hex:" + "0" * 64)

    def test_error_message_does_not_leak_key(self):
        """ConfigError message must not include the bogus key value."""
        bad = "supersecretvaluedontprintme" + "0" * 37
        with pytest.raises(ConfigError) as exc_info:
            validate_signing_key_shape(bad)
        assert "supersecretvaluedontprintme" not in str(exc_info.value.message)


# ── R9.2 — NTP discipline ────────────────────────────────────────────────


def _date_header(dt: datetime) -> str:
    """RFC 7231 Date format (e.g., 'Wed, 21 Oct 2026 07:28:00 GMT')."""
    return format_datetime(dt, usegmt=True)


class TestCheckClockSkewSuccess:

    @respx.mock
    @pytest.mark.asyncio
    async def test_in_sync_returns_small_skew(self):
        now = datetime.now(timezone.utc)
        respx.get(f"{HB_URL}/health").mock(
            return_value=Response(200, headers={"Date": _date_header(now)})
        )

        skew = await check_clock_skew_against_heartbeat(HB_URL)

        assert skew is not None
        assert skew < 5.0  # well within the 60s threshold

    @respx.mock
    @pytest.mark.asyncio
    async def test_skew_just_under_threshold_passes(self):
        # 30 seconds off — under the 60s default
        hb_time = datetime.now(timezone.utc) - timedelta(seconds=30)
        respx.get(f"{HB_URL}/health").mock(
            return_value=Response(200, headers={"Date": _date_header(hb_time)})
        )

        skew = await check_clock_skew_against_heartbeat(HB_URL)
        assert skew is not None
        assert 25 < skew < 35


class TestCheckClockSkewBail:

    @respx.mock
    @pytest.mark.asyncio
    async def test_skew_over_threshold_raises_config_error(self):
        # 5 minutes off — way over the 60s default
        hb_time = datetime.now(timezone.utc) - timedelta(seconds=300)
        respx.get(f"{HB_URL}/health").mock(
            return_value=Response(200, headers={"Date": _date_header(hb_time)})
        )

        with pytest.raises(ConfigError) as exc_info:
            await check_clock_skew_against_heartbeat(HB_URL)
        assert "Clock skew" in exc_info.value.message
        assert "300" in exc_info.value.message or "299" in exc_info.value.message

    @respx.mock
    @pytest.mark.asyncio
    async def test_future_skew_also_raises(self):
        """Skew is symmetric — 90 s in the future is just as bad as 90 s in the past."""
        hb_time = datetime.now(timezone.utc) + timedelta(seconds=90)
        respx.get(f"{HB_URL}/health").mock(
            return_value=Response(200, headers={"Date": _date_header(hb_time)})
        )

        with pytest.raises(ConfigError):
            await check_clock_skew_against_heartbeat(HB_URL)

    @respx.mock
    @pytest.mark.asyncio
    async def test_custom_bail_threshold_respected(self):
        hb_time = datetime.now(timezone.utc) - timedelta(seconds=20)
        respx.get(f"{HB_URL}/health").mock(
            return_value=Response(200, headers={"Date": _date_header(hb_time)})
        )

        # Tight 10s threshold — 20s skew should bail
        with pytest.raises(ConfigError):
            await check_clock_skew_against_heartbeat(
                HB_URL, bail_threshold_s=10.0
            )


class TestCheckClockSkewSoftFail:
    """When the check can't run, return None + log WARNING — never bail."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_hb_unreachable_returns_none(self):
        respx.get(f"{HB_URL}/health").mock(
            side_effect=__import__("httpx").ConnectError("nope")
        )

        skew = await check_clock_skew_against_heartbeat(HB_URL)
        assert skew is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_missing_date_header_returns_none(self):
        respx.get(f"{HB_URL}/health").mock(
            return_value=Response(200, headers={})
        )

        skew = await check_clock_skew_against_heartbeat(HB_URL)
        assert skew is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_unparseable_date_returns_none(self):
        respx.get(f"{HB_URL}/health").mock(
            return_value=Response(
                200, headers={"Date": "not-a-date"}
            )
        )

        skew = await check_clock_skew_against_heartbeat(HB_URL)
        assert skew is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        respx.get(f"{HB_URL}/health").mock(
            side_effect=__import__("httpx").TimeoutException("slow")
        )

        skew = await check_clock_skew_against_heartbeat(HB_URL)
        assert skew is None
