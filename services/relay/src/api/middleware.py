"""
API Middleware

TraceIDMiddleware: Adds X-Trace-ID to every request/response.
BodyCacheMiddleware: Pre-reads and caches raw body so both HMAC auth and
                     form parsing can access it without "Stream consumed" errors.
RateLimitMiddleware: Token-bucket rate limit per caller, per window
                     (Phase 1b, spec §6).
RequestSafetyMiddleware: Size cap + handler timeout (Phase 1b, spec §8).
relay_error_handler: Catches RelayError and returns structured JSON.
"""

import asyncio
import base64
import json
import logging
from typing import Any, Callable, Dict, Optional, Tuple

from uuid6 import uuid7

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..errors import RelayError, RequestTooLargeError, RequestTimeoutError

logger = logging.getLogger(__name__)


# Endpoint → token cost (spec §6.5). Paths matched by startswith.
# 0 = never rate-limited (deployment liveness / scrape endpoints).
ENDPOINT_WEIGHTS: Tuple[Tuple[str, int], ...] = (
    ("/health", 0),
    ("/metrics", 0),
    ("/api/ingest", 5),
    ("/api/bulk/preview", 10),
)
DEFAULT_WEIGHT = 1


def _endpoint_weight(path: str) -> int:
    for prefix, weight in ENDPOINT_WEIGHTS:
        if path.startswith(prefix):
            return weight
    return DEFAULT_WEIGHT


def _unsafe_decode_jwt_payload(token: str) -> Dict[str, Any]:
    """
    Decode a JWT payload WITHOUT verifying the signature.

    Used only for rate-limit keying — the auth dispatcher still verifies
    the token via HeartBeat introspect before the request reaches a
    handler. If the payload is malformed we return {} and the middleware
    falls back to a token-hash key.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        padding = "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(parts[1] + padding))
    except Exception:
        return {}


def _caller_key_from_request(scope: Scope) -> str:
    """
    Derive rate-limit caller key from raw request headers (no auth call).

    Keying rules (spec §6.1):
      - HMAC path  → api_key
      - Service creds Bearer api_key:api_secret → api_key
      - User JWT Bearer <jwt> → tenant_id claim (fallback: sub, then token[:16])
      - Anonymous → ip:<client_ip>
    """
    headers = {k.decode("latin-1").lower(): v.decode("latin-1")
               for k, v in scope.get("headers", [])}

    if "x-api-key" in headers and "x-signature" in headers:
        return f"key:{headers['x-api-key']}"

    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if ":" in token and "." not in token:
            api_key = token.split(":", 1)[0]
            return f"key:{api_key}"
        if token:
            payload = _unsafe_decode_jwt_payload(token)
            tid = payload.get("tenant_id") or payload.get("tid")
            if tid:
                return f"tenant:{tid}"
            sub = payload.get("sub")
            if sub:
                return f"user:{sub}"
            return f"jwt:{token[:16]}"

    client = scope.get("client")
    ip = client[0] if client else "unknown"
    return f"ip:{ip}"


class BodyCacheMiddleware:
    """
    Pure ASGI middleware that pre-reads the request body and replaces
    the receive callable so that both HMAC authentication and
    FastAPI's form/file parsers can read the same body.

    Must be added BEFORE any middleware that calls call_next() (like
    TraceIDMiddleware), because BaseHTTPMiddleware wraps receive.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Read the entire body from the original receive
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break

        # Store raw body in scope state so dependencies can access it
        # without calling request.body() (which may conflict with stream).
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["raw_body"] = body

        # Replace receive with a callable that replays the cached body
        body_sent = False

        async def cached_receive() -> Message:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            # After body is sent, return disconnect
            return {"type": "http.disconnect"}

        await self.app(scope, cached_receive, send)


