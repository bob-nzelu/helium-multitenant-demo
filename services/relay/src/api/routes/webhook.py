"""
Ed25519 webhook receive route — L5.

``POST /api/webhook`` — HeartBeat pushes a webhook; Relay verifies its
**Ed25519** signature (see :mod:`src.api.webhook_auth`) and 200s.

This is the consumer side of the L5 rework: HB signs with Ed25519 (reusing
its OAuth JWKS infra + a published webhook public key), Relay verifies against
that key fetched by ``kid``. There is **no symmetric HMAC** anywhere on this
path — the earlier shared-secret receiver was reversed by the ARCH "Bob
ratification pass" 2026-06-19 (ledger L5).

The route deliberately does the minimum: verify, then return ``200``.
Message-type dispatch is OUT OF SCOPE here (a separate harmonization) — see
the NEEDS-FROM-HB note at the dispatch seam below.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..webhook_auth import WebhookVerifier, get_webhook_verifier

logger = logging.getLogger(__name__)

router = APIRouter()


class WebhookAck(BaseModel):
    """200 acknowledgement body for a verified webhook delivery."""

    status: str = "ok"


@router.post(
    "/api/webhook",
    response_model=WebhookAck,
    summary="Receive an Ed25519-signed HeartBeat webhook (L5)",
)
async def receive_webhook(
    request: Request,
    verifier: WebhookVerifier = Depends(get_webhook_verifier),
) -> WebhookAck:
    """Verify the inbound webhook's Ed25519 signature, then acknowledge.

    On signature failure :meth:`WebhookVerifier.verify` raises a 401
    ``WebhookSignatureError`` which the global ``relay_error_handler`` renders;
    this handler is only reached when verification passed.
    """
    # Ed25519 signature verification (raises 401 WebhookSignatureError on fail).
    await verifier.verify(request)

    trace_id = getattr(request.state, "trace_id", "")

    # ──────────────────────────────────────────────────────────────────────
    # NEEDS-FROM-HB: message-type dispatch goes HERE.
    #
    # This is a SEPARATE harmonization, NOT part of the L5 receiver-rework
    # task. Once HeartBeat publishes the webhook MESSAGE-TYPE CATALOGUE (what
    # event kinds it pushes — e.g. config_changed, key_rotation, lifecycle
    # updates — and the body schema for each), parse the verified body here
    # and route to the matching handler. Until then the verified delivery is
    # acknowledged with a bare 200 and no body interpretation.
    # ──────────────────────────────────────────────────────────────────────
    logger.info("[%s] Webhook received + Ed25519-verified (no dispatch yet).", trace_id)

    return WebhookAck(status="ok")
