"""
Tests for the JWT introspect cache (CSSV1 S1 chip 2/2).

Covers ``IntrospectClient`` cache semantics added in
``feat/relay-cssv1-s1-introspect-cache``:

- positive outcomes cached for 30s, keyed on JWT ``jti``
- negative outcomes cached the same way (anti-spam)
- ``bypass_cache=True`` skips read AND write
- tokens lacking ``jti`` fall through to HB every time
- LRU eviction at ``maxsize``
- counter wiring: ``relay_introspect_cache_total{result}`` for every
  observable outcome (hit / miss / bypass / no_jti)

Pattern matches ``test_introspect.py``: ``respx`` for HB mocking,
``IntrospectClient`` constructed directly per test.
"""

from __future__ import annotations

import base64
import json

import pytest
import respx
from httpx import Response

from src.clients.introspect import (
    INTROSPECT_CACHE_MAXSIZE,
    INTROSPECT_CACHE_TTL_S,
    IntrospectClient,
)
from src.errors import JWTRejectedError
from src.observability import counters


HEARTBEAT_URL = "http://localhost:9000"
SIGNING_KEY = "0123456789abcdef" * 4  # 64-hex test key
API_KEY = "rl_test_relay001"


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_jwt(claims: dict) -> str:
    """Build an unsigned compact-JWS-shaped token with the given payload claims.

    Signature segment is intentionally bogus — HB does the actual
    signature verification, Relay only base64url-decodes the payload
    to extract ``jti``. This matches how synthetic tokens look in
    production tests of HB-mocked code paths.
    """
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode("ascii")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8")).rstrip(b"=").decode("ascii")
    return f"{header}.{payload}.signature_placeholder"


def _active_response_body(jti: str = "any") -> dict:
    """Mirror HB's introspect 200 response for ``active=true``.

    ``jti`` here is informational — the cache key comes from the JWT,
    not the response body.
    """
    return {
        "active": True,
        "user_id": f"user-{jti}",
        "tenant_id": "tenant-test",
        "role": "Operator",
        "permissions": ["blob.upload"],
        "actor_type": "human",
        "device_id": "dev-1",
        "last_auth_at": None,
        "expires_at": "2030-01-01T00:00:00Z",
        "session_expires_at": "2030-01-02T00:00:00Z",
        "step_up_satisfied": True,
    }


def _inactive_response_body(error_code: str = "TOKEN_EXPIRED", message: str = "JWT exp claim in the past") -> dict:
    """Mirror HB's introspect 200 response for ``active=false``."""
    return {
        "active": False,
        "error_code": error_code,
        "message": message,
        "user_id": None,
        "tenant_id": None,
        "role": None,
        "permissions": [],
        "actor_type": None,
        "device_id": None,
        "last_auth_at": None,
        "expires_at": None,
        "session_expires_at": None,
        "step_up_satisfied": None,
    }


def _counter_value(name: str, **labels: str) -> int:
    """Return the integer value of a counter (or 0 if not yet incremented)."""
    for cname, clabels, value in counters.get_all():
        if cname == name and clabels == labels:
            return value
    return 0


@pytest.fixture
def client():
    return IntrospectClient(
        heartbeat_url=HEARTBEAT_URL,
        service_api_key=API_KEY,
        service_api_secret="legacy-bcrypt-secret-unused",
        service_signing_key=SIGNING_KEY,
        timeout_s=5.0,
    )


@pytest.fixture(autouse=True)
def _reset_counters():
    """Each test starts with a clean counter dict so we can read
    ``relay_introspect_cache_total`` deltas in isolation."""
    counters.reset()
    yield
    counters.reset()


# ── jti extraction ───────────────────────────────────────────────────────


