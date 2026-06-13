"""
POST /api/artifacts/fetch — Scout-callable artifact bytes / lifecycle JSON
(R-M4 / §B-RelayArtifactFetch).

Implements CLAUDE.md "Backend Debt Notes" §B-RelayArtifactFetch +
READER_RELAY_INTEGRATION_DEBT_MAP_2026_06_12.md L139-201.

VERB_DELTA (the single load-bearing delta for this chip): the SBS sketch
``relay_fetch_artifact`` (relay.py:1030) docstrings ``GET /api/relay/artifacts/
<ref>`` with the ``artifact_ref`` IN THE PATH. The real Relay route is
**POST-body**: ``POST /api/artifacts/fetch { artifact_ref, artifact_type }``.
``artifact_ref`` is effectively a *bearer capability* for raw signed-PDF / HLX /
FIRS bytes — it MUST NEVER appear in a URL, querystring, proxy log, or referrer.
So it travels in the POST body only (debt-map L193-200). There is deliberately
NO GET variant.

Bytes-vs-JSON:
    - HARD artifacts → raw **bytes** to Scout, ``Content-Type`` per kind, sourced
      from HeartBeat blob storage (``HeartBeatClient.fetch_blob``).
    - LIFECYCLE artifacts → raw **JSON** to Scout, sourced from Core
      (``CoreClient.fetch_lifecycle_artifact``). Reader never sees this raw JSON;
      Scout reduces it to display-safe fields (``raw_bytes_sent`` stays false).
    - A miss → HTTP 404 ``{"code": "ARTIFACT_NOT_FOUND", "artifact_ref": <ref>}``
      (EXACT contract body; see ``ArtifactNotFoundError.to_dict``).

Kind signalling: the Scout production adapter ``ScoutRelayArtifactFetchAdapter``
(scout.py:567-590) sends an explicit ``artifact_type`` alongside ``artifact_ref``,
so the route uses that as the primary discriminator, with a fallback inference
from the ref prefix (``manifest-`` ⇒ a lifecycle JSON manifest) when
``artifact_type`` is absent. (ARCH Open Q (b) — request-signalled vs
Relay-inferred-from-stored-kind, and the closed kind enumeration; this chip
PROPOSES the enum in its handoff report. Until ARCH rules, request-signalled
with prefix fallback is the conservative choice.)

CANON — response headers MUST be ``X-Relay-Artifact-*``; NEVER ``X-SBS-*`` (the
SBS-branded ``X-SBS-Relay-Artifact`` / ``X-SBS-Artifact-Ref`` /
``X-SBS-Artifact-Version`` / ``X-SBS-Durable-Invoice-Data`` headers in
relay.py:1054-1059 are simulator-only and must not ship). The route emits:
    - ``X-Relay-Artifact: true``
    - ``X-Relay-Artifact-Ref: <artifact_ref>``  (echo; the request carried it)
    - ``X-Relay-Artifact-Kind: <hard|lifecycle>``
    - ``X-Relay-Artifact-ETag: sha256:<digest>``  (over the returned body)
and, for QR blobs, ``X-Relay-Durable-Invoice-Data: qr_bytes``.

Auth: the existing combined dispatcher ``authenticate_request`` (HMAC / Bearer
JWT / service creds), resolved inside the handler (mirrors /api/finalize) so the
SAME dispatcher /api/ingest uses runs, and the cached raw body is read for HMAC
before the JSON is parsed.
"""

import hashlib
import json
import logging
from typing import Optional, Tuple

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from ..caller_context import CallerContext
from ..deps import authenticate_request
from ...errors import ArtifactNotFoundError, ValidationFailedError

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Kind classification (§B-RelayArtifactFetch) ──────────────────────────────
#
# Closed kind enum (PROPOSED — ARCH Open Q (b)). Derived from the SBS
# ``_blob_payload`` kind→mime map (scout_backend_simulator_core.py:710-750) +
# the debt-map observed list (L216-218) + the concrete ``artifact_type`` values
# the Scout adapter actually sends (scout.py:4060 ``approval_lifecycle``,
# scout.py:7981/8193 ``backend_copy``).
#
# HARD artifacts → bytes, with the Content-Type to serve per kind.
_HARD_ARTIFACT_CONTENT_TYPES = {
    # PDFs (the dominant hard artifact). ``backend_copy`` is the Scout adapter's
    # name for an approver's backend PDF copy (scout.py:7981/8193).
    "signed_pdf": "application/pdf",
    "fixed_pdf": "application/pdf",
    "original_pdf": "application/pdf",
    "backend_pdf": "application/pdf",
    "backend_copy": "application/pdf",
    # Durable invoice QR payload bytes (NOT a generated QR-stamped PDF).
    "qr_invoice": "application/vnd.helium.invoice-qr+json",
    "qr_blob": "application/vnd.helium.invoice-qr+json",
    # Raw detached signature bytes.
    "signature": "application/octet-stream",
}

