"""
Unit tests for FinalizeService — the #3 reference-only fiscalize (R-M2).

Direct service tests (no HTTP): idempotency keying, 409-on-duplicate via
AlreadyFinalizedError, and the missing-reference 400. Asserts the Core finalize
trigger is forwarded with the trace_id (the cross-seat NEEDS-CORE forward).

Q24 (ARCH tick56): Relay is ingress-only — Core emits the finalize.accepted
lifecycle event on its own stream. Relay no longer publishes lifecycle events,
so there is no publisher seam to assert here; the event_family is still echoed
on the result for client correlation.
"""

import pytest

from src.errors import AlreadyFinalizedError, FinalizeReferenceMissingError
from src.services.finalize import (
    FAMILY_FINALIZE_ACCEPTED,
    FinalizeService,
    _finalize_key,
    OPERATION_FINALIZE,
)


class _MockCore:
    """Records finalize_by_reference calls; returns a canned Core ack."""

    def __init__(self):
        self.finalize_calls = []

    async def finalize_by_reference(self, ref, trace_id="", metadata=None, jwt_token=None):
        self.finalize_calls.append(
            {"ref": ref, "trace_id": trace_id, "metadata": metadata, "jwt": jwt_token}
        )
        return {"ref": ref, "status": "finalized", "trace_id": trace_id, "event_id": "core-evt-1"}


@pytest.fixture
def core():
    return _MockCore()


@pytest.fixture
def service(core):
    return FinalizeService(core)


# ── Key derivation ───────────────────────────────────────────────────────


def test_finalize_key_prefers_trace_id():
    assert _finalize_key(ref="r", trace_id="t") == f"{OPERATION_FINALIZE}:t"


def test_finalize_key_falls_back_to_ref():
    assert _finalize_key(ref="r", trace_id="") == f"{OPERATION_FINALIZE}:r"


def test_finalize_key_empty_when_both_blank():
    assert _finalize_key(ref="", trace_id="") == ""


# ── Happy path ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_finalize_accepts_and_forwards_to_core(service, core):
    result = await service.finalize(ref="sha256:abc", trace_id="trace-1")
    assert result.status == "accepted"
    assert result.finalize_by_reference is True
    assert result.raw_bytes_sent is False
    assert result.ref == "sha256:abc"
    assert result.trace_id == "trace-1"
    assert result.event_family == FAMILY_FINALIZE_ACCEPTED
    assert result.idempotent_replay is False

    # Core finalize trigger forwarded WITH the trace_id (NEEDS-CORE forward).
    # Q24: the finalize.accepted lifecycle event is Core's to emit, not Relay's.
    assert len(core.finalize_calls) == 1
    assert core.finalize_calls[0]["trace_id"] == "trace-1"


@pytest.mark.asyncio
async def test_doc_ref_defaults_to_ref(service):
    result = await service.finalize(ref="sha256:xyz", trace_id="t2")
    assert result.doc_ref == "sha256:xyz"


@pytest.mark.asyncio
async def test_explicit_doc_ref_preserved(service):
    result = await service.finalize(ref="sha256:xyz", trace_id="t3", doc_ref="DOC-9")
    assert result.doc_ref == "DOC-9"


# ── Idempotency / 409 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_trace_id_raises_409(service, core):
    await service.finalize(ref="sha256:a", trace_id="dup")
    with pytest.raises(AlreadyFinalizedError) as ei:
        await service.finalize(ref="sha256:a", trace_id="dup")
    assert ei.value.status_code == 409
    assert ei.value.error_code == "ALREADY_FINALIZED"
    assert ei.value.trace_id == "dup"
    # No second Core trigger (the duplicate is short-circuited before forward).
    assert len(core.finalize_calls) == 1


@pytest.mark.asyncio
async def test_duplicate_carries_original_event_id(service):
    first = await service.finalize(ref="sha256:b", trace_id="dup2")
    with pytest.raises(AlreadyFinalizedError) as ei:
        await service.finalize(ref="sha256:b", trace_id="dup2")
    assert ei.value.original_event_id == first.event_id


@pytest.mark.asyncio
async def test_same_trace_dedups_across_changed_ref(service):
    """The trace_id is the dedup anchor across the #2↔#3 switch — even if the
    ref differs, the same trace_id is already-finalized (§3.3)."""
    await service.finalize(ref="sha256:first", trace_id="stable-trace")
    with pytest.raises(AlreadyFinalizedError):
        await service.finalize(ref="sha256:edited", trace_id="stable-trace")


@pytest.mark.asyncio
async def test_distinct_trace_ids_both_accepted(service, core):
    r1 = await service.finalize(ref="sha256:p", trace_id="trace-A")
    r2 = await service.finalize(ref="sha256:q", trace_id="trace-B")
    assert r1.status == "accepted" and r2.status == "accepted"
    assert len(core.finalize_calls) == 2


# ── Validation ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_reference_raises_400(service, core):
    with pytest.raises(FinalizeReferenceMissingError) as ei:
        await service.finalize(ref="", trace_id="")
    assert ei.value.status_code == 400
    # Rejected before any Core forward.
    assert core.finalize_calls == []


# ── Resilience: Core down must not strand the finalize ───────────────────


class _BrokenCore:
    async def finalize_by_reference(self, ref, trace_id="", metadata=None, jwt_token=None):
        raise RuntimeError("core unreachable")


@pytest.mark.asyncio
async def test_core_failure_is_non_fatal():
    svc = FinalizeService(_BrokenCore())
    result = await svc.finalize(ref="sha256:resilient", trace_id="t-res")
    # Still accepted even though the Core forward raised (doc already ingested).
    assert result.status == "accepted"
    assert result.trace_id == "t-res"
