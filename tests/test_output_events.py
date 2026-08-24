import json

import pytest

from ai_drive.output import OutputEvent, OutputEventError, OutputKind, OutputSeverity, OutputSource


def test_output_event_round_trips_through_the_versioned_json_contract():
    event = OutputEvent(
        event_id="event-1",
        kind=OutputKind.DETECTION,
        source=OutputSource.AUTOMATION,
        occurred_at=123.5,
        title="检测到目标",
        message="刷新按钮",
        severity=OutputSeverity.WARNING,
        generation=7,
        metadata={"rule_id": "accept", "confidence": 0.96},
    )

    encoded = event.to_json()

    assert json.loads(encoded) == {
        "schema_version": 1,
        "event_id": "event-1",
        "kind": "detection",
        "source": "automation",
        "occurred_at": 123.5,
        "title": "检测到目标",
        "message": "刷新按钮",
        "severity": "warning",
        "generation": 7,
        "metadata": {"rule_id": "accept", "confidence": 0.96},
    }
    assert OutputEvent.from_json(encoded) == event


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda payload: payload.update(schema_version=2), "schema_version"),
        (lambda payload: payload.update(schema_version=True), "schema_version"),
        (lambda payload: payload.update(schema_version=1.0), "schema_version"),
        (lambda payload: payload.update(extra="not allowed"), "fields"),
        (lambda payload: payload.update(event_id=""), "event_id"),
        (lambda payload: payload.update(generation=-1), "generation"),
        (lambda payload: payload.update(message="x" * 16_385), "message"),
    ],
)
def test_output_event_rejects_invalid_or_unbounded_wire_data(mutation, message):
    payload = {
        "schema_version": 1,
        "event_id": "event-1",
        "kind": "detection",
        "source": "automation",
        "occurred_at": 123.5,
        "title": "检测到目标",
        "message": "刷新按钮",
        "severity": "warning",
        "generation": None,
        "metadata": {},
    }
    mutation(payload)

    with pytest.raises(OutputEventError, match=message):
        OutputEvent.from_json(json.dumps(payload))


@pytest.mark.parametrize(
    "field, value",
    [
        ("kind", "detection"),
        ("source", "automation"),
        ("severity", "warning"),
    ],
)
def test_direct_event_construction_requires_typed_enum_values(field, value):
    values = {
        "event_id": "event-1",
        "kind": OutputKind.DETECTION,
        "source": OutputSource.AUTOMATION,
        "occurred_at": 123.5,
        "title": "检测到目标",
        "message": "刷新按钮",
        "severity": OutputSeverity.WARNING,
        "generation": None,
        "metadata": {},
    }
    values[field] = value

    with pytest.raises(OutputEventError, match=field):
        OutputEvent(**values)


def test_streaming_answer_events_require_a_generation_at_the_protocol_boundary():
    payload = {
        "schema_version": 1,
        "event_id": "answer-1",
        "kind": "answer_delta",
        "source": "knowledge",
        "occurred_at": 123.5,
        "title": "知识回答",
        "message": "内容",
        "severity": "info",
        "generation": None,
        "metadata": {},
    }

    with pytest.raises(OutputEventError, match="generation"):
        OutputEvent.from_json(json.dumps(payload))


@pytest.mark.parametrize("kind", ["emergency_stop", "emergency_cleared"])
def test_emergency_events_require_a_latch_revision_at_the_protocol_boundary(kind):
    payload = {
        "schema_version": 1,
        "event_id": f"{kind}-1",
        "kind": kind,
        "source": "automation",
        "occurred_at": 123.5,
        "title": "安全状态",
        "message": "状态已变化",
        "severity": "critical",
        "generation": None,
        "metadata": {},
    }

    with pytest.raises(OutputEventError, match="generation"):
        OutputEvent.from_json(json.dumps(payload))


def test_event_rejects_unencodable_unicode_before_transport_acknowledgement():
    payload = {
        "schema_version": 1,
        "event_id": "bad-unicode-1",
        "kind": "status",
        "source": "system",
        "occurred_at": 123.5,
        "title": "状态",
        "message": "\ud800",
        "severity": "error",
        "generation": None,
        "metadata": {},
    }

    with pytest.raises(OutputEventError, match="Unicode"):
        OutputEvent.from_json(json.dumps(payload))


def test_mutated_metadata_still_fails_as_an_output_event_error():
    event = OutputEvent(
        event_id="metadata-1",
        kind=OutputKind.STATUS,
        source=OutputSource.SYSTEM,
        occurred_at=123.5,
        title="状态",
        message="正常",
        severity=OutputSeverity.INFO,
        generation=None,
        metadata={},
    )
    event.metadata["bad"] = "\ud800"

    with pytest.raises(OutputEventError, match="Unicode"):
        event.to_json()
