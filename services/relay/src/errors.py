"""
Relay-API Error Hierarchy

Cherry-picked from old_src/services/errors/exceptions.py with cleaner signatures.
All error codes follow the RELAY_BULK_SPEC.md convention.

Hierarchy:
    RelayError (base)
    ├── ValidationFailedError (400)
    │   ├── NoFilesProvidedError
    │   ├── TooManyFilesError
    │   ├── InvalidFileExtensionError
    │   └── FileSizeExceededError
    ├── MalwareDetectedError (400)
    ├── AuthenticationFailedError (401)
    │   ├── InvalidAPIKeyError
    │   ├── SignatureVerificationFailedError
    │   ├── TimestampExpiredError
    │   └── JWTRejectedError
    ├── CrossTenantDeniedError (403)
    ├── RateLimitExceededError (429)
    ├── QueueNotFoundError (404)
    ├── DuplicateFileError (409)
    ├── InternalError (500)
    ├── TransientError (500, retryable)
    │   ├── ConnectionTimeoutError
    │   └── ConnectionResetError
    └── ServiceUnavailableError (503)
        ├── CoreUnavailableError
        └── HeartBeatUnavailableError
"""

from typing import Any, Dict, List, Optional


# ── Base ──────────────────────────────────────────────────────────────────


class RelayError(Exception):
    """Base class for all Relay-API errors."""

    def __init__(
        self,
        error_code: str,
        message: str,
        details: Optional[List[Dict[str, Any]]] = None,
        status_code: int = 500,
    ):
        self.error_code = error_code
        self.message = message
        self.details = details or []
        self.status_code = status_code
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to API response format."""
        result: Dict[str, Any] = {
            "status": "error",
            "error_code": self.error_code,
            "message": self.message,
        }
        if self.details:
            result["details"] = self.details
        return result


# ── Validation Errors (400) ───────────────────────────────────────────────


class ValidationFailedError(RelayError):
    """File or request validation failed."""

    def __init__(
        self,
        message: str = "Validation failed",
        details: Optional[List[Dict[str, Any]]] = None,
    ):
        super().__init__(
            error_code="VALIDATION_FAILED",
            message=message,
            details=details,
            status_code=400,
        )


class NoFilesProvidedError(ValidationFailedError):
    """No files in the request."""

    def __init__(self):
        super().__init__(message="No files provided. Upload at least 1 file.")


class TooManyFilesError(ValidationFailedError):
    """File count exceeds limit."""

    def __init__(self, count: int, limit: int):
        super().__init__(
            message=f"Too many files: {count} provided, max {limit}.",
            details=[{"count": str(count), "limit": str(limit)}],
        )


class InvalidFileExtensionError(ValidationFailedError):
    """File extension not in allowed list."""

    def __init__(self, filename: str, allowed: List[str]):
        super().__init__(
            message=f"File '{filename}' has invalid extension. Allowed: {', '.join(allowed)}",
            details=[{"filename": filename, "allowed": ", ".join(allowed)}],
        )


class FileSizeExceededError(ValidationFailedError):
    """Individual file or total batch size exceeds limit."""

    def __init__(self, filename: str, size_mb: float, limit_mb: float):
        super().__init__(
            message=f"File '{filename}' ({size_mb:.1f} MB) exceeds {limit_mb} MB limit.",
            details=[{
                "filename": filename,
                "size_mb": f"{size_mb:.2f}",
                "limit_mb": f"{limit_mb:.1f}",
            }],
        )


class TotalSizeExceededError(ValidationFailedError):
    """Total upload size exceeds limit."""

    def __init__(self, total_mb: float, limit_mb: float):
        super().__init__(
            message=f"Total upload size ({total_mb:.1f} MB) exceeds {limit_mb} MB limit.",
            details=[{
                "total_mb": f"{total_mb:.2f}",
                "limit_mb": f"{limit_mb:.1f}",
            }],
        )


# ── Malware (400) ─────────────────────────────────────────────────────────


class MalwareDetectedError(RelayError):
    """Malware detected in uploaded file."""

    def __init__(self, filename: str, virus_name: str = "unknown"):
        super().__init__(
            error_code="MALWARE_DETECTED",
            message=f"Malware detected in '{filename}': {virus_name}",
            details=[{"filename": filename, "virus_name": virus_name}],
            status_code=400,
        )


# ── Authentication Errors (401) ───────────────────────────────────────────


class AuthenticationFailedError(RelayError):
    """Authentication failed (generic)."""

    def __init__(self, message: str = "Authentication failed"):
        super().__init__(
            error_code="AUTHENTICATION_FAILED",
            message=message,
            status_code=401,
        )


class InvalidAPIKeyError(AuthenticationFailedError):
    """API key not recognized."""

    def __init__(self):
        super().__init__(message="API key not recognized.")


class SignatureVerificationFailedError(AuthenticationFailedError):
    """HMAC signature mismatch."""

    def __init__(self):
        super().__init__(message="HMAC signature verification failed.")


class WebhookSignatureError(AuthenticationFailedError):
    """
    Inbound HeartBeat webhook failed Ed25519 signature verification (L5).

    Raised by :class:`~src.api.webhook_auth.WebhookVerifier` for every
    rejection on the webhook receive path: missing/malformed headers,
    unknown ``kid`` (no published webhook key matches), expired timestamp
    (replay window), tampered body, or a bad signature. Subclasses
    :class:`AuthenticationFailedError`, so it maps to HTTP **401** with
    ``error_code="AUTHENTICATION_FAILED"`` via the global
    ``relay_error_handler`` — no extra handler wiring.

    This REPLACES the symmetric-HMAC ``WebhookAuthError`` (3-header
    ``sha256=`` shared-secret scheme) per the ARCH "Bob ratification pass"
    2026-06-19 (ledger L5), which reversed the earlier symmetric ruling.
    HeartBeat now SIGNS webhooks with Ed25519 (reusing its OAuth JWKS
    Ed25519 infra); Relay VERIFIES against HB's published webhook public
    key. There is intentionally no symmetric code path.

    The ``reason`` is kept on the exception for structured logging /
    metrics; the client-facing ``message`` is deliberately generic so a
    probe cannot distinguish "unknown kid" from "bad signature".
    """

    def __init__(self, reason: str = "Webhook signature verification failed"):
        super().__init__(message="Webhook signature verification failed.")
        self.reason = reason


class TimestampExpiredError(AuthenticationFailedError):
    """Request timestamp outside the 5-minute window."""

    def __init__(self, age_seconds: int):
        super().__init__(
            message=f"Timestamp is {age_seconds}s old. Must be within 300s."
        )


class JWTRejectedError(RelayError):
    """
    HeartBeat rejected a user JWT.

    Per BACKEND_SERVICE_AUTH_AND_ABUSE_SPEC.md §3.1, introspect returns
    an error_code that maps to a specific HTTP status (401 for token
    invalid/expired, 403 for permission/stepup, 423 for account locked).
    Callers pass those through here rather than collapsing to 401.
    """

    def __init__(
        self,
        message: str = "JWT rejected by HeartBeat",
        error_code: str = "TOKEN_INVALID",
        status_code: int = 401,
    ):
        super().__init__(
            error_code=error_code,
            message=message,
            status_code=status_code,
        )


class AuthUpstreamUnavailableError(RelayError):
    """
    HeartBeat introspect is unreachable or returned 5xx.

    Per BACKEND_SERVICE_AUTH_AND_ABUSE_SPEC.md §2.4, auth failures against
    an unreachable upstream must fail closed — return 502 to the caller,
    never permit the request on cached claims.
    """

    def __init__(self, message: str = "Auth upstream unavailable"):
        super().__init__(
            error_code="AUTH_UPSTREAM_UNAVAILABLE",
            message=message,
            status_code=502,
        )


class CrossTenantDeniedError(RelayError):
    """
    Caller from tenant A attempted to read/write a tenant B resource.

    Per HeartBeat ``CLAUDE.md`` "Tenant Isolation — Default Deny" — every
    cross-tenant attempt is 403 + a Prometheus counter + an HB audit
    event with ``event_type="security.cross_tenant_denied"``. HB's audit
    writer fans out to ``security_events`` per its own dual-fire rule.
    Not 404; not a silent skip — make abuse visible.

    Response body intentionally does NOT echo ``caller_tenant`` /
    ``requested_tenant`` — those would leak tenant existence to a
    cross-tenant probe. Both ids are kept on the exception object for
    audit + counter wiring only.
    """

    def __init__(
        self,
        endpoint: str,
        caller_tenant: str,
        requested_tenant: str,
    ):
        super().__init__(
            error_code="CROSS_TENANT_DENIED",
            message="Access denied — resource is not in your tenant.",
            details=[{"endpoint": endpoint}],
            status_code=403,
        )
        self.endpoint = endpoint
        self.caller_tenant = caller_tenant
        self.requested_tenant = requested_tenant


# ── Rate Limit (429) ──────────────────────────────────────────────────────


class RateLimitExceededError(RelayError):
    """Daily usage limit exceeded."""

    def __init__(
        self,
        message: str = "Daily rate limit exceeded",
        retry_after_seconds: int = 86400,
    ):
        super().__init__(
            error_code="RATE_LIMIT_EXCEEDED",
            message=message,
            status_code=429,
        )
        self.retry_after_seconds = retry_after_seconds


# ── Not Found (404) ───────────────────────────────────────────────────────


class QueueNotFoundError(RelayError):
    """Queue entry not found."""

    def __init__(self, queue_id: str):
        super().__init__(
            error_code="QUEUE_NOT_FOUND",
            message=f"Queue entry '{queue_id}' not found.",
            status_code=404,
        )


class ArtifactNotFoundError(RelayError):
    """
    Artifact referenced by ``artifact_ref`` is not resolvable (§B-RelayArtifactFetch).

    Per CLAUDE.md "Backend Debt Notes" §B-RelayArtifactFetch + the debt-map
    (READER_RELAY_INTEGRATION_DEBT_MAP_2026_06_12.md L155-157), a miss on the
    artifact-fetch route returns HTTP 404 with the EXACT body
    ``{"code": "ARTIFACT_NOT_FOUND", "artifact_ref": <ref>}`` — NO
    ``status`` / ``error_code`` / ``message`` / ``details`` envelope. This
    mirrors the SBS executable spec (``relay_fetch_artifact`` relay.py:1039/1052
    + ``fetch_blob_from_sbs`` core.py:122). ``to_dict`` is overridden so the
    global ``relay_error_handler`` emits that contract shape verbatim rather
    than the generic Relay error envelope.

    ``artifact_ref`` is a bearer-capability handle: it is echoed in the body
    (the request already carried it) but MUST NOT be placed in a URL / path /
    proxy log (VERB_DELTA, §B-RelayArtifactFetch). The miss path here is
    body-only, consistent with that rule.
    """

    def __init__(self, artifact_ref: str):
        super().__init__(
            error_code="ARTIFACT_NOT_FOUND",
            message="Artifact not found.",
            status_code=404,
        )
        self.artifact_ref = artifact_ref

    def to_dict(self) -> Dict[str, Any]:
        # Contract body shape — intentionally NOT the generic Relay envelope.
        return {"code": self.error_code, "artifact_ref": self.artifact_ref}


# ── Duplicate (409) ───────────────────────────────────────────────────────


class DuplicateFileError(RelayError):
    """File already processed (duplicate hash)."""

    def __init__(self, file_hash: str, original_queue_id: Optional[str] = None):
        super().__init__(
            error_code="DUPLICATE_FILE",
            message="This file has already been processed.",
            details=[{
                "file_hash": file_hash,
                **({"original_queue_id": original_queue_id} if original_queue_id else {}),
            }],
            status_code=409,
        )
        self.file_hash = file_hash
        self.original_queue_id = original_queue_id


class AlreadyFinalizedError(RelayError):
    """
    A finalize (#3) call arrived for a ``trace_id`` (or ``ref``) that has
    already been finalized.

    Per §B-Submit (CLAUDE.md L260-266 / SCOUT contract §3.3) a duplicate /
    already-finalized ``trace_id`` returns **409**, which the client treats
    as success (idempotent). The same ``trace_id`` is carried across the
    #2↔#3 switch so a retry that flips call type still dedups backend-side.

    This differs from an idempotent *replay* (same call repeated): a replay
    returns the cached 202 body with ``idempotent_replay=True``; this 409 is
    the explicit "already terminal" signal the contract names.
    """

    def __init__(
        self,
        ref: str = "",
        trace_id: str = "",
        original_event_id: Optional[str] = None,
    ):
        super().__init__(
            error_code="ALREADY_FINALIZED",
            message="This document has already been finalized.",
            details=[{
                **({"ref": ref} if ref else {}),
                **({"trace_id": trace_id} if trace_id else {}),
                **({"original_event_id": original_event_id} if original_event_id else {}),
            }],
            status_code=409,
        )
        self.ref = ref
        self.trace_id = trace_id
        self.original_event_id = original_event_id


class FinalizeReferenceMissingError(ValidationFailedError):
    """A finalize (#3) call arrived with neither ``ref`` nor ``trace_id``.

    The #3 reference-only call fiscalizes an already-ingested doc *by
    reference* (file SHA-256 / ``trace_id`` / ``doc_ref``). With no
    reference at all there is nothing to fiscalize — 400, never a silent
    no-op (§B-Submit, "never silent").
    """

    def __init__(self):
        super().__init__(
            message=(
                "finalize requires a reference: provide 'ref' "
                "(file SHA-256 / doc_ref) and/or 'trace_id'."
            ),
        )


# ── Internal Error (500) ──────────────────────────────────────────────────


class InternalError(RelayError):
    """Internal server error."""

    def __init__(
        self,
        message: str = "Internal server error",
        original_error: Optional[Exception] = None,
    ):
        super().__init__(
            error_code="INTERNAL_ERROR",
            message=message,
            status_code=500,
        )
        self.original_error = original_error


# ── Configuration Error (process-bail at startup) ─────────────────────────


class ConfigError(RelayError):
    """
    Raised at startup when Relay's configuration is invalid in a way that
    cannot be safely degraded. Examples:

    - ``RELAY_S2S_SIGNING_KEY`` is not 64 lowercase-hex chars (CSSV1 R9.1)
    - System clock skew vs HeartBeat exceeds the safe margin (CSSV1 R9.2)
    - A required env var is missing on a code path that cannot tolerate
      the empty default

    These should NEVER surface to a request handler. They abort the
    lifespan startup so the container fails fast and the orchestrator
    surfaces the misconfiguration in deploy logs rather than mystery
    401s mid-flight.
    """

    def __init__(self, message: str):
        super().__init__(
            error_code="CONFIG_ERROR",
            message=message,
            status_code=500,  # never returned to a client; bails at startup
        )


# ── Transient Errors (500, retryable) ─────────────────────────────────────


class TransientError(RelayError):
    """Base for transient errors — clients should retry with backoff."""

    def __init__(
        self,
        error_code: str = "TRANSIENT_ERROR",
        message: str = "Transient error, please retry",
        status_code: int = 500,
    ):
        super().__init__(
            error_code=error_code,
            message=message,
            status_code=status_code,
        )


class ConnectionTimeoutError(TransientError):
    """Upstream connection timed out."""

    def __init__(self, message: str = "Connection timed out"):
        super().__init__(error_code="CONNECTION_TIMEOUT", message=message)


class ConnectionResetError(TransientError):
    """Upstream connection reset."""

    def __init__(self, message: str = "Connection reset by peer"):
        super().__init__(error_code="CONNECTION_RESET", message=message)


# ── Service Unavailable (503) ─────────────────────────────────────────────


class ServiceUnavailableError(RelayError):
    """Upstream service temporarily unavailable."""

    def __init__(self, service_name: str = "Service", message: Optional[str] = None):
        if message is None:
            message = f"{service_name} is temporarily unavailable"
        super().__init__(
            error_code="SERVICE_UNAVAILABLE",
            message=message,
            status_code=503,
        )
        self.service_name = service_name


class CoreUnavailableError(ServiceUnavailableError):
    """Core API is unavailable."""

    def __init__(self, message: str = "Core API is temporarily unavailable"):
        super().__init__(service_name="Core", message=message)


class HeartBeatUnavailableError(ServiceUnavailableError):
    """HeartBeat API is unavailable."""

    def __init__(self, message: str = "HeartBeat API is temporarily unavailable"):
        super().__init__(service_name="HeartBeat", message=message)


# ── Encryption Errors ─────────────────────────────────────────────────────


class EncryptionError(RelayError):
    """Encryption or decryption failed."""

    def __init__(self, message: str = "Encryption error"):
        super().__init__(
            error_code="ENCRYPTION_ERROR",
            message=message,
            status_code=400,
        )


class DecryptionError(EncryptionError):
    """Failed to decrypt incoming envelope."""

    def __init__(self, message: str = "Failed to decrypt request envelope"):
        super().__init__(message=message)


class EncryptionRequiredError(RelayError):
    """Remote request without encryption."""

    def __init__(self):
        super().__init__(
            error_code="ENCRYPTION_REQUIRED",
            message="Encryption required for remote requests. Set X-Encrypted: true.",
            status_code=403,
        )


# ── Module Cache Errors ──────────────────────────────────────────────────


class ModuleCacheError(RelayError):
    """Transforma module cache operation failed."""

    def __init__(self, message: str = "Module cache error"):
        super().__init__(
            error_code="MODULE_CACHE_ERROR",
            message=message,
            status_code=500,
        )


class ModuleNotLoadedError(ServiceUnavailableError):
    """Transforma module not yet loaded."""

    def __init__(self, module_name: str):
        super().__init__(
            service_name="TransformaCache",
            message=f"Module '{module_name}' not loaded. Try again shortly.",
        )
        self.module_name = module_name


# ── IRN/QR Errors ────────────────────────────────────────────────────────


class IRNGenerationError(RelayError):
    """IRN generation failed."""

    def __init__(self, message: str = "IRN generation failed"):
        super().__init__(
            error_code="IRN_GENERATION_ERROR",
            message=message,
            status_code=500,
        )


class QRGenerationError(RelayError):
    """QR code generation failed."""

    def __init__(self, message: str = "QR generation failed"):
        super().__init__(
            error_code="QR_GENERATION_ERROR",
            message=message,
            status_code=500,
        )
