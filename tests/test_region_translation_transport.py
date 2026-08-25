import base64
import json
import threading
import time

import pytest

from ai_drive.region_translation_transport import (
    OllamaRegionTranslationClient,
    OllamaRegionVisionClient,
    RegionTransportError,
)


class FakeResponse:
    def __init__(self, rows, status_code=200):
        self.rows = rows
        self.status_code = status_code
        self.is_redirect = False
        self.closed = False
        self.started = threading.Event()
        self.release = threading.Event()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")

    def iter_lines(self, decode_unicode=False):
        self.started.set()
        for row in self.rows:
            if row == "__BLOCK__":
                assert self.release.wait(1)
                continue
            yield row.encode("utf-8")

    def close(self):
        self.closed = True
        self.release.set()


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


def row(content, done=False):
    return json.dumps({"message": {"content": content}, "done": done})


def test_vision_client_aggregates_stream_and_sends_local_multimodal_payload():
    response = FakeResponse([row('{"found_text":'), row('true}'), row("", True)])
    session = FakeSession(response)
    client = OllamaRegionVisionClient(session=session)

    result = client.analyze("qwen2.5vl:7b", b"image", "read text")

    assert result == '{"found_text":true}'
    args, kwargs = session.calls[0]
    assert args[0] == "http://127.0.0.1:11434/api/chat"
    assert kwargs["stream"] is True
    payload = kwargs["json"]
    assert payload["format"] == "json"
    assert payload["messages"][0]["images"] == [base64.b64encode(b"image").decode()]
    assert payload["messages"][0]["content"] == "read text"


def test_translation_client_aggregates_stream_without_image():
    response = FakeResponse([row("你好"), row("，世界"), row("", True)])
    session = FakeSession(response)
    client = OllamaRegionTranslationClient(session=session)

    result = client.complete("translategemma:4b", "system", '{"source_text":"hi"}')

    assert result == "你好，世界"
    _args, kwargs = session.calls[0]
    assert kwargs["stream"] is True
    assert "images" not in kwargs["json"]["messages"][0]


@pytest.mark.parametrize("client", [OllamaRegionVisionClient, OllamaRegionTranslationClient])
def test_transport_rejects_non_loopback_endpoint(client):
    with pytest.raises(ValueError, match="local"):
        client("https://example.com")


def test_transport_rejects_malformed_stream_rows():
    response = FakeResponse(["not json"])
    client = OllamaRegionTranslationClient(session=FakeSession(response))

    with pytest.raises(RegionTransportError, match="JSON"):
        client.complete("translategemma:4b", "system", "user")


def test_cancel_closes_active_response_and_unblocks_request():
    response = FakeResponse([row("partial"), "__BLOCK__"])
    session = FakeSession(response)
    client = OllamaRegionTranslationClient(session=session)
    result = []
    errors = []

    def run():
        try:
            result.append(client.complete("translategemma:4b", "system", "user"))
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    assert response.started.wait(1)
    assert client.cancel() is True
    worker.join(1)

    assert not worker.is_alive()
    assert response.closed is True
    assert len(errors) == 1
    assert isinstance(errors[0], RegionTransportError)


def test_cancel_is_idempotent_when_no_request_is_active():
    client = OllamaRegionVisionClient()

    assert client.cancel() is True
    assert client.cancel() is True


def test_transport_can_be_reused_after_cancelled_request_finishes():
    first = FakeResponse([row("partial"), "__BLOCK__"])
    session = FakeSession(first)
    client = OllamaRegionTranslationClient(session=session)
    errors = []

    worker = threading.Thread(
        target=lambda: _capture_error(
            errors,
            lambda: client.complete("translategemma:4b", "system", "user"),
        )
    )
    worker.start()
    assert first.started.wait(1)
    client.cancel()
    worker.join(1)
    assert not worker.is_alive()

    session.response = FakeResponse([row("second"), row("", True)])
    assert client.complete("translategemma:4b", "system", "user") == "second"
    assert len(errors) == 1


def _capture_error(errors, operation):
    try:
        operation()
    except Exception as exc:
        errors.append(exc)