class TestGetJti:
    """The static ``_get_jti`` helper — pure function, no HB dependency."""

    def test_extracts_jti_from_well_formed_token(self):
        token = _make_jwt({"jti": "abc-123", "sub": "user-1"})
        assert IntrospectClient._get_jti(token) == "abc-123"

    def test_returns_none_when_no_jti_claim(self):
        token = _make_jwt({"sub": "user-1"})
        assert IntrospectClient._get_jti(token) is None

    def test_returns_none_when_jti_empty_string(self):
        token = _make_jwt({"jti": ""})
        assert IntrospectClient._get_jti(token) is None

    def test_returns_none_when_jti_not_a_string(self):
        token = _make_jwt({"jti": 12345})
        assert IntrospectClient._get_jti(token) is None

    def test_returns_none_when_token_not_three_parts(self):
        assert IntrospectClient._get_jti("eyJ.payload-only") is None
        assert IntrospectClient._get_jti("singleblob") is None
        assert IntrospectClient._get_jti("a.b.c.d") is None

    def test_returns_none_when_payload_not_base64(self):
        assert IntrospectClient._get_jti("header.@@@-not-base64-@@@.sig") is None

    def test_returns_none_when_payload_not_json(self):
        not_json_payload = base64.urlsafe_b64encode(b"plain text not json").rstrip(b"=").decode("ascii")
        assert IntrospectClient._get_jti(f"hdr.{not_json_payload}.sig") is None

    def test_returns_none_when_payload_not_an_object(self):
        list_payload = base64.urlsafe_b64encode(b"[1,2,3]").rstrip(b"=").decode("ascii")
        assert IntrospectClient._get_jti(f"hdr.{list_payload}.sig") is None

    def test_padding_handles_payload_lengths_correctly(self):
        """base64url payloads can be 0/1/2/3 mod 4 — the helper must pad correctly."""
        for sub_value in ("a", "ab", "abc", "abcd", "abcde"):
            token = _make_jwt({"jti": "j", "sub": sub_value})
            assert IntrospectClient._get_jti(token) == "j", f"failed for sub={sub_value!r}"


# ── Cache hit / miss / TTL ───────────────────────────────────────────────


class TestCacheHitMiss:
    """First call hits HB and caches; second call returns from cache."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_first_call_misses_second_hits(self, client):
        token = _make_jwt({"jti": "j-001", "sub": "u-1"})
        route = respx.post(f"{HEARTBEAT_URL}/api/auth/introspect").mock(
            return_value=Response(200, json=_active_response_body("j-001"))
        )

        r1 = await client.introspect(token)
        r2 = await client.introspect(token)

        # Same dataclass object on a hit (cached by reference).
        assert r1 is r2
        # HB called exactly once across two introspects.
        assert route.call_count == 1
        # Counters: 1 miss, 1 hit.
        assert _counter_value("relay_introspect_cache_total", result="miss") == 1
        assert _counter_value("relay_introspect_cache_total", result="hit") == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_different_jtis_dont_collide(self, client):
        token_a = _make_jwt({"jti": "j-A"})
        token_b = _make_jwt({"jti": "j-B"})
        route = respx.post(f"{HEARTBEAT_URL}/api/auth/introspect").mock(
            return_value=Response(200, json=_active_response_body())
        )

        await client.introspect(token_a)
        await client.introspect(token_b)
        # Both miss — separate cache entries.
        assert route.call_count == 2
        assert _counter_value("relay_introspect_cache_total", result="miss") == 2

        # Each cached on second call.
        await client.introspect(token_a)
        await client.introspect(token_b)
        assert route.call_count == 2  # still 2 — both were hits
        assert _counter_value("relay_introspect_cache_total", result="hit") == 2


class TestCacheTtl:
    """Entries expire after ``INTROSPECT_CACHE_TTL_S``."""

    def test_ttl_constant_is_30_seconds(self):
        assert INTROSPECT_CACHE_TTL_S == 30.0

    @respx.mock
    @pytest.mark.asyncio
    async def test_cache_expires_after_ttl(self, monkeypatch):
        """Drive ``time.monotonic`` forward past the TTL — next call must miss."""
        from src.clients import introspect as introspect_module

        clock = {"now": 1_000_000.0}

        def fake_monotonic() -> float:
            return clock["now"]

        monkeypatch.setattr(introspect_module.time, "monotonic", fake_monotonic)

        client = IntrospectClient(
            heartbeat_url=HEARTBEAT_URL,
            service_api_key=API_KEY,
            service_signing_key=SIGNING_KEY,
        )
        token = _make_jwt({"jti": "ttl-test"})
        route = respx.post(f"{HEARTBEAT_URL}/api/auth/introspect").mock(
            return_value=Response(200, json=_active_response_body())
        )

        await client.introspect(token)
        assert route.call_count == 1

        # Within TTL — hit.
        clock["now"] += 29.0
        await client.introspect(token)
        assert route.call_count == 1

        # Past TTL — miss + new HB call.
        clock["now"] += 2.0  # now 31s after first call
        await client.introspect(token)
        assert route.call_count == 2


# ── Negative cache ───────────────────────────────────────────────────────


class TestNegativeCache:
    """``active=false`` outcomes are cached the same as positives."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_negative_first_call_calls_hb_and_caches(self, client):
        token = _make_jwt({"jti": "bad-001"})
        route = respx.post(f"{HEARTBEAT_URL}/api/auth/introspect").mock(
            return_value=Response(200, json=_inactive_response_body("TOKEN_EXPIRED"))
        )

        with pytest.raises(JWTRejectedError) as exc_info:
            await client.introspect(token)
        assert exc_info.value.error_code == "TOKEN_EXPIRED"
        assert exc_info.value.status_code == 401
        assert route.call_count == 1

        # Second call hits the negative cache — no HB call, same rejection.
        with pytest.raises(JWTRejectedError) as exc_info_2:
            await client.introspect(token)
        assert exc_info_2.value.error_code == "TOKEN_EXPIRED"
        assert exc_info_2.value.status_code == 401
        assert route.call_count == 1
        assert _counter_value("relay_introspect_cache_total", result="hit") == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_negative_cache_preserves_message(self, client):
        token = _make_jwt({"jti": "bad-002"})
        respx.post(f"{HEARTBEAT_URL}/api/auth/introspect").mock(
            return_value=Response(
                200,
                json=_inactive_response_body("TOKEN_INVALID", "Detailed reason from HB"),
            )
        )

        with pytest.raises(JWTRejectedError) as e1:
            await client.introspect(token)
        with pytest.raises(JWTRejectedError) as e2:
            await client.introspect(token)
        # Negative cache reproduces the exact message text from HB.
        assert e2.value.message == e1.value.message == "Detailed reason from HB"


