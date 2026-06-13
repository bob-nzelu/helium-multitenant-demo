"""
Unit tests for FinalizeService — the #3 reference-only fiscalize (R-M2).

Direct service tests (no HTTP): idempotency keying, 409-on-duplicate via
AlreadyFinalizedError, the relay.finalize.accepted lifecycle emit through a
mock Core sink, and the missing-reference 400. Asserts the Core finalize
trigger is forwarded with the trace_id (the cross-seat NEEDS-CORE forward).
"""

import pytest

from src.errors import AlreadyFinalizedError, FinalizeReferenceMissingError
from src.services.finalize import FinalizeService, _finalize_key, OPERATION_FINALIZE
from src.services.lifecycle import (
    FAMILY_FINALIZE_ACCEPTED,
    RecordingLifecyclePublisher,
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
def publisher():
    return RecordingLifecyclePublisher()


@pytest.fixture
def service(core, publisher):
    return FinalizeService(core, publisher)


# ── Key derivation ───────────────────────────────────────────────────────


def test_finalize_key_prefers_trace_id():
    assert _finalize_key(ref="r", trace_id="t") == f"{OPERATION_FINALIZE}:t"


def test_finalize_key_falls_back_to_ref():
    assert _finalize_key(ref="r", trace_id="") == f"{OPERATION_FINALIZE}:r"


def test_finalize_key_empty_when_both_blank():
    assert _finalize_key(ref="", trace_id="") == ""


# ── Happy path ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_finalize_accepts_and_emits_event(service, core, publisher):
    result = await service.finalize(ref="sha256:abc", trace_id="trace-1")
    assert result.status == "accepted"
    assert result.finalize_by_reference is True
    assert result.raw_bytes_sent is False
    assert result.ref == "sha256:abc"
    assert result.trace_id == "trace-1"
    assert result.event_family == FAMILY_FINALIZE_ACCEPTED
    assert result.idempotent_replay is False

    # Core finalize trigger forwarded WITH the trace_id (NEEDS-CORE forward).
    assert len(core.finalize_calls) == 1
    assert core.finalize_calls[0]["trace_id"] == "trace-1"

    # relay.finalize.accepted emitted via the seam, trace_id echoed.
    assert len(publisher.events) == 1
    evt = publisher.last()
    assert evt.family == FAMILY_FINALIZE_ACCEPTED
    assert evt.trace_id == "trace-1"
    assert publisher.frames[-1]["trace_id"] == "trace-1"


@pytest.mark.asyncio
async def test_doc_ref_defaults_to_ref(service):
    result = await service.finalize(ref="sha256:xyz", trace_id="t2")
    assert result.doc_ref == "sha256:xyz"


@pytest.mark.asyncio
async def test_explicit_doc_ref_preserved(service, publisher):
    result = await service.finalize(ref="sha256:xyz", trace_id="t3", doc_ref="DOC-9")
    assert result.doc_ref == "DOC-9"
    assert publisher.frames[-1]["data"]["doc_ref"] == "DOC-9"


# ── Idempotency / 409 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_trace_id_raises_409(service, core, publisher):
    await service.finalize(ref="sha256:a", trace_id="dup")
    with pytest.raises(AlreadyFinalizedError) as ei:
        await service.finalize(ref="sha256:a", trace_id="dup")
    assert ei.value.status_code == 409
    assert ei.value.error_code == "ALREADY_FINALIZED"
    assert ei.value.trace_id == "dup"
    # No second Core trigger, no second event.
    assert len(core.finalize_calls) == 1
    assert len(publisher.events) == 1


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
async def test_distinct_trace_ids_both_accepted(service, publisher):
    r1 = await service.finalize(ref="sha256:p", trace_id="trace-A")
    r2 = await service.finalize(ref="sha256:q", trace_id="trace-B")
    assert r1.status == "accepted" and r2.status == "accepted"
    assert len(publisher.events) == 2


# ── Validation ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_reference_raises_400(service, publisher):
    with pytest.raises(FinalizeReferenceMissingError) as ei:
        await service.finalize(ref="", trace_id="")
    assert ei.value.status_code == 400
    assert publisher.events == []


# ── Resilience: Core down must not strand the finalize ───────────────────


class _BrokenCore:
    async def finalize_by_reference(self, ref, trace_id="", metadata=None, jwt_token=None):
        raise RuntimeError("core unreachable")


@pytest.mark.asyncio
async def test_core_failure_is_non_fatal(publisher):
    svc = FinalizeService(_BrokenCore(), publisher)
    result = await svc.finalize(ref="sha256:resilient", trace_id="t-res")
    # Still accepted + still emits the lifecycle event (doc already ingested).
    assert result.status == "accepted"
    assert len(publisher.events) == 1
    assert publisher.last().trace_id == "t-res"