# Kinds whose Content-Type is the QR-invoice media type (used to add the
# ``X-Relay-Durable-Invoice-Data`` marker, mirroring SBS relay.py:1058-1059).
_QR_INVOICE_CONTENT_TYPE = "application/vnd.helium.invoice-qr+json"

# LIFECYCLE artifacts → raw JSON (Core-owned).
_LIFECYCLE_ARTIFACT_KINDS = frozenset(
    {
        "hlx",
        "firs_returned_artifact",
        "approval_lifecycle_json",
        "approval_lifecycle",  # Scout adapter's spelling (scout.py:4060)
        "manifest",
    }
)

# Default Content-Type for a hard artifact whose kind is known-hard but not in
# the explicit map (defensive; PDFs are the dominant hard artifact).
_DEFAULT_HARD_CONTENT_TYPE = "application/pdf"


def classify_artifact(
    *,
    artifact_type: Optional[str],
    artifact_ref: str,
) -> Tuple[str, Optional[str]]:
    """Resolve ``(kind_class, content_type)`` for an artifact request.

    ``kind_class`` is one of ``"lifecycle"`` (→ JSON) or ``"hard"`` (→ bytes).
    ``content_type`` is the MIME to serve for hard artifacts, or ``None`` for
    lifecycle (the JSON response sets its own).

    Signalling priority (ARCH Open Q (b) — conservative default):
        1. explicit ``artifact_type`` (what the Scout adapter sends);
        2. fallback inference from the ref prefix (``manifest-`` ⇒ lifecycle
           manifest), matching SBS ``relay_fetch_artifact`` relay.py:1035;
        3. final fallback ⇒ hard/PDF (the dominant hard artifact).
    """
    kind = (artifact_type or "").strip().lower()

    if kind:
        if kind in _LIFECYCLE_ARTIFACT_KINDS:
            return "lifecycle", None
        if kind in _HARD_ARTIFACT_CONTENT_TYPES:
            return "hard", _HARD_ARTIFACT_CONTENT_TYPES[kind]
        # Unknown explicit kind — fall through to prefix inference, then to the
        # hard/PDF default. We never *guess* JSON for an unknown kind (serving
        # opaque bytes is safe; mislabelling bytes as JSON corrupts them).

    # Fallback inference from the ref prefix (SBS parity).
    ref = (artifact_ref or "").strip()
    if ref.startswith("manifest-"):
        return "lifecycle", None

    return "hard", _DEFAULT_HARD_CONTENT_TYPE


def _etag_for(body: bytes) -> str:
    """SBS-mirrored ETag value: ``sha256:<hexdigest>`` over the body
    (relay.py:1057 ``_artifact_etag``)."""
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


async def _read_fetch_body(request: Request) -> dict:
    """Read + parse the artifact-fetch JSON body from the cached raw body.

    ``BodyCacheMiddleware`` stashes the raw bytes in ``request.state.raw_body``
    (so HMAC auth and this handler read the SAME body — VERB_DELTA: the
    ``artifact_ref`` is body-sourced, never path-sourced). Falls back to
    ``request.body()`` if the cache is absent (e.g. direct unit calls).
    """
    raw = getattr(request.state, "raw_body", None)
    if raw is None:
        raw = await request.body()
    if not raw:
        raise ValidationFailedError(
            message=(
                "artifact fetch requires a JSON body with 'artifact_ref' "
                "(and optionally 'artifact_type')."
            ),
        )
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValidationFailedError(
            message=f"Invalid artifact-fetch JSON body: {exc}",
        ) from exc
    if not isinstance(parsed, dict):
        raise ValidationFailedError(
            message="artifact-fetch body must be a JSON object.",
        )
    return parsed


