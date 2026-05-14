"""
CSSV1 R9.3 — alarm path when HeartBeat returns ``BEARER_S2S_REMOVED``.

This is a regression detector. After the Phase 0 catchup
(``helium-multitenant-demo#16``, merged 2026-05-09), no Relay→HB call
should EVER ship the dead ``Authorization: Bearer api_key:api_secret``
form. If a code path slips back, HB returns ``401`` with body
``{"error_code": "BEARER_S2S_REMOVED"}``, and Relay must:

1. Increment ``relay_bearer_removed_received_total{endpoint=<context>}``.
2. Emit an ERROR-level log (not WARNING — this is alarm-grade).

The 401 itself still raises :class:`JWTRejectedError`; the alarm is a
side-channel signal so ops sees the offending callsite via
``/metrics`` rather than buried in 401 spam.
"""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from src.clients.heartbeat import HeartBeatClient
from src.clients.introspect import IntrospectClient
from src.errors import HeartBeatUnavailableError, JWTRejectedError
from src.observability import counters


HB_URL = "http://hb.test:9000"
SIGNING_KEY = "0123456789abcdef" * 4


@pytest.fixture(autouse=True)
def _reset_counters():
    counters.reset()
    yield
    counters.reset()


# ── HeartBeatClient path ─────────────────────────────────────────────────


@pytest.fixture
def hb_client():
    return HeartBeatClient(
        heartbeat_api_url=HB_URL,
        timeout=5.0,
        max_attempts=1,
        trace_id="test-trace",
        service_api_key="test-api-key",
        service_api_secret="test-secret",
        service_signing_key=SIGNING_KEY,
    )


class TestHeartBeatBearerRemovedAlarm:

    @respx.mock
    @pytest.mark.asyncio
    async def test_401_with_bearer_removed_increments_counter(self, hb_client):
        respx.post(f"{HB_URL}/api/dedup/check").mock(
            return_value=Response(
                401,
                json={
                    "error_code": "BEARER_S2S_REMOVED",
                    "message": "Bearer api_key:api_secret no longer accepted; "
                    "send HMAC headers per HMAC_S2S_MIGRATION_SPEC §2.",
                },
            )
        )

        with pytest.raises(JWTRejectedError):
            await hb_client.check_duplicate("abc123")

        # Counter incremented with endpoint label set to the context string
        out = list(counters.get_all())
        assert out == [
            (
                "relay_bearer_removed_received_total",
                {"endpoint": "check_duplicate"},
                1,
            )
        ]

    @respx.mock
    @pytest.mark.asyncio
    async def test_401_with_bearer_removed_logs_error(self, hb_client, caplog):
        respx.post(f"{HB_URL}/api/limits/daily/check").mock(
            return_value=Response(
                401, json={"error_code": "BEARER_S2S_REMOVED"}
            )
        )

        with caplog.at_level("ERROR", logger="src.clients.heartbeat"):
            with pytest.raises(JWTRejectedError):
                await hb_client.check_daily_limit("co-1", file_count=1)

        assert any(
            "BEARER_S2S_REMOVED" in rec.message and rec.levelname == "ERROR"
            for rec in caplog.records
        ), "expected an ERROR-level log mentioning BEARER_S2S_REMOVED"

    @respx.mock
    @pytest.mark.asyncio
    async def test_401_with_bearer_removed_still_raises_jwt_rejected(self, hb_client):
        """The exception type is unchanged — alarm is a side-channel signal."""
        respx.post(f"{HB_URL}/api/dedup/check").mock(
            return_value=Response(
                401, json={"error_code": "BEARER_S2S_REMOVED"}
            )
        )

        with pytest.raises(JWTRejectedError) as exc_info:
            await hb_client.check_duplicate("abc")
        assert exc_info.value.status_code == 401

    @respx.mock
    @pytest.mark.asyncio
    async def test_401_other_error_does_not_increment_counter(self, hb_client):
        """A regular HMAC_TIMESTAMP_SKEW 401 must NOT trip the alarm."""
        respx.post(f"{HB_URL}/api/dedup/check").mock(
            return_value=Response(
                401,
                json={
                    "error_code": "HMAC_TIMESTAMP_SKEW",
                    "message": "Timestamp outside 300s window",
                },
            )
        )

        with pytest.raises(JWTRejectedError):
            await hb_client.check_duplicate("abc")

        assert list(counters.get_all()) == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_401_with_no_json_body_does_not_increment(self, hb_client):
        """Plaintext 401 (e.g., from a proxy) is not a BEARER_S2S_REMOVED signal."""
        respx.post(f"{HB_URL}/api/dedup/check").mock(
            return_value=Response(401, text="Unauthorized")
        )

        with pytest.raises(JWTRejectedError):
            await hb_client.check_duplicate("abc")

        assert list(counters.get_all()) == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_multiple_endpoints_separate_counter_rows(self, hb_client):
        """Each (endpoint label) value gets its own counter row."""
        respx.post(f"{HB_URL}/api/dedup/check").mock(
            return_value=Response(
                401, json={"error_code": "BEARER_S2S_REMOVED"}
            )
        )
        respx.post(f"{HB_URL}/api/limits/daily/check").mock(
            return_value=Response(
                401, json={"error_code": "BEARER_S2S_REMOVED"}
            )
        )

        with pytest.raises(JWTRejectedError):
            await hb_client.check_duplicate("abc")
        with pytest.raises(JWTRejectedError):
            await hb_client.check_daily_limit("co-1")
        with pytest.raises(JWTRejectedError):
            await hb_client.check_duplicate("def")

        # Bucket the snapshot by labels for readability
        rows = {
            tuple(sorted(lbls.items())): value
            for _, lbls, value in counters.get_all()
        }
        assert rows[(("endpoint", "check_duplicate"),)] == 2
        assert rows[(("endpoint", "check_daily_limit"),)] == 1