# ── Bypass header ────────────────────────────────────────────────────────


class TestBypassCache:
    """``bypass_cache=True`` skips both read and write."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_bypass_skips_read(self, client):
        token = _make_jwt({"jti": "bypass-1"})
        route = respx.post(f"{HEARTBEAT_URL}/api/auth/introspect").mock(
            return_value=Response(200, json=_active_response_body())
        )

        await client.introspect(token)  # populates the cache
        await client.introspect(token, bypass_cache=True)  # skips cache, calls HB
        assert route.call_count == 2
        assert _counter_value("relay_introspect_cache_total", result="bypass") == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_bypass_skips_write(self, client):
        """A bypass call must NOT poison the cache — subsequent normal call still misses."""
        token = _make_jwt({"jti": "bypass-2"})
        route = respx.post(f"{HEARTBEAT_URL}/api/auth/introspect").mock(
            return_value=Response(200, json=_active_response_body())
        )

        await client.introspect(token, bypass_cache=True)
        # Second call (no bypass) should still miss because the bypass
        # call didn't cache its outcome.
        await client.introspect(token)
        assert route.call_count == 2
        # bypass + miss recorded — but no hit.
        assert _counter_value("relay_introspect_cache_total", result="bypass") == 1
        assert _counter_value("relay_introspect_cache_total", result="miss") == 1
        assert _counter_value("relay_introspect_cache_total", result="hit") == 0

    @respx.mock
    @pytest.mark.asyncio
    async def test_bypass_works_for_negative_outcome_too(self, client):
        """A bypass call on a known-bad token must NOT poison the negative cache."""
        token = _make_jwt({"jti": "bypass-3"})
        route = respx.post(f"{HEARTBEAT_URL}/api/auth/introspect").mock(
            return_value=Response(200, json=_inactive_response_body("TOKEN_INVALID"))
        )

        with pytest.raises(JWTRejectedError):
            await client.introspect(token, bypass_cache=True)
        # Cache was not written — second non-bypass call still misses + hits HB.
        with pytest.raises(JWTRejectedError):
            await client.introspect(token)
        assert route.call_count == 2


# ── Missing jti fallback ─────────────────────────────────────────────────


class TestMissingJtiFallback:
    """Tokens lacking ``jti`` fall through to HB on every call (no caching)."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_no_jti_calls_hb_each_time(self, client):
        token = _make_jwt({"sub": "user-1"})  # no jti
        route = respx.post(f"{HEARTBEAT_URL}/api/auth/introspect").mock(
            return_value=Response(200, json=_active_response_body())
        )

        await client.introspect(token)
        await client.introspect(token)
        await client.introspect(token)
        # No caching — every call hits HB.
        assert route.call_count == 3
        assert _counter_value("relay_introspect_cache_total", result="no_jti") == 3
        assert _counter_value("relay_introspect_cache_total", result="hit") == 0
        assert _counter_value("relay_introspect_cache_total", result="miss") == 0

    @respx.mock
    @pytest.mark.asyncio
    async def test_malformed_token_calls_hb_each_time(self, client):
        """Bogus token shape → no jti → no caching, but the call still
        proceeds (HB is responsible for rejecting bad shapes)."""
        token = "not-a-jwt-at-all"
        route = respx.post(f"{HEARTBEAT_URL}/api/auth/introspect").mock(
            return_value=Response(200, json=_inactive_response_body("TOKEN_INVALID"))
        )

        with pytest.raises(JWTRejectedError):
            await client.introspect(token)
        with pytest.raises(JWTRejectedError):
            await client.introspect(token)
        assert route.call_count == 2
        assert _counter_value("relay_introspect_cache_total", result="no_jti") == 2


