"""
POST /api/artifacts/fetch — Scout-callable artifact bytes / lifecycle JSON.

Implements CLAUDE.md "Backend Debt Notes" §B-RelayArtifactFetch.

VERB_DELTA (the single load-bearing delta for this chip): the SBS sketch
``relay_fetch_artifact`` docstrings ``GET /api/relay/artifacts/<ref>`` with the
``artifact_ref`` IN THE PATH. The real Relay route is **POST-body**:
``POST /api/artifacts/fetch { artifact_ref, artifact_type }``. ``artifact_ref``
is effectively a bearer capability for raw signed-PDF / HLX / FIRS bytes — it
MUST NEVER appear in a URL, querystring, proxy log, or referrer. So it travels
in the POST body only.

Bytes-vs-JSON:
    - HARD artifacts → raw **bytes** to Scout, ``Content-Type`` per kind, sourced
      from HeartBeat blob storage (``HeartBeatClient.fetch_blob``).
    - LIFECYCLE artifacts → raw **JSON** to Scout, sourced from Core
      (``CoreClient.fetch_lifecycle_artifact``).
    - A miss → HTTP 404 ``{"code": "ARTIFACT_NOT_FOUND", "artifact_ref": <ref>}``
      (exact body; see ``ArtifactNotFoundError``).

Kind is signalled by the explicit ``artifact_type`` the Scout
``ScoutRelayArtifactFetchAdapter`` already sends, with fallback inference from
the ref prefix (``manifest-`` ⇒ a lifecycle JSON manifest) when ``artifact_type``
is absent. (Open question for ARCH (b): request-signalled vs Relay-inferred from
stored kind, and the closed kind enumeration — see the debt map.)

Response headers mirror the SBS where reasonable: ``ETag: sha256:<digest>`` over
the returned body, and an ``X-Artifact-Ref`` echo header.
"""

import hashlib
import json
import logging

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from ..caller_context import CallerContext
from ..deps import authenticate_request
from ..models import ArtifactFetchRequest
from ...errors import ArtifactNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Kind classification (§B-RelayArtifactFetch) ──────────────────────────────

# HARD artifacts → bytes, with the Content-Type to serve per kind.
_HARD_ARTIFACT_CONTENT_TYPES = {
    "signed_pdf": "application/pdf",
    "fixed_pdf": "application/pdf",
    "original_pdf": "application/pdf",
    "backend_pdf": "application/pdf",
    "qr_invoice": "application/vnd.helium.invoice-qr+json",
    "qr_blob": "application/vnd.helium.invoice-qr+json",
    "signature": "application/octet-stream",
}

# LIFECYCLE artifacts → raw JSON.
_LIFECYCLE_ARTIFACT_KINDS = frozenset(
    {
        "hlx",
        "firs_returned_artifact",
        "approval_lifecycle_json",
        "manifest",
    }
)

# Default Content-Type for a hard artifact whose kind is known-hard but not in
# the explicit map (defensive; PDFs are the dominant hard artifact).
_DEFAULT_HARD_CONTENT_TYPE = "application/pdf"


def classify_artifact(
    *,
    artifact_type: str | None,
    artifact_ref: str,
) -> tuple[str, str | None]:
    """Resolve ``(kind_class, content_type)`` for an artifact request.

    ``kind_class`` is one of ``"lifecycle"`` (→ JSON) or ``"hard"`` (→ bytes).
    ``content_type`` is the MIME to serve for hard artifacts, or ``None`` for
    lifecycle (the JSON response sets its own).

    Signalling priority:
        1. explicit ``artifact_type`` (what the Scout adapter sends);
        2. fallback inference from the ref prefix (``manifest-`` ⇒ lifecycle
           manifest);
        3. final fallback ⇒ hard/PDF (the dominant hard artifact).
    """
    kind = (artifact_type or "").strip().lower()

    if kind:
        if kind in _LIFECYCLE_ARTIFACT_KINDS:
            return "lifecycle", None
        if kind in _HARD_ARTIFACT_CONTENT_TYPES:
            return "hard", _HARD_ARTIFACT_CONTENT_TYPES[kind]
        # Unknown explicit kind — fall through to prefix inference below, then
        # to the hard/PDF default, rather than guessing JSON for an unknown.

    # Fallback inference from the ref prefix.
    ref = (artifact_ref or "").strip()
    if ref.startswith("manifest-"):
        return "lifecycle", None

    return "hard", _DEFAULT_HARD_CONTENT_TYPE