# ── IntrospectClient path ────────────────────────────────────────────────


def _filter_bearer_alarm_rows(snapshot):
    """Strip introspect-cache bookkeeping rows from a counters snapshot.

    The IntrospectClient also increments ``relay_introspect_cache_total``
    (per CSSV1 S1 chip 2/2, PR #18) on every call — including a ``no_jti``
    branch hit by these tests' synthetic ``eyJ.eyJ.SIG`` tokens. That
    counter is orthogonal to the bearer_removed alarm under test; filter
    it out so the assertions stay focused on alarm behaviour rather than
    incidental cache-counter rows.
    """
    return [
        row for row in snapshot
        if row[0] != "relay_introspect_cache_total"
    ]


class TestIntrospectBearerRemovedAlarm:

    @respx.mock
    @pytest.mark.asyncio
    async def test_401_bearer_removed_increments_introspect_counter(self):
        respx.post(f"{HB_URL}/api/auth/introspect").mock(
            return_value=Response(
                401, json={"error_code": "BEARER_S2S_REMOVED"}
            )
        )

        client = IntrospectClient(
            heartbeat_url=HB_URL,
            service_api_key="api-key",
            service_signing_key=SIGNING_KEY,
            timeout_s=5.0,
        )

        with pytest.raises(HeartBeatUnavailableError):
            await client.introspect("eyJ.eyJ.SIG", trace_id="t")
        await client.close()

        # Filter out the introspect-cache bookkeeping rows (PR #18) so
        # the assertion pins the alarm behaviour only.
        out = _filter_bearer_alarm_rows(list(counters.get_all()))
        assert out == [
            (
                "relay_bearer_removed_received_total",
                {"endpoint": "introspect"},
                1,
            )
        ]

    @respx.mock
    @pytest.mark.asyncio
    async def test_401_other_error_does_not_increment_introspect_counter(self):
        """HMAC skew 401 (genuine config error) must not trip the alarm."""
        respx.post(f"{HB_URL}/api/auth/introspect").mock(
            return_value=Response(
                401, json={"error_code": "HMAC_TIMESTAMP_SKEW"}
            )
        )

        client = IntrospectClient(
            heartbeat_url=HB_URL,
            service_api_key="api-key",
            service_signing_key=SIGNING_KEY,
            timeout_s=5.0,
        )

        with pytest.raises(HeartBeatUnavailableError):
            await client.introspect("eyJ.eyJ.SIG")
        await client.close()

        assert _filter_bearer_alarm_rows(list(counters.get_all())) == []
