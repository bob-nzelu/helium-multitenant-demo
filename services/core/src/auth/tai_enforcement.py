"""Server-side TAI eligibility enforcement for Core (Sika authz).

Brings the Scout-side TAI resolver (``tai_resolver.py``, ported verbatim) to the
BACKEND so Core ENFORCES who may approve / reject / reverse / accept-inbound /
update-payment — instead of trusting any authenticated tenant member (the
segregation-of-duties gap proven live 2026-06-28: a restricted Operator approved
an Owner's invoice with HTTP 202).

Eligibility is driven by:
  * the tenant's TAI config (submission/reversal surfaces + role permissions),
    read from the live tenant config (config_cache) with a BUNDLED Sika default
    fallback so the demo backend is never silently laissez-faire;
  * the actor's ROLE (JWT ``role`` claim) + EMAIL (resolved from auth.users by
    user_id — the JWT carries no email, and the Sika TAI approvers are ``email:``
    identities);
  * the doc's approval stage + creator (for the no-self-approval rule).

Pure-resolver decisions in; (allowed: bool, reason: str) out. Handlers turn a
False into HTTP 403.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

from src.auth import tai_resolver as TR

logger = logging.getLogger(__name__)

_AUTH_USERS_TABLE = "auth.users"


def bearer_claims(request) -> dict[str, Any]:
    """Best-effort JWT claims for the calling actor.

    Prefers ``request.state.jwt_claims`` when the middleware populated it; else
    decodes the forwarded ``Authorization: Bearer`` payload directly (Core's auth
    layer already VERIFIED the token to reach the handler — we only need to READ
    role/sub/permissions, which the middleware does not reliably expose on every
    route). The JWT carries role + sub + permissions but NOT email.
    """
    st = getattr(request.state, "jwt_claims", None)
    if isinstance(st, dict) and (st.get("sub") or st.get("role")):
        return st
    try:
        auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    except Exception:
        auth = ""
    if isinstance(auth, str) and auth.lower().startswith("bearer "):
        parts = auth[7:].strip().split(".")
        if len(parts) >= 2:
            seg = parts[1] + "=" * (-len(parts[1]) % 4)
            try:
                payload = json.loads(base64.urlsafe_b64decode(seg))
                if isinstance(payload, dict):
                    return payload
            except Exception:
                return {}
    return {}

# Bundled Sika TAI — mirrors HeartBeat seed ``sika_tenant_config.json`` 'tai'
# block. Used when the live tenant config (config_cache) carries no 'tai', so
# enforcement on the Sika demo backend is never silently laissez-faire.
_SIKA_TAI_DEFAULT: dict[str, Any] = {
    "tai": {
        "policy_revision": "sika-tai-1-proof-10id",
        "submission": {
            "creators": ["role:Owner", "role:Admin", "perm:creator"],
            "stages": [
                {
                    "stage": 1,
                    "min_req": 1,
                    "approvers": [
                        "email:bamkefaoluwa.ibukun@ng.sika.com",
                        "email:ojelade.folashade@ng.sika.com",
                    ],
                }
            ],
        },
        "reversal": {
            "creators": ["token:original_invoice_creator", "perm:reverser"],
            "stages": [
                {
                    "stage": 1,
                    "min_req": 1,
                    "approvers": ["email:ojelade.folashade@ng.sika.com"],
                }
            ],
        },
    }
}


def load_tai_config(app_state) -> "TR.TAIConfig":
    """Resolve the active TAI config: live tenant config if it carries a 'tai'
    block, else the bundled Sika default. Never raises — falls back to the
    all-defaults resolver config on a parse error."""
    raw: Any = None
    cache = getattr(app_state, "config_cache", None)
    if cache is not None:
        try:
            raw = cache.raw
        except Exception:
            raw = None
    if isinstance(raw, dict) and isinstance(raw.get("tai"), dict):
        try:
            return TR.parse_tai_config(raw)
        except TR.TAIConfigError as exc:
            logger.warning("tai_config parse failed; using bundled Sika default: %s", exc)
    try:
        return TR.parse_tai_config(_SIKA_TAI_DEFAULT)
    except TR.TAIConfigError as exc:  # pragma: no cover - bundled is valid
        logger.error("bundled Sika TAI invalid: %s", exc)
        return TR.default_tai_config()


async def resolve_actor(conn, request, body: dict[str, Any]) -> tuple[str, str, str]:
    """Return (user_id, email, role) for the calling actor.

    user_id: JWT ``sub`` (body ``actor_user_id`` override honoured for tests).
    role: JWT ``role``. email: looked up from auth.users by user_id — the JWT
    carries no email and the Sika TAI approvers are ``email:`` identities. Body
    ``actor_user_email`` overrides the lookup (tests / explicit-actor demo calls).
    """
    claims = bearer_claims(request)
    body = body or {}
    user_id = str(body.get("actor_user_id") or body.get("user_id") or claims.get("sub") or "").strip()
    role = str(claims.get("role") or "").strip()
    email = str(body.get("actor_user_email") or "").strip()
    if (not email or not role) and user_id:
        try:
            cur = await conn.execute(
                f"SELECT email, role_id FROM {_AUTH_USERS_TABLE} WHERE user_id = %s",
                (user_id,),
            )
            row = await cur.fetchone()
            if row is not None:
                email = email or str(row[0] or "")
                role = role or str(row[1] or "")
        except Exception as exc:
            logger.warning("resolve_actor lookup failed user_id=%s: %s", user_id, exc)
    return user_id, email, role


async def creator_email(conn, invoice_row: dict[str, Any] | None) -> str:
    """Resolve the invoice creator's email. created_by may be an email, a
    user_id, or NULL (older rows). Returns '' when unknown."""
    cb = str((invoice_row or {}).get("created_by") or "").strip()
    if not cb:
        return ""
    if "@" in cb:
        return cb
    try:
        cur = await conn.execute(
            f"SELECT email FROM {_AUTH_USERS_TABLE} WHERE user_id = %s", (cb,)
        )
        row = await cur.fetchone()
        return str(row[0]) if row is not None else ""
    except Exception:
        return ""


def check_can_approve(
    config,
    *,
    actor_email: str,
    actor_role: str,
    surface: str = "submission",
    current_stage: int = 0,
    creator_email: str = "",
    is_creator: bool = False,
) -> tuple[bool, str]:
    """Is the actor an eligible approver for the surface's current stage?
    A surface with no stages (laissez-faire) is open; the no-self-approval rule
    applies per the stage's allow_creator_self_approval flag."""
    chain = TR.resolve_chain(
        config=config,
        surface=surface,
        actor_email=actor_email,
        actor_role=actor_role,
        original_invoice_creator_email=creator_email,
        is_current_request_creator=is_creator,
        current_stage=current_stage or 0,
    )
    if not chain.stages:
        return True, "no_approval_stages"
    current = next((s for s in chain.stages if s.stage == chain.current_stage), None)
    if current is None:
        return True, "no_current_stage"
    return current.eligible, current.reason