def _etag_for(body: bytes) -> str:
    """SBS-mirrored strong-ish ETag: ``sha256:<hexdigest>`` over the body."""
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


@router.post(
    "/api/artifacts/fetch",
    summary="Fetch artifact bytes (hard) or lifecycle JSON by artifact_ref",
    responses={
        200: {"description": "Artifact bytes (hard) or lifecycle JSON"},
        401: {"description": "Authentication failed"},
        404: {"description": "Artifact not found (ARTIFACT_NOT_FOUND)"},
    },
)
async def fetch_artifact(
    request: Request,
    body: ArtifactFetchRequest,
    ctx: CallerContext = Depends(authenticate_request),
) -> Response:
    """Fetch one artifact by reference — bytes for hard kinds, JSON for lifecycle.

    Auth is enforced via the shared ``authenticate_request`` dispatcher (HMAC /
    service-creds / user-JWT), identical to every other sensitive Relay route.
    """
    artifact_ref = (body.artifact_ref or "").strip()
    trace_id = ctx.trace_id or getattr(request.state, "trace_id", "")

    if not artifact_ref:
        # An empty ref can never resolve — treat as a miss with the contract body.
        raise ArtifactNotFoundError(artifact_ref="")

    kind_class, content_type = classify_artifact(
        artifact_type=body.artifact_type,
        artifact_ref=artifact_ref,
    )

    # User JWT is forwarded downstream for attribution; HMAC/service paths use
    # Relay's own service credentials to talk to HB/Core.
    jwt_token = None
    if ctx.is_user:
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            jwt_token = auth_header[7:].strip()

    logger.info(
        "[%s] POST /api/artifacts/fetch — kind_class=%s, artifact_type=%s, "
        "actor=%s, tenant=%s",
        trace_id,
        kind_class,
        body.artifact_type or "none",
        ctx.actor_type,
        ctx.tenant_id,
    )

    if kind_class == "lifecycle":
        core = request.app.state.core
        lifecycle_json = await core.fetch_lifecycle_artifact(
            artifact_ref=artifact_ref,
            artifact_type=body.artifact_type,
        )
        if not lifecycle_json:
            raise ArtifactNotFoundError(artifact_ref=artifact_ref)
        # Raw JSON to Scout only. ETag over the canonical JSON bytes so a Scout
        # cache can dedupe identical lifecycle payloads (SBS-mirrored).
        raw = json.dumps(lifecycle_json, sort_keys=True).encode("utf-8")
        return JSONResponse(
            content=lifecycle_json,
            headers={
                "ETag": _etag_for(raw),
                "X-Artifact-Ref": artifact_ref,
            },
        )

    # HARD artifact → bytes from HB blob storage.
    heartbeat = request.app.state.heartbeat
    blob = await heartbeat.fetch_blob(artifact_ref=artifact_ref, jwt_token=jwt_token)
    if not blob or not blob.get("data"):
        raise ArtifactNotFoundError(artifact_ref=artifact_ref)

    data: bytes = blob["data"]
    # Prefer the kind-derived Content-Type (contract: per-kind); fall back to
    # whatever HB reported for the stored blob.
    served_content_type = content_type or blob.get("content_type") or _DEFAULT_HARD_CONTENT_TYPE
    return Response(
        content=data,
        media_type=served_content_type,
        headers={
            "ETag": _etag_for(data),
            "X-Artifact-Ref": artifact_ref,
        },
    )
