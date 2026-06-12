"""
Unit tests for the lifecycle publisher seam (R-M2 / §B-EventLog).

Asserts:
    - LifecycleEvent.to_frame() lifts a non-empty trace_id to top-level and
      defaults raw_bytes_in_event=False (mirrors events SBS frame, events.py).
    - CoreLifecyclePublisher forwards the frame to Core's
      publish_lifecycle_event over the (HTTP) CoreClient seam — and a Core
      failure is swallowed (best-effort: must not fail the request).
    - The seam is swappable: any object implementing publish() is a valid sink
      (RecordingLifecyclePublisher records; a custom sink works too).
"""

import pytest

from src.services.lifecycle import (
    CoreLifecyclePublisher,
    FAMILY_FINALIZE_ACCEPTED,
    LifecycleEvent,
    LifecyclePublisher,
    RecordingLifecyclePublisher,
)


# ── Frame shape ──────────────────────────────────────────────────────────


def test_frame_lifts_trace_id_to_top_level():
    evt = LifecycleEvent(family=FAMILY_FINALIZE_ACCEPTED, trace_id="abc", data={"doc_ref": "D1"})
    frame = evt.to_frame()
    assert frame["trace_id"] == "abc"
    assert frame["data"]["trace_id"] == "abc"
    assert frame["family"] == FAMILY_FINALIZE_ACCEPTED
    assert frame["event"] == FAMILY_FINALIZE_ACCEPTED  # back-compat alias
    assert frame["source"] == "relay"
    assert frame["data"]["doc_ref"] == "D1"


def test_frame_defaults_raw_bytes_false():
    evt = LifecycleEvent(family=FAMILY_FINALIZE_ACCEPTED, trace_id="t", data={})
    assert evt.to_frame()["data"]["raw_bytes_in_event"] is False


def test_frame_omits_top_level_trace_id_when_empty():
    evt = LifecycleEvent(family=FAMILY_FINALIZE_ACCEPTED, trace_id="", data={})
    frame = evt.to_frame()
    assert "trace_id" not in frame  # not lifted when blank
    assert frame["data"]["trace_id"] == ""  # still present in data (empty)


# ── CoreLifecyclePublisher forwards to the CoreClient seam ───────────────


class _MockCore:
    def __init__(self):
        self.published = []

    async def publish_lifecycle_event(self, frame):
        self.published.append(frame)
        return {"accepted": True, "family": frame.get("family"), "trace_id": frame.get("trace_id")}


@pytest.mark.asyncio
async def test_core_publisher_forwards_frame():
    core = _MockCore()
    pub = CoreLifecyclePublisher(core)
    ok = await pub.publish(
        LifecycleEvent(family=FAMILY_FINALIZE_ACCEPTED, trace_id="trace-9", data={"ref": "R"})
    )
    assert ok is True
    assert len(core.published) == 1
    assert core.published[0]["trace_id"] == "trace-9"
    assert core.published[0]["family"] == FAMILY_FINALIZE_ACCEPTED


class _BrokenCore:
    async def publish_lifecycle_event(self, frame):
        raise RuntimeError("core down")


@pytest.mark.asyncio
async def test_core_publisher_swallows_failure():
    """A Core publish failure must NOT raise — best-effort sink."""
    pub = CoreLifecyclePublisher(_BrokenCore())
    ok = await pub.publish(LifecycleEvent(family=FAMILY_FINALIZE_ACCEPTED, trace_id="t"))
    assert ok is False  # swallowed, signalled via return value


# ── Swappability (the contract reason the seam exists) ───────────────────


@pytest.mark.asyncio
async def test_recording_publisher_is_a_valid_sink():
    rec = RecordingLifecyclePublisher()
    assert isinstance(rec, LifecyclePublisher)  # runtime_checkable Protocol
    await rec.publish(LifecycleEvent(family=FAMILY_FINALIZE_ACCEPTED, trace_id="t1"))
    await rec.publish(LifecycleEvent(family=FAMILY_FINALIZE_ACCEPTED, trace_id="t2"))
    assert [e.trace_id for e in rec.events] == ["t1", "t2"]
    assert rec.last().trace_id == "t2"


@pytest.mark.asyncio
async def test_custom_sink_satisfies_protocol():
    class CustomSink:
        def __init__(self):
            self.count = 0

        async def publish(self, event):
            self.count += 1
            return True

    sink = CustomSink()
    assert isinstance(sink, LifecyclePublisher)
    await sink.publish(LifecycleEvent(family=FAMILY_FINALIZE_ACCEPTED, trace_id="x"))
    assert sink.count == 1