def check_can_request(config, *, actor_email: str, actor_role: str) -> tuple[bool, str]:
    """Can the actor initiate a submission-approval request (is a creator)?"""
    ok = TR.is_creator_of_surface(
        config=config, surface="submission",
        actor_email=actor_email, actor_role=actor_role,
    )
    return ok, ("submission_creator" if ok else "not_submission_creator")


def check_can_reverse(
    config, *, actor_email: str, actor_role: str, original_creator_email: str = ""
) -> tuple[bool, str]:
    """Can the actor initiate a reversal (reversal-surface creator: the original
    invoice creator or a perm:reverser = Owner/Admin baseline)?"""
    ok = TR.is_creator_of_surface(
        config=config, surface="reversal",
        actor_email=actor_email, actor_role=actor_role,
        original_invoice_creator_email=original_creator_email,
    )
    return ok, ("reversal_creator" if ok else "not_reversal_creator")


def check_has_permission(config, *, actor_role: str, perm: str) -> tuple[bool, str]:
    """Does the actor's role baseline grant a TAI permission (accept_inbound /
    update_payment / view_inbound / ...)?"""
    perms = TR.effective_permissions(
        role=actor_role, extra_permissions=(), role_permissions=config.role_permissions
    )
    ok = perm in perms
    return ok, (f"has_{perm}" if ok else f"missing_{perm}")
