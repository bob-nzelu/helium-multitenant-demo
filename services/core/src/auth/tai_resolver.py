"""TAI (Tenant Approval Intelligence) resolver — Scout-owned, real-time.

Canonical doc: ``reader/App/docs/TAI_SHAPE_AND_PERMISSIONS.md``.

**Ownership boundary** (per Bob 2026-05-24): TAI is local, real-time, and
evaluated against the ciphered tenant_config on the user's machine. It is
**not** a "simulation." This module belongs to Scout's real-time projection
path. The backend simulator may consume the resolved facts when it emits
fake SSE-driven projections, but it does NOT own the TAI config or the
evaluation function.

Layers:

- **Pure resolver** (``parse_tai_config``, ``resolve_chain``,
  ``is_creator_of_surface``, ``effective_permissions``,
  ``identity_matches_actor``) — no I/O, no projection state. Just
  "given this config + this actor, what do you decide?"
- **Integration helpers** (``actor_policy_for_tai_row``,
  ``apply_tai_to_approval_rows``) — bridge between a loaded TAI config +
  a current-user projection + a row carrying TAI metadata, producing the
  display-safe ``actor_policy`` shape Reader contracts already consume.

Both layers are pure functions. Callers (Scout's real-time projection,
the fake backend simulator) hold the loaded TAI config + current-user
projection themselves and pass them in.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


_ALLOWED_ROLES = frozenset({"Owner", "Admin", "Operator", "Support"})
_ALLOWED_PERMISSIONS = frozenset(
    {
        "view_payment",
        "update_payment",
        "creator",
        "initiator",
        "reverser",
        "view_inbound",
        "accept_inbound",
    }
)
_ALLOWED_TOKENS = frozenset(
    {"original_invoice_creator", "creator", "any_approver"}
)
_ALLOWED_PREFIXES = ("role:", "email:", "perm:", "token:")
_SURFACES = frozenset({"submission", "reversal"})


class TAIConfigError(ValueError):
    """Raised when the on-disk TAI block fails validation."""


@dataclass(frozen=True)
class TAIIdentity:
    """One parsed identity entry from an ``approvers`` or ``creators`` list."""

    kind: str  # "role" | "email" | "perm" | "token"
    value: str

    @property
    def raw(self) -> str:
        return f"{self.kind}:{self.value}"


@dataclass(frozen=True)
class TAIStage:
    """One stage in an approval chain.

    ``stage`` is an integer level (1, 2, 3, ...). Lower numbers run first and
    cannot be bypassed. The ordering of stages is determined by the integer
    value, not by list position.
    """

    stage: int
    approvers: tuple[TAIIdentity, ...]
    min_req: int
    allow_creator_self_approval: bool = False


@dataclass(frozen=True)
class TAISurfacePolicy:
    """Policy for one approval surface (``submission`` or ``reversal``)."""

    creators: tuple[TAIIdentity, ...]
    stages: tuple[TAIStage, ...]


@dataclass(frozen=True)
class TAIConfig:
    policy_revision: str
    submission: TAISurfacePolicy
    reversal: TAISurfacePolicy
    role_permissions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


# Implicit role grants per TAI_SHAPE_AND_PERMISSIONS.md §2.
_IMPLICIT_ROLE_PERMISSIONS: Mapping[str, tuple[str, ...]] = {
    "Owner": (
        "view_payment",
        "update_payment",
        "creator",
        "initiator",
        "reverser",
        "view_inbound",
        "accept_inbound",
    ),
    "Admin": ("view_payment", "creator", "initiator", "reverser", "view_inbound"),
    "Operator": ("creator", "initiator"),
    "Support": (),
}


# Default fallback policies per TAI_SHAPE_AND_PERMISSIONS.md §4.
_DEFAULT_SUBMISSION = TAISurfacePolicy(
    creators=(
        TAIIdentity("role", "Owner"),
        TAIIdentity("role", "Admin"),
        TAIIdentity("role", "Operator"),
        TAIIdentity("perm", "creator"),
        TAIIdentity("perm", "initiator"),
    ),
    stages=(),
)
_DEFAULT_REVERSAL = TAISurfacePolicy(
    creators=(
        TAIIdentity("token", "original_invoice_creator"),
        TAIIdentity("perm", "reverser"),
    ),
    stages=(
        TAIStage(
            stage=1,
            approvers=(TAIIdentity("role", "Owner"),),
            min_req=1,
        ),
    ),
)


def parse_identity(raw: object) -> TAIIdentity:
    """Parse a single prefixed-string identity. Raises ``TAIConfigError``."""
    text = str(raw or "").strip()
    if not text:
        raise TAIConfigError("identity may not be empty")
    if not text.startswith(_ALLOWED_PREFIXES):
        raise TAIConfigError(
            f"identity {text!r} must start with one of {_ALLOWED_PREFIXES}"
        )
    prefix, _, value = text.partition(":")
    value = value.strip()
    if not value:
        raise TAIConfigError(f"identity {text!r} has empty value after prefix")
    kind = prefix
    if kind == "role" and value not in _ALLOWED_ROLES:
        raise TAIConfigError(
            f"role identity {text!r} must use one of {sorted(_ALLOWED_ROLES)}"
        )
    if kind == "perm" and value not in _ALLOWED_PERMISSIONS:
        raise TAIConfigError(
            f"perm identity {text!r} must use one of {sorted(_ALLOWED_PERMISSIONS)}"
        )
    if kind == "token" and value not in _ALLOWED_TOKENS:
        raise TAIConfigError(
            f"token identity {text!r} must use one of {sorted(_ALLOWED_TOKENS)}"
        )
    if kind == "email":
        value = value.lower()
    return TAIIdentity(kind=kind, value=value)


def parse_identities(items: object, *, field_name: str) -> tuple[TAIIdentity, ...]:
    if not isinstance(items, Iterable) or isinstance(items, (str, bytes)):
        raise TAIConfigError(f"{field_name} must be a list of identity strings")
    parsed = []
    for entry in items:
        parsed.append(parse_identity(entry))
    return tuple(parsed)


def parse_stage(payload: object, *, surface: str, index: int) -> TAIStage:
    if not isinstance(payload, Mapping):
        raise TAIConfigError(
            f"{surface}.stages[{index}] must be an object, got {type(payload).__name__}"
        )
    stage_raw = payload.get("stage")
    if not isinstance(stage_raw, int) or isinstance(stage_raw, bool):
        raise TAIConfigError(
            f"{surface}.stages[{index}].stage must be a positive integer (level)"
        )
    stage = int(stage_raw)
    if stage < 1:
        raise TAIConfigError(
            f"{surface}.stages[{index}].stage must be >= 1 (stages are 1-indexed levels)"
        )
    approvers = parse_identities(
        payload.get("approvers"),
        field_name=f"{surface}.stages[{index}].approvers",
    )
    min_req_raw = payload.get("min_req")
    if not isinstance(min_req_raw, int) or isinstance(min_req_raw, bool):
        raise TAIConfigError(
            f"{surface}.stages[{index}].min_req must be a positive integer"
        )
    min_req = int(min_req_raw)
    if min_req < 1:
        raise TAIConfigError(
            f"{surface}.stages[{index}].min_req must be at least 1"
        )
    if len(approvers) < min_req:
        raise TAIConfigError(
            f"{surface}.stages[{index}] has min_req={min_req} but only "
            f"{len(approvers)} approver identities"
        )
    allow_self = bool(payload.get("allow_creator_self_approval", False))
    return TAIStage(
        stage=stage,
        approvers=approvers,
        min_req=min_req,
        allow_creator_self_approval=allow_self,
    )


def parse_surface(
    payload: object,
    *,
    surface: str,
    default: TAISurfacePolicy,
) -> TAISurfacePolicy:
    if payload is None:
        return default
    if not isinstance(payload, Mapping):
        raise TAIConfigError(
            f"tai.{surface} must be an object, got {type(payload).__name__}"
        )
    creators = parse_identities(
        payload.get("creators", default.creators if default.creators else ()),
        field_name=f"tai.{surface}.creators",
    )
    if not creators:
        raise TAIConfigError(f"tai.{surface}.creators must not be empty")
    stages_raw = payload.get("stages", [])
    if stages_raw is None:
        stages_raw = []
    if not isinstance(stages_raw, list):
        raise TAIConfigError(f"tai.{surface}.stages must be a list")
    stages = tuple(
        parse_stage(stage, surface=surface, index=i)
        for i, stage in enumerate(stages_raw)
    )
    # Ensure stages are contiguous 1..N — gaps or duplicates are policy bugs.
    levels = [stage.stage for stage in stages]
    if levels:
        expected = list(range(1, len(levels) + 1))
        if sorted(levels) != expected:
            raise TAIConfigError(
                f"tai.{surface}.stages must have contiguous integer levels "
                f"1..{len(levels)}; got {sorted(levels)}"
            )
        # Sort by level so list position matches stage level.
        stages = tuple(sorted(stages, key=lambda s: s.stage))
    return TAISurfacePolicy(creators=creators, stages=stages)


def parse_role_permissions(payload: object) -> Mapping[str, tuple[str, ...]]:
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise TAIConfigError("role_permissions must be an object")
    out: dict[str, tuple[str, ...]] = {}
    for role_name, grants in payload.items():
        role_text = str(role_name).strip()
        if role_text not in _ALLOWED_ROLES:
            raise TAIConfigError(
                f"role_permissions key {role_text!r} must be one of "
                f"{sorted(_ALLOWED_ROLES)}"
            )
        if not isinstance(grants, list):
            raise TAIConfigError(
                f"role_permissions[{role_text}] must be a list of permission strings"
            )
        normalized: list[str] = []
        for grant in grants:
            grant_text = str(grant).strip()
            if grant_text not in _ALLOWED_PERMISSIONS:
                raise TAIConfigError(
                    f"role_permissions[{role_text}] entry {grant_text!r} must be "
                    f"one of {sorted(_ALLOWED_PERMISSIONS)}"
                )
            normalized.append(grant_text)
        out[role_text] = tuple(dict.fromkeys(normalized))
    return out


def parse_tai_config(payload: object) -> TAIConfig:
    """Parse the ``tai`` + ``role_permissions`` blocks of ``tenant_config.json``.

    The argument should be the *tenant_config root*, not just the ``tai`` slot,
    so the parser can pick up sibling ``role_permissions`` in one call. A
    fully-missing ``tai`` block applies the §4 defaults.
    """
    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise TAIConfigError(
            f"tenant_config payload must be an object, got {type(payload).__name__}"
        )
    role_permissions = parse_role_permissions(payload.get("role_permissions"))
    tai_block = payload.get("tai")
    if tai_block is None:
        return TAIConfig(
            policy_revision="default-no-tai",
            submission=_DEFAULT_SUBMISSION,
            reversal=_DEFAULT_REVERSAL,
            role_permissions=role_permissions,
        )
    if not isinstance(tai_block, Mapping):
        raise TAIConfigError(
            f"tai must be an object, got {type(tai_block).__name__}"
        )
    policy_revision = str(tai_block.get("policy_revision") or "unrevisioned").strip()
    submission = parse_surface(
        tai_block.get("submission"),
        surface="submission",
        default=_DEFAULT_SUBMISSION,
    )
    reversal = parse_surface(
        tai_block.get("reversal"),
        surface="reversal",
        default=_DEFAULT_REVERSAL,
    )
    return TAIConfig(
        policy_revision=policy_revision,
        submission=submission,
        reversal=reversal,
        role_permissions=role_permissions,
    )


def policy_for_surface(config: TAIConfig, surface: str) -> TAISurfacePolicy:
    if surface not in _SURFACES:
        raise TAIConfigError(
            f"unknown TAI surface {surface!r}; must be one of {sorted(_SURFACES)}"
        )
    return getattr(config, surface)


def effective_permissions(
    *,
    role: str,
    extra_permissions: Iterable[str],
    role_permissions: Mapping[str, tuple[str, ...]],
    excluded_permissions: Iterable[str] = (),
) -> frozenset[str]:
    """Compute the effective permissions for a user.

    Formula: ``(role_baseline ∪ extra_permissions) − excluded_permissions``.

    The role baseline comes from the tenant-configured ``role_permissions``
    if the role has an entry there, or from the implicit baseline in §2 of
    the canon otherwise. ``extra_permissions`` is the per-user
    ``permissions`` array (additive). ``excluded_permissions`` is the
    per-user ``permission_exclusions`` array (subtractive — strips a role
    baseline grant for one specific user without changing their role).
    """
    if role in role_permissions:
        baseline = tuple(role_permissions[role])
    else:
        baseline = _IMPLICIT_ROLE_PERMISSIONS.get(role, ())
    extras = tuple(str(p).strip() for p in extra_permissions if str(p or "").strip())
    exclusions = frozenset(
        str(p).strip() for p in excluded_permissions if str(p or "").strip()
    )
    combined = _expand_permission_aliases((*baseline, *extras))
    expanded_exclusions = _expand_permission_aliases(exclusions)
    return frozenset(perm for perm in combined if perm not in expanded_exclusions)


def identity_matches_actor(
    identity: TAIIdentity,
    *,
    actor_email: str,
    actor_role: str,
    actor_permissions: frozenset[str],
    original_invoice_creator_email: str = "",
    is_current_request_creator: bool = False,
) -> bool:
    """Does ``identity`` resolve to a set that includes the current actor?"""
    if identity.kind == "role":
        return actor_role == identity.value
    if identity.kind == "email":
        return actor_email.lower() == identity.value
    if identity.kind == "perm":
        if identity.value in {"creator", "initiator"}:
            return bool(actor_permissions.intersection({"creator", "initiator"}))
        return identity.value in actor_permissions
    if identity.kind == "token":
        if identity.value == "original_invoice_creator":
            return bool(
                original_invoice_creator_email
                and actor_email.lower() == original_invoice_creator_email.lower()
            )
        if identity.value == "creator":
            return is_current_request_creator
        if identity.value == "any_approver":
            # Reserved; resolver does not implement membership tracking yet.
            return False
    return False


@dataclass(frozen=True)
class StageEligibility:
    """Eligibility of one actor against one stage."""

    stage: int
    eligible: bool
    reason: str


@dataclass(frozen=True)
class ResolvedChain:
    """The fully-resolved chain for one (invoice, actor) pair."""

    surface: str
    policy_revision: str
    stages: tuple[StageEligibility, ...]
    current_stage: int  # 0 when the surface has no stages (laissez-faire)
    is_creator: bool


def resolve_chain(
    *,
    config: TAIConfig,
    surface: str,
    actor_email: str,
    actor_role: str,
    actor_extra_permissions: Iterable[str] = (),
    actor_excluded_permissions: Iterable[str] = (),
    original_invoice_creator_email: str = "",
    is_current_request_creator: bool = False,
    current_stage: int = 0,
) -> ResolvedChain:
    """Resolve the full eligibility chain for one (invoice, actor) pair.

    ``current_stage`` is the integer level the invoice is currently sitting on
    (1, 2, 3, ...). When 0, defaults to the first stage. When the surface has
    no stages (laissez-faire submission), ``stages`` is empty and
    ``current_stage`` is 0.
    """
    policy = policy_for_surface(config, surface)
    perms = effective_permissions(
        role=actor_role,
        extra_permissions=actor_extra_permissions,
        excluded_permissions=actor_excluded_permissions,
        role_permissions=config.role_permissions,
    )
    if not policy.stages:
        return ResolvedChain(
            surface=surface,
            policy_revision=config.policy_revision,
            stages=(),
            current_stage=0,
            is_creator=is_current_request_creator,
        )

    if current_stage < 1:
        current_stage = policy.stages[0].stage

    eligibilities: list[StageEligibility] = []
    for stage in policy.stages:
        if stage.stage != current_stage:
            eligibilities.append(
                StageEligibility(
                    stage=stage.stage,
                    eligible=False,
                    reason="not_current_stage",
                )
            )
            continue
        if is_current_request_creator and not stage.allow_creator_self_approval:
            eligibilities.append(
                StageEligibility(
                    stage=stage.stage,
                    eligible=False,
                    reason="creator_cannot_self_approve",
                )
            )
            continue
        matched = any(
            identity_matches_actor(
                approver,
                actor_email=actor_email,
                actor_role=actor_role,
                actor_permissions=perms,
                original_invoice_creator_email=original_invoice_creator_email,
                is_current_request_creator=is_current_request_creator,
            )
            for approver in stage.approvers
        )
        eligibilities.append(
            StageEligibility(
                stage=stage.stage,
                eligible=matched,
                reason=(
                    "tai_projected_current_actor_approval"
                    if matched
                    else "actor_not_in_stage_approvers"
                ),
            )
        )

    return ResolvedChain(
        surface=surface,
        policy_revision=config.policy_revision,
        stages=tuple(eligibilities),
        current_stage=current_stage,
        is_creator=is_current_request_creator,
    )


def is_creator_of_surface(
    *,
    config: TAIConfig,
    surface: str,
    actor_email: str,
    actor_role: str,
    actor_extra_permissions: Iterable[str] = (),
    actor_excluded_permissions: Iterable[str] = (),
    original_invoice_creator_email: str = "",
) -> bool:
    """Can this actor initiate a new request on this surface?"""
    policy = policy_for_surface(config, surface)
    perms = effective_permissions(
        role=actor_role,
        extra_permissions=actor_extra_permissions,
        excluded_permissions=actor_excluded_permissions,
        role_permissions=config.role_permissions,
    )
    return any(
        identity_matches_actor(
            creator,
            actor_email=actor_email,
            actor_role=actor_role,
            actor_permissions=perms,
            original_invoice_creator_email=original_invoice_creator_email,
            is_current_request_creator=False,
        )
        for creator in policy.creators
    )


def default_tai_config() -> TAIConfig:
    """Return the all-defaults TAI config (no tenant policy present)."""
    return TAIConfig(
        policy_revision="default-no-tai",
        submission=_DEFAULT_SUBMISSION,
        reversal=_DEFAULT_REVERSAL,
        role_permissions={},
    )


def _expand_permission_aliases(permissions: Iterable[str]) -> frozenset[str]:
    expanded: set[str] = set()
    for permission in permissions:
        normalized = str(permission or "").strip()
        if not normalized:
            continue
        expanded.add(normalized)
        if normalized == "creator":
            expanded.add("initiator")
        elif normalized == "initiator":
            expanded.add("creator")
    return frozenset(expanded)


# ---------------------------------------------------------------------------
# Integration layer — Scout-side projection helpers
# ---------------------------------------------------------------------------

def actor_policy_for_tai_row(
    *,
    tai_config: TAIConfig,
    current_user_projection: Mapping[str, Any] | None,
    action_id: str,
    tai_surface: str,
    current_stage: int,
    original_invoice_creator_email: str = "",
    is_current_request_creator: bool = False,
) -> dict[str, Any]:
    """Build an ``actor_policy`` shape from a loaded TAI config + current user.

    This is the bridge between the pure resolver and Reader's
    ``available_actions[*].actor_policy`` contract. Returns a dict ready to
    drop directly into the projection.

    When ``current_user_projection`` is empty (harness/anonymous case), the
    decision is the conservative harness default ``allowed`` with reason
    ``current_actor_unknown_harness_default``, so dev tools don't accidentally
    lock themselves out.

    Used by Scout's real-time projection path AND by the backend simulator
    when it emits TAI-aware approval rows. Both callers hold their own TAI
    config and current-user projection; this helper is stateless.
    """
    if not current_user_projection:
        return {
            "source": "tai",
            "decision": "allowed",
            "reason": "current_actor_unknown_harness_default",
            "policy_revision": tai_config.policy_revision,
            "action_id": str(action_id or ""),
        }

    identity = current_user_projection.get("identity")
    identity_map = identity if isinstance(identity, Mapping) else {}
    actor_email = str(
        identity_map.get("email")
        or identity_map.get("username")
        or current_user_projection.get("email")
        or ""
    ).strip()
    actor_role = str(current_user_projection.get("role") or "").strip()
    extras_raw = current_user_projection.get("permissions") or []
    if not isinstance(extras_raw, list):
        extras_raw = []
    actor_extra_permissions = tuple(str(p) for p in extras_raw if str(p or "").strip())
    exclusions_raw = current_user_projection.get("permission_exclusions") or []
    if not isinstance(exclusions_raw, list):
        exclusions_raw = []
    actor_excluded_permissions = tuple(
        str(p) for p in exclusions_raw if str(p or "").strip()
    )

    chain = resolve_chain(
        config=tai_config,
        surface=tai_surface,
        actor_email=actor_email,
        actor_role=actor_role,
        actor_extra_permissions=actor_extra_permissions,
        actor_excluded_permissions=actor_excluded_permissions,
        original_invoice_creator_email=original_invoice_creator_email,
        is_current_request_creator=is_current_request_creator,
        current_stage=current_stage,
    )

    current = next(
        (entry for entry in chain.stages if entry.stage == chain.current_stage),
        None,
    )
    if current is None:
        decision = "allowed"
        reason = "tai_no_stages_required"
    else:
        decision = "allowed" if current.eligible else "disabled"
        reason = current.reason

    return {
        "source": "tai",
        "decision": decision,
        "reason": reason,
        "policy_revision": chain.policy_revision,
        "action_id": str(action_id or ""),
        "actor_ref": str(identity_map.get("actor_id") or actor_email or ""),
        "display_role": actor_role,
        "current_stage": chain.current_stage,
    }


# Action IDs that carry an actor-decision in the approval surface. The
# legacy hardcoded simulator path projected these as always-allowed; the
# TAI-aware path replaces that with a real eligibility decision when the
# row carries the required metadata.
_APPROVAL_DECISION_ACTIONS = frozenset({"approve_invoice", "reject_invoice"})


def apply_tai_to_approval_rows(
    rows: list[dict[str, Any]],
    *,
    tai_config: TAIConfig,
    current_user_projection: Mapping[str, Any] | None,
) -> None:
    """Apply TAI-resolved ``actor_policy`` to a list of approval rows.

    Mutates ``rows`` in place. For each row that carries ``tai_surface`` +
    ``current_stage`` metadata, the ``approve_invoice`` / ``reject_invoice``
    actions get a TAI-resolved ``actor_policy``. Rows without that metadata
    are left alone (existing legacy projection paths keep working).

    Used by both Scout's real-time projection writer and the backend
    simulator. Same function, same behavior — TAI is one source of truth.
    """
    for row in rows:
        if not isinstance(row, dict):
            continue
        actions = row.get("available_actions")
        if not isinstance(actions, list):
            continue
        tai_surface = str(row.get("tai_surface") or "").strip()
        stage_raw = row.get("current_stage")
        try:
            current_stage = int(stage_raw) if stage_raw is not None else 0
        except (TypeError, ValueError):
            current_stage = 0
        if not (tai_surface and current_stage >= 1):
            continue
        creator_email = str(row.get("creator_email") or "").strip()
        is_creator = bool(row.get("current_user_is_creator", False))
        for action in actions:
            if not isinstance(action, dict):
                continue
            action_id = str(action.get("id") or action.get("action_id") or "").strip()
            if action_id not in _APPROVAL_DECISION_ACTIONS:
                continue
            # Don't clobber a pre-set actor_policy (caller may have a more
            # specific reason already).
            if "actor_policy" in action:
                continue
            action["actor_policy"] = actor_policy_for_tai_row(
                tai_config=tai_config,
                current_user_projection=current_user_projection,
                action_id=action_id,
                tai_surface=tai_surface,
                current_stage=current_stage,
                original_invoice_creator_email=creator_email,
                is_current_request_creator=is_creator,
            )