@router.post(
    "/api/artifacts/fetch",
    summary="Fetch artifact bytes (hard) or lifecycle JSON by artifact_ref (POST-body)",
    responses={
        200: {"description": "Artifact bytes (hard) or lifecycle JSON"},
        400: {"description": "Invalid / missing JSON body"},
        401: {"description": "Authentication failed"},
        404: {"description": "Artifact not found (ARTIFACT_NOT_FOUND)"},
    },
)
async def fetch_artifact(request: Request) -> Response:
    """Fetch one artifact by reference — bytes for hard kinds, JSON for lifecycle.

    Auth is enforced via the shared ``authenticate_request`` dispatcher (HMAC /
    service-creds / user-JWT), identical to every other sensitive Relay route.
    Resolved here (not as a ``Depends`` default) so the SAME dispatcher
    /api/ingest uses runs and the cached raw body is read for HMAC BEFORE the
    JSON is parsed. ``ctx`` is intentionally NOT a handler parameter so FastAPI
    does not try to validate ``CallerContext`` as a request body.
    """
    ctx: CallerContext = await authenticate_request(request)

    trace_id = ctx.trace_id or getattr(request.state, "trace_id", "")
    body = await _read_fetch_body(request)

    artifact_ref = str(body.get("artifact_ref") or "").strip()
    artifact_type = body.get("artifact_type")
    if artifact_type is not None:
        artifact_type = str(artifact_type).strip() or None

    if not artifact_ref:
        # An empty/absent ref can never resolve — treat as a miss with the
        # contract body (the VERB_DELTA forbids a path ref, so there is no
        # other place it could have come from).
        raise ArtifactNotFoundError(artifact_ref="")

    kind_class, content_type = classify_artifact(
        artifact_type=artifact_type,
        artifact_ref=artifact_ref,
    )

    # User JWT is forwarded downstream for attribution; HMAC/service paths use
    # Relay's own service credentials (HMAC s2s) to talk to HB/Core.
    jwt_token: Optional[str] = None
    if ctx.is_user:
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            jwt_token = auth_header[7:].strip()

    logger.info(
        "[%s] POST /api/artifacts/fetch — kind_class=%s artifact_type=%s "
        "actor=%s tenant=%s jwt=%s",
        trace_id,
        kind_class,
        artifact_type or "none",
        ctx.actor_type,
        ctx.tenant_id,
        "yes" if jwt_token else "no",
    )

    if kind_class == "lifecycle":
        core = request.app.state.core
        lifecycle_json = await core.fetch_lifecycle_artifact(
            artifact_ref=artifact_ref,
            artifact_type=artifact_type,
        )
        if not lifecycle_json:
            raise ArtifactNotFoundError(artifact_ref=artifact_ref)
        # Raw JSON to Scout only. ETag over the canonical JSON bytes so a Scout
        # cache can dedupe identical lifecycle payloads.
        raw = json.dumps(lifecycle_json, sort_keys=True).encode("utf-8")
        return JSONResponse(
            content=lifecycle_json,
            headers={
                "X-Relay-Artifact": "true",
                "X-Relay-Artifact-Ref": artifact_ref,
                "X-Relay-Artifact-Kind": "lifecycle",
                "X-Relay-Artifact-ETag": _etag_for(raw),
            },
        )

    # HARD artifact → bytes from HB blob storage.
    heartbeat = request.app.state.heartbeat
    blob = await heartbeat.fetch_blob(artifact_ref=artifact_ref, jwt_token=jwt_token)
    if not blob or not blob.get("data"):
        raise ArtifactNotFoundError(artifact_ref=artifact_ref)

    data: bytes = blob["data"]
    # Prefer the kind-derived Content-Type (contract: per-kind); fall back to
    # whatever HB reported for the stored blob, then to PDF.
    served_content_type = (
        content_type or blob.get("content_type") or _DEFAULT_HARD_CONTENT_TYPE
    )

    headers = {
        "X-Relay-Artifact": "true",
        "X-Relay-Artifact-Ref": artifact_ref,
        "X-Relay-Artifact-Kind": "hard",
        "X-Relay-Artifact-ETag": _etag_for(data),
    }
    # QR-invoice durable-data marker (SBS parity, de-branded from
    # ``X-SBS-Durable-Invoice-Data``).
    if served_content_type == _QR_INVOICE_CONTENT_TYPE:
        headers["X-Relay-Durable-Invoice-Data"] = "qr_bytes"

    return Response(
        content=data,
        media_type=served_content_type,
        headers=headers,
    )