# ── LRU eviction at maxsize ──────────────────────────────────────────────


class TestLruEviction:
    """When ``maxsize`` is hit, oldest entries are evicted first."""

    def test_maxsize_constant_is_1000(self):
        assert INTROSPECT_CACHE_MAXSIZE == 1000

    @respx.mock
    @pytest.mark.asyncio
    async def test_eviction_at_maxsize_drops_oldest(self):
        """Use a small ``cache_maxsize=3`` so we can prove eviction without
        timing out the test on 1001 round-trips."""
        client = IntrospectClient(
            heartbeat_url=HEARTBEAT_URL,
            service_api_key=API_KEY,
            service_signing_key=SIGNING_KEY,
            cache_maxsize=3,
        )
        route = respx.post(f"{HEARTBEAT_URL}/api/auth/introspect").mock(
            return_value=Response(200, json=_active_response_body())
        )

        tokens = [_make_jwt({"jti": f"j-{i}"}) for i in range(4)]

        # Fill the cache: tokens 0, 1, 2 → all miss + cached.
        for t in tokens[:3]:
            await client.introspect(t)
        assert route.call_count == 3

        # Token 0 still cached — hit.
        await client.introspect(tokens[0])
        assert route.call_count == 3

        # Add token 3 — pushes the oldest non-bumped entry (token 1) out.
        # Token 0 was just bumped, so it survives. Tokens 2, 3, 0 stay.
        await client.introspect(tokens[3])
        assert route.call_count == 4

        # Token 1 should now be evicted — re-introspecting it misses HB.
        await client.introspect(tokens[1])
        assert route.call_count == 5

        # Token 0 survived the eviction (it was most-recently-used).
        await client.introspect(tokens[0])
        assert route.call_count == 5  # still cached → hit

    @respx.mock
    @pytest.mark.asyncio
    async def test_cache_size_never_exceeds_maxsize(self):
        client = IntrospectClient(
            heartbeat_url=HEARTBEAT_URL,
            service_api_key=API_KEY,
            service_signing_key=SIGNING_KEY,
            cache_maxsize=5,
        )
        respx.post(f"{HEARTBEAT_URL}/api/auth/introspect").mock(
            return_value=Response(200, json=_active_response_body())
        )

        for i in range(20):
            await client.introspect(_make_jwt({"jti": f"j-{i}"}))

        # The internal _cache must never have grown beyond maxsize.
        assert len(client._cache) <= 5


# ── Per-instance isolation (no module-level globals) ─────────────────────


class TestInstanceIsolation:
    """Two clients share no cache state — important for test isolation
    and for any future multi-tenant client-per-config scenario."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_two_clients_have_separate_caches(self):
        client_a = IntrospectClient(
            heartbeat_url=HEARTBEAT_URL,
            service_api_key=API_KEY,
            service_signing_key=SIGNING_KEY,
        )
        client_b = IntrospectClient(
            heartbeat_url=HEARTBEAT_URL,
            service_api_key=API_KEY,
            service_signing_key=SIGNING_KEY,
        )
        token = _make_jwt({"jti": "shared-jti"})
        route = respx.post(f"{HEARTBEAT_URL}/api/auth/introspect").mock(
            return_value=Response(200, json=_active_response_body())
        )

        await client_a.introspect(token)
        # client_b has its own cache, so this call must miss + hit HB.
        await client_b.introspect(token)
        assert route.call_count == 2


# ── COUNTER_HELP registration ────────────────────────────────────────────


class TestCounterHelpRegistration:
    """The counter must show up in COUNTER_HELP so /metrics emits HELP/TYPE
    for it even before any introspect call has fired."""

    def test_introspect_cache_counter_in_help(self):
        assert "relay_introspect_cache_total" in counters.COUNTER_HELP
        help_text, type_str = counters.COUNTER_HELP["relay_introspect_cache_total"]
        assert isinstance(help_text, str) and help_text
        assert type_str == "counter"