class TraceIDMiddleware:
    """
    Pure ASGI middleware that injects X-Trace-ID into every request.

    If the client sends X-Trace-ID, use it. Otherwise, generate a new UUID.
    Stores trace_id in scope["state"] for downstream access via request.state.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Get or generate trace ID
        headers = dict(scope.get("headers", []))
        trace_id = headers.get(b"x-trace-id", b"").decode("utf-8")
        if not trace_id:
            trace_id = str(uuid7())

        # Store in scope state for request.state.trace_id access
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["trace_id"] = trace_id

        # Wrap send to inject response header
        async def send_with_trace(message: Message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-trace-id", trace_id.encode("utf-8")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_trace)


class RateLimitMiddleware:
    """
    Token-bucket rate limiter (spec §6).

    Runs BEFORE auth so abuse is rejected cheaply. Keys per caller derived
    from raw headers (api_key or tenant_id from unverified JWT payload).

    Checks per-minute and per-hour buckets. On a reject, returns 429 with
    Retry-After + X-RateLimit-* headers. On allow, adds X-RateLimit-*
    response headers for the caller's remaining budget.

    Tier-aware limits come from ``app.state.rate_limits_by_tier`` which is
    populated at startup from HB ``tier_limits``. For this phase every
    caller is treated as ``standard``; a per-caller tier resolver is a
    Phase 2 enhancement.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        cost = _endpoint_weight(path)

        if cost == 0:
            await self.app(scope, receive, send)
            return

        # Late import to avoid a cycle at module load
        app_state = scope.get("app").state  # type: ignore[attr-defined]
        redis = getattr(app_state, "redis", None)
        if redis is None:
            # Rate-limit storage not configured — spec §6.6 fail-open.
            await self.app(scope, receive, send)
            return

        tier_limits = getattr(app_state, "rate_limits_by_tier", {}) or {}
        # Everyone gets "standard" in Phase 1b. Per-caller tier resolver
        # is a Phase 2 enhancement — see spec §6.3.
        limits = tier_limits.get("standard") or {
            "api_requests_per_minute": 100,
            "api_requests_per_hour": 2000,
        }

        caller_key = _caller_key_from_request(scope)
        per_minute_cap = int(limits.get("api_requests_per_minute") or 0)
        per_hour_cap = int(limits.get("api_requests_per_hour") or 0)

        per_minute = await redis.token_bucket_check(
            caller_key, "per_minute", per_minute_cap, cost=cost,
        )
        per_hour = await redis.token_bucket_check(
            caller_key, "per_hour", per_hour_cap, cost=cost,
        )

        if not per_minute.allowed or not per_hour.allowed:
            await self._send_429(send, per_minute, per_hour, caller_key, path)
            return

        rl_headers = _rate_limit_headers(per_minute, per_hour)

        async def send_with_headers(message: Message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(rl_headers)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)

    async def _send_429(
        self, send: Send,
        per_minute: "Any", per_hour: "Any",
        caller_key: str, path: str,
    ):
        import time as _time
        now = int(_time.time())
        # Retry-After uses whichever window blocked
        retry_after = 1
        if not per_minute.allowed:
            retry_after = max(1, per_minute.reset_epoch - now)
        elif not per_hour.allowed:
            retry_after = max(1, per_hour.reset_epoch - now)

        logger.warning(
            f"rate_limit_exceeded caller={caller_key} path={path} "
            f"per_minute={per_minute.allowed} per_hour={per_hour.allowed}"
        )

        body = json.dumps({
            "status": "error",
            "error_code": "RATE_LIMIT_EXCEEDED",
            "message": (
                "Rate limit exceeded. Retry after the Retry-After interval."
            ),
            "details": [{
                "window": "per_minute" if not per_minute.allowed else "per_hour",
                "retry_after_seconds": str(retry_after),
            }],
        }).encode("utf-8")

        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"retry-after", str(retry_after).encode("ascii")),
        ]
        headers.extend(_rate_limit_headers(per_minute, per_hour))

        await send({
            "type": "http.response.start",
            "status": 429,
            "headers": headers,
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })


def _rate_limit_headers(per_minute, per_hour):
    """Build X-RateLimit-* response header list (spec §6.4)."""
    return [
        (b"x-ratelimit-limit-minute", str(per_minute.limit).encode("ascii")),
        (b"x-ratelimit-remaining-minute", str(per_minute.remaining).encode("ascii")),
        (b"x-ratelimit-reset-minute", str(per_minute.reset_epoch).encode("ascii")),
        (b"x-ratelimit-limit-hour", str(per_hour.limit).encode("ascii")),
        (b"x-ratelimit-remaining-hour", str(per_hour.remaining).encode("ascii")),
        (b"x-ratelimit-reset-hour", str(per_hour.reset_epoch).encode("ascii")),
    ]


class RequestSafetyMiddleware:
    """
    Early request safety checks (spec §8).

    - Size cap: reject requests with ``Content-Length`` greater than
      ``max_request_bytes`` with 413 before any handler runs. Requests
      without Content-Length (chunked) are allowed through — uvicorn
      enforces a separate h11 limit.
    - Timeout: wrap the downstream ASGI call in ``asyncio.wait_for`` at
      ``request_timeout_s`` seconds, returning 504 on timeout. Prevents
      slow-loris and runaway-handler hangs.
    """

    def __init__(
        self,
        app: ASGIApp,
        max_request_bytes: int = 52_428_800,  # 50 MB
        request_timeout_s: int = 30,
    ):
        self.app = app
        self.max_request_bytes = max_request_bytes
        self.request_timeout_s = request_timeout_s

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length_raw = self._header_value(scope, b"content-length")
        if content_length_raw is not None:
            try:
                content_length = int(content_length_raw)
            except ValueError:
                content_length = None
            if (content_length is not None
                    and content_length > self.max_request_bytes):
                await self._send_error(
                    send, 413, "REQUEST_TOO_LARGE",
                    f"Request body {content_length} bytes exceeds the "
                    f"{self.max_request_bytes}-byte limit.",
                )
                return

        try:
            await asyncio.wait_for(
                self.app(scope, receive, send),
                timeout=self.request_timeout_s,
            )
        except asyncio.TimeoutError:
            # Handler may have started streaming a response. send() will raise
            # if the response has already started — in that case we cannot do
            # anything except log.
            try:
                await self._send_error(
                    send, 504, "REQUEST_TIMEOUT",
                    f"Request exceeded {self.request_timeout_s}s timeout.",
                )
            except Exception:
                logger.error(
                    "Timeout after response started — cannot emit 504.",
                )

    @staticmethod
    def _header_value(scope: Scope, name: bytes) -> Optional[str]:
        name_lower = name.lower()
        for k, v in scope.get("headers", []):
            if k.lower() == name_lower:
                try:
                    return v.decode("latin-1")
                except Exception:
                    return None
        return None

    @staticmethod
    async def _send_error(send: Send, status: int, code: str, message: str):
        body = json.dumps({
            "status": "error",
            "error_code": code,
            "message": message,
        }).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })


async def relay_error_handler(request: Request, exc: RelayError) -> JSONResponse:
    """
    Global exception handler for RelayError hierarchy.

    Converts any RelayError into a structured JSON response with
    the appropriate HTTP status code.
    """
    logger.warning(
        f"[{getattr(request.state, 'trace_id', 'unknown')}] "
        f"{exc.error_code}: {exc.message}",
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
    )
