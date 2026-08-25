import base64
import json
import threading
import time
from io import BytesIO

import pytest
from PIL import Image

from ai_drive.region_translation_transport import (
    build_region_translation_clients,
    OllamaRegionTranslationClient,
    OllamaRegionVisionClient,
    RegionTransportError,
    RegionTransportQueueTimeout,
    MAX_VISION_LONG_EDGE,
)
from cohelper_core import Config
from ai_drive.model_scheduler import ModelScheduler


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


class BlockingPostSession(FakeSession):
    def __init__(self, response):
        super().__init__(response)
        self.started = threading.Event()
        self.release = threading.Event()

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        self.started.set()
        assert self.release.wait(1)
        return self.response


class SlowFirstCloseResponse(FakeResponse):
    def __init__(self, rows):
        super().__init__(rows)
        self.close_started = threading.Event()
        self.allow_first_close = threading.Event()
        self._close_count = 0

    def close(self):
        self._close_count += 1
        self.closed = True
        self.release.set()
        if threading.current_thread().name == "region-translation-cancel":
            self.close_started.set()
            assert self.allow_first_close.wait(2)


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


def test_configured_region_endpoints_and_timeouts_build_the_clients():
    config = Config(
        {
            "region_translation": {
                "ocr_base_url": "http://localhost:11435",
                "translation_base_url": "https://127.0.0.1:11436",
            }
        }
    )
    vision, translation = build_region_translation_clients(config, scheduler=ModelScheduler())

    assert vision.endpoint == "http://localhost:11435"
    assert translation.endpoint == "https://127.0.0.1:11436"
    assert vision._timeout == 60
    assert translation._timeout == 60


def test_vision_payload_downscales_oversized_image_before_base64_encoding():
    output = BytesIO()
    Image.new("RGB", (MAX_VISION_LONG_EDGE + 1000, 100), "white").save(output, format="PNG")
    response = FakeResponse([row("{}", True)])
    session = FakeSession(response)
    OllamaRegionVisionClient(session=session).analyze("qwen2.5vl:7b", output.getvalue(), "read text")

    encoded = session.calls[0][1]["json"]["messages"][0]["images"][0]
    with Image.open(BytesIO(base64.b64decode(encoded))) as bounded:
        assert max(bounded.size) <= MAX_VISION_LONG_EDGE


@pytest.mark.parametrize("client", [OllamaRegionVisionClient, OllamaRegionTranslationClient])
@pytest.mark.parametrize("endpoint", ["https://example.com", "ftp://127.0.0.1:11434"])
def test_transport_rejects_non_loopback_endpoint(client, endpoint):
    with pytest.raises(ValueError, match="local"):
        client(endpoint)


def test_transport_rejects_malformed_stream_rows():
    response = FakeResponse(["not json"])
    client = OllamaRegionTranslationClient(session=FakeSession(response))

    with pytest.raises(RegionTransportError, match="JSON"):
        client.complete("translategemma:4b", "system", "user")


def test_transport_rejects_stream_without_done_marker():
    response = FakeResponse([row("partial")])
    client = OllamaRegionTranslationClient(session=FakeSession(response))

    with pytest.raises(RegionTransportError, match="done"):
        client.complete("translategemma:4b", "system", "user")


def test_transport_classifies_shared_model_queue_timeout():
    scheduler = ModelScheduler()
    held = scheduler.acquire("http://127.0.0.1:11434", "translategemma:4b")
    client = OllamaRegionTranslationClient(
        scheduler=scheduler,
        queue_timeout=0.02,
        session=FakeSession(FakeResponse([])),
    )

    with pytest.raises(RegionTransportQueueTimeout):
        client.complete("translategemma:4b", "system", "user")
    held.release()


def test_transport_rejects_ollama_error_rows():
    response = FakeResponse([json.dumps({"error": "model missing", "done": True})])
    client = OllamaRegionTranslationClient(session=FakeSession(response))

    with pytest.raises(RegionTransportError, match="error"):
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
    assert client.cancel() is False
    worker.join(1)

    assert not worker.is_alive()
    assert response.closed is True
    assert len(errors) == 1
    assert isinstance(errors[0], RegionTransportError)


def test_cancel_reports_stopping_while_session_post_is_blocked():
    response = FakeResponse([row("partial"), row("", True)])
    session = BlockingPostSession(response)
    client = OllamaRegionTranslationClient(session=session)
    errors = []

    worker = threading.Thread(
        target=lambda: _capture_error(
            errors,
            lambda: client.complete("translategemma:4b", "system", "user"),
        )
    )
    worker.start()
    assert session.started.wait(1)
    assert client.cancel() is False
    session.release.set()
    worker.join(1)

    assert not worker.is_alive()
    assert len(errors) == 1


def test_cancel_event_closes_active_response_without_direct_client_cancel():
    response = FakeResponse([row("partial"), "__BLOCK__"])
    session = FakeSession(response)
    client = OllamaRegionTranslationClient(session=session)
    cancel = threading.Event()
    errors = []

    worker = threading.Thread(
        target=lambda: _capture_error(
            errors,
            lambda: client.complete("translategemma:4b", "system", "user", cancel),
        )
    )
    worker.start()
    assert response.started.wait(1)
    cancel.set()
    worker.join(2)

    assert not worker.is_alive()
    assert response.closed is True
    assert len(errors) == 1


def test_delayed_old_cancel_cannot_cancel_a_reused_request():
    first = SlowFirstCloseResponse([row("partial"), "__BLOCK__"])
    session = FakeSession(first)
    client = OllamaRegionTranslationClient(session=session)
    cancel = threading.Event()
    errors = []
    worker = threading.Thread(
        target=lambda: _capture_error(
            errors,
            lambda: client.complete("translategemma:4b", "system", "user", cancel),
        )
    )
    worker.start()
    assert first.started.wait(1)
    cancel.set()
    assert first.close_started.wait(1)
    worker.join(2)
    assert not worker.is_alive()

    second = FakeResponse([row("second"), row("", True)])
    session.response = second
    assert client.complete("translategemma:4b", "system", "user") == "second"
    first.allow_first_close.set()
    assert errors


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
    assert client.cancel() is False
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
